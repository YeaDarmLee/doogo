# 2-space indent
import os
from datetime import datetime
from sqlalchemy import text, select
from sqlalchemy.exc import SQLAlchemyError
from application.src.models import db
from application.src.models.SupplierList import SupplierList
from application.src.service.slackService import create_slack_channel_only

# (선택) SlackApiError 상세 로깅
try:
  from slack_sdk.errors import SlackApiError
except Exception:
  SlackApiError = Exception

def _acquire_lock(lock_key: str) -> bool:
  got = db.session.execute(text("SELECT GET_LOCK(:k, 0)"), {"k": lock_key}).scalar()
  return bool(got)

def _release_lock(lock_key: str):
  try:
    db.session.execute(text("SELECT RELEASE_LOCK(:k)"), {"k": lock_key})
  except Exception:
    pass

def process_pending_suppliers(batch_size: int = 10, lock_key: str = "job_supplier_slack"):
  if not _acquire_lock(lock_key):
    return

  try:
    pending = (
      db.session.execute(
        select(SupplierList)
        .where(SupplierList.stateCode.is_(None))
        .order_by(SupplierList.seq.asc())
        .limit(batch_size)
      ).scalars().all()
    )
    if not pending:
      return

    for s in pending:
      s.stateCode = 'P'
    db.session.commit()

    for s in pending:
      try:
        res = create_slack_channel_only(s)
        channel_id = (res or {}).get("channel_id")
        if not channel_id:
          s.stateCode = 'E'
          db.session.commit()
          print(f"[{datetime.now()}] Slack 처리 실패(채널ID 없음) seq={s.seq} company={s.companyName} res={res}")
          continue

        s.channelId = channel_id
        s.stateCode = 'A'
        db.session.commit()
        print(f"[{datetime.now()}] Slack 처리 성공 seq={s.seq} company={s.companyName} channel_id={channel_id}")

        _after_slack_success(s)

      except SlackApiError as e:
        resp = getattr(e, "response", None)
        status = getattr(resp, "status_code", None)
        data = getattr(resp, "data", None)
        err_code = (data.get("error") if isinstance(data, Dict) else None)
        s.stateCode = 'E'
        db.session.commit()
        print(
          f"[{datetime.now()}] Slack 처리 실패(SlackApiError) "
          f"seq={s.seq} company={s.companyName} status={status} error={err_code} data={data}"
        )
      except Exception as e:
        s.stateCode = 'E'
        db.session.commit()
        print(f"[{datetime.now()}] Slack 처리 실패 seq={s.seq} company={s.companyName} err={e}")

  except SQLAlchemyError as e:
    db.session.rollback()
    print("DB error during job:", e)
  except Exception as e:
    db.session.rollback()
    print("Unexpected error during job:", e)
  finally:
    _release_lock(lock_key)

def _after_slack_success(supplier: SupplierList):
  """
  슬랙 성공 후 자동 계약서 발송.
  contractStatus 흐름:
    - P: 전송 준비 (토큰 발급/전송 시도 직전)
    - A: 전송 성공(문서 생성 성공)
    - E: 실패(이메일 없음/토큰·문서 오류 등)
  """
  try:
    from application.src.service.eformsign_service import EformsignService, EformsignError
  except Exception as e:
    supplier.contractStatus = 'E'
    try:
      db.session.commit()
    except Exception:
      db.session.rollback()
    print(f"[{datetime.now()}] eformsign 모듈 임포트 실패 seq={supplier.seq} err={e}")
    return

  # 1) 전송대기(P)로 세팅
  try:
    supplier.contractStatus = 'P'
    db.session.commit()
  except Exception:
    db.session.rollback()
    print(f"[{datetime.now()}] 계약 상태(P) 저장 실패 seq={supplier.seq}")

  try:
    svc = EformsignService()
    tr = svc.issue_access_token()
    print(
      f"[{datetime.now()}] eformsign 토큰 발급 성공 "
      f"seq={supplier.seq} company={supplier.companyName} api_url={tr.api_url} expires_in={tr.expires_in}"
    )

    recipient_email = (supplier.email or "").strip()
    recipient_name = (supplier.companyName or "공급사 담당자").strip()
    if not recipient_email:
      supplier.contractStatus = 'E'
      try:
        db.session.commit()
      except Exception:
        db.session.rollback()
      print(f"[{datetime.now()}] eformsign 전송 스킵(이메일 없음) seq={supplier.seq}")
      return tr

    doc = svc.create_document_from_template(
      token=tr,
      template_id=os.getenv("EFORMSIGN_TEMPLATE_ID"),
      recipient_name=recipient_name,
      recipient_email=recipient_email,
      fields=[],
    )

    doc_id = doc.get("document_id") or None
    print(
      f"[{datetime.now()}] eformsign 문서 전송 성공 seq={supplier.seq} company={supplier.companyName} "
      f"recipient={recipient_email} document_id={doc_id or '(unknown)'}"
    )

    # 2) 전송 성공(A) + 계약서 ID 저장
    supplier.contractStatus = 'A'
    if doc_id:                     # 문서 ID가 파싱되면 저장
      supplier.contractId = doc_id
    try:
      db.session.commit()
    except Exception:
      db.session.rollback()
      print(f"[{datetime.now()}] 계약 상태/ID 저장 실패 seq={supplier.seq}")

    return tr

  except EformsignError as e:
    status = getattr(e, "status", None)
    payload = getattr(e, "payload", {})
    print(
      f"[{datetime.now()}] eformsign 처리 실패 "
      f"(토큰/문서) seq={supplier.seq} company={supplier.companyName} status={status} payload={payload}"
    )
    supplier.contractStatus = 'E'
    try:
      db.session.commit()
    except Exception:
      db.session.rollback()

  except Exception as e:
    print(f"[{datetime.now()}] eformsign 처리 실패(예상치 못한 오류) seq={supplier.seq} err={e}")
    supplier.contractStatus = 'E'
    try:
      db.session.commit()
    except Exception:
      db.session.rollback()
