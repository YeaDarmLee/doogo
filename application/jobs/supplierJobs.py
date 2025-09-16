# application/jobs/supplierJobs.py
# -*- coding: utf-8 -*-
import os
from datetime import datetime
from typing import Dict, Optional
from sqlalchemy import text, select, and_, or_
from sqlalchemy.exc import SQLAlchemyError

from application.src.models import db
from application.src.models.SupplierList import SupplierList
from application.src.service.slackService import (
  create_slack_channel_only,
  send_workspace_join_invite_email,
  notify_invite_mail_sent,
  notify_user_invited_to_channel,
  notify_contract_sent,
  notify_contract_failed,
  post_message_to_channel
)

# Slack SDK
try:
  from slack_sdk import WebClient
  from slack_sdk.errors import SlackApiError
except Exception:
  WebClient = None
  class SlackApiError(Exception): ...
  pass

SLACK_BROADCAST_CHANNEL_ID = os.getenv("SLACK_BROADCAST_CHANNEL_ID", "").strip()
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "").strip()
_slack_client = WebClient(token=SLACK_BOT_TOKEN) if (WebClient and SLACK_BOT_TOKEN) else None

# stateCode semantics:
#   None : 미처리
#   R    : 승인 대기중
#   P    : 작업중(락)
#   A    : 준비 완료(채널 생성 성공 등)
#   E    : 오류
#   I    : 초대 메일 발송됨(가입 대기 상태)

def _acquire_lock(lock_key: str) -> bool:
  got = db.session.execute(text("SELECT GET_LOCK(:k, 0)"), {"k": lock_key}).scalar()
  return bool(got)

def _release_lock(lock_key: str):
  try:
    db.session.execute(text("SELECT RELEASE_LOCK(:k)"), {"k": lock_key})
  except Exception:
    pass

def _lookup_user_id_by_email(email: str) -> Optional[str]:
  if not (_slack_client and email):
    return None
  try:
    resp = _slack_client.users_lookupByEmail(email=email)
    user = resp.get("user") or {}
    return user.get("id")
  except SlackApiError as e:
    data = getattr(e, "response", None)
    data = getattr(data, "data", None) if data else None
    err = (data or {}).get("error")
    if err in ("users_not_found", "account_inactive"):
      return None
    print(f"[{datetime.now()}] lookupByEmail error email={email} err={err} data={data}")
    return None
  except Exception as e:
    print(f"[{datetime.now()}] lookupByEmail exception email={email} err={e}")
    return None

def process_pending_suppliers(batch_size: int = 10, lock_key: str = "job_supplier_slack"):
  """
  대상:
    - None && (채널 생성 필요 or 계약 필요)
    - I(가입 대기) : 가입 확인 재시도만 수행 (이때 lookup 실패면 '메일 재발송 금지')
  흐름:
    1) 대상 조회 → prev_state 맵 확보 → 모두 P로 마킹
    2) 각 항목 처리
       - need_slack → 채널 생성
       - lookupByEmail
         * uid 없고 prev_state == 'I' → 메일 보내지 않고 I로 되돌림(종료)
         * uid 없고 prev_state != 'I' → 메일 1회 발송 후 I로 전환(종료)
         * uid 있으면 → 채널 초대
       - need_contract → 계약 진행
       - 마무리 상태 정리
  """
  if not _acquire_lock(lock_key):
    return

  try:
    base_target = or_(
      SupplierList.stateCode.is_(None),  # STATE_CODE IS NULL
      and_(                              # STATE_CODE='RA' AND (ID/PW 존재)
        SupplierList.stateCode == "RA",
        SupplierList.stateCode.isnot(None),
        SupplierList.supplierID.isnot(None),
        SupplierList.supplierPW.isnot(None),
      ),
      SupplierList.stateCode == "I"      # STATE_CODE = 'I'
    )

    rows = (
      db.session.execute(
        select(SupplierList)
        .where(base_target)
        .order_by(SupplierList.seq.asc())
        .limit(batch_size)
      ).scalars().all()
    )

    if not rows:
      return

    # 1) prev_state 기억 후 모두 P로 마킹
    prev_state_map: Dict[int, Optional[str]] = {r.seq: r.stateCode for r in rows}
    for r in rows:
      if r.stateCode != 'I':
        r.stateCode = 'P'
    db.session.commit()

    # 2) 처리 루프
    for s in rows:
      prev_state = prev_state_map.get(s.seq)
      try:
        email = (s.email or "").strip()

        need_slack = (not s.channelId) and bool(s.supplierCode) and bool(s.companyName) and bool(email)
        need_contract = bool(s.channelId) and ((s.contractStatus is None) or (s.contractStatus == 'E')) and bool(email)

        # 2-1) 채널 생성
        if need_slack:
          res = create_slack_channel_only(s)
          channel_id = (res or {}).get("channel_id")
          name = (res or {}).get("name")
          reused = (res or {}).get("reused")
          renamed = (res or {}).get("renamed")

          if not channel_id:
            s.stateCode = 'E'
            db.session.commit()
            print(f"[{datetime.now()}] Slack 처리 실패(채널ID 없음) seq={s.seq} company={s.companyName} res={res}")
            continue

          s.channelId = channel_id
          s.stateCode = 'A'
          db.session.commit()
          print(f"[{datetime.now()}] Slack 처리 성공 seq={s.seq} name={name} reused={reused} renamed={renamed} channel_id={channel_id}")
          need_contract = True

        # 2-2) 가입 확인
        if email:
          uid = _lookup_user_id_by_email(email)

          # (A) 미가입 & 이전 상태가 'I' 였으면 → 메일 재발송 금지, 그대로 I로 되돌리고 종료
          if not uid and prev_state == 'I':
            s.stateCode = 'I'
            db.session.commit()
            print(f"[{datetime.now()}] 가입 대기 유지(seq={s.seq}) email={email} (재발송 금지)")
            continue

          # (B) 미가입 & 이전 상태가 'I' 아니면(최초) → 메일 1회 발송 후 I로 전환
          if not uid and prev_state != 'I':
            sent = send_workspace_join_invite_email(email, s.companyName)
            if sent:
              notify_invite_mail_sent(email=email, supplier_name=s.companyName, supplier_channel_id=s.channelId)
            note = "발송됨" if sent else "발송실패/스킵"
            s.stateCode = 'I'
            db.session.commit()
            print(f"[{datetime.now()}] 가입 유도 메일 {note} seq={s.seq} email={email} → state='I'")
            continue

          # (C) 가입됨 → 채널 초대
          if uid and s.channelId and _slack_client:
            invited_ok = False
            try:
              _slack_client.conversations_invite(channel=s.channelId, users=uid)
              print(f"[{datetime.now()}] 채널 초대 성공 ch={s.channelId} email={email} uid={uid}")
              s.stateCode = 'A'
              db.session.commit()
              invited_ok = True
            except SlackApiError as e:
              data = getattr(e, "response", None)
              data = getattr(data, "data", None) if data else None
              err = (data or {}).get("error")
              if err == "already_in_channel":
                print(f"[{datetime.now()}] 이미 채널 멤버 ch={s.channelId} email={email}")
                invited_ok = True  # 이미 멤버라도 OK 취급
              else:
                print(f"[{datetime.now()}] 채널 초대 실패 ch={s.channelId} email={email} err={err} data={data}")
            except Exception as e:
              print(f"[{datetime.now()}] 채널 초대 예외 ch={s.channelId} email={email} err={e}")

            # ✅ 초대 결과 알림 (성공이든 '이미 멤버'든 안내)
            if invited_ok:
              notify_user_invited_to_channel(
                email=email,
                user_id=uid,
                supplier_name=s.companyName,
                supplier_channel_id=s.channelId
              )

        # 2-3) 계약서 전송
        if need_contract:
          _after_slack_success(s)

        # 2-4) 마무리 상태 정리
        #   - 여기까지 내려왔다는 건 I로 보류된 케이스가 아님
        #   - 채널/계약 준비 OK면 A 유지, 처리 중(P)이면 A로
        if s.stateCode == 'P':
          s.stateCode = 'A'
          db.session.commit()

      except SlackApiError as e:
        resp = getattr(e, "response", None)
        status = getattr(resp, "status_code", None)
        data = getattr(resp, "data", None)
        err_code = (data.get("error") if isinstance(data, Dict) else None)
        s.stateCode = 'E'
        try:
          db.session.commit()
        except Exception:
          db.session.rollback()
        print(
          f"[{datetime.now()}] Slack/계약 처리 실패(SlackApiError) "
          f"seq={s.seq} company={s.companyName} status={status} error={err_code} data={data}"
        )
      except Exception as e:
        s.stateCode = 'E'
        try:
          db.session.commit()
        except Exception:
          db.session.rollback()
        print(f"[{datetime.now()}] Slack/계약 처리 실패 seq={s.seq} company={s.companyName} err={e}")

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
  Slack 생성 이후 전자서명 발송:
  - contractSkip=1 이면 스킵(S) 처리
  - contractTemplate='A' → 단일 수수료(%)
  - contractTemplate='B' → 임계금액 + 이하/초과 수수료(%)
  - 이메일 없으면 오류(E)
  - 토큰/발송 성공 시 대기(P) → 완료(A)
  """
  # 0) 전처리: eformsign 모듈 체크
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

  # 1) 스킵 플래그(외부 제출) → 발송 생략
  if getattr(supplier, "contractSkip", False):
    supplier.contractStatus = 'S'  # Skipped(외부제출)
    try:
      db.session.commit()
    except Exception:
      db.session.rollback()
    print(f"[{datetime.now()}] eformsign 전송 스킵(외부제출) seq={supplier.seq}")
    # 알림: 스킵 통지(원하면 주석 해제)
    text = (
      f":package: *사전 계약 완료 공급사* / 공급사: {supplier.companyName}"
    )
    post_message_to_channel(channel_id=supplier.channelId,text=text)
    post_message_to_channel(channel_id=SLACK_BROADCAST_CHANNEL_ID,text=text)
    
    success_text = (
      f":tada: `{supplier.companyName}` 공급사 지원 채널이 생성되었습니다.\n"
      f"관리자 링크 바로가기: https://eclogin.cafe24.com/Shop/?url=Init&login_mode=3\n"
      f"아이디: `onedayboxb2b` / 공급사 ID: `{supplier.supplierID}` / PW: `{supplier.supplierPW}`\n"
      f"첫 로그인 할 때 비밀번호 재설정이 나오니 로그인 후 원하시는 비밀번호로 셋팅해주시면 됩니다.:smile:\n"
      f":round_pushpin: 운영 관련 공지와 사용 가이드는 <#C09DBG0UYCS> 및 <#C09EAJ46Z5J> 채널을 꼭 참고해주세요."
    )
    post_message_to_channel(channel_id=supplier.channelId,text=success_text)
    return

  # 2) 수신 이메일 검사
  recipient_email = (supplier.email or "").strip()
  if not recipient_email:
    supplier.contractStatus = 'E'
    try:
      db.session.commit()
    except Exception:
      db.session.rollback()
    print(f"[{datetime.now()}] eformsign 전송 스킵(이메일 없음) seq={supplier.seq}")
    notify_contract_failed(
      recipient_email or "-",
      supplier_name=supplier.companyName,
      reason="이메일 없음",
      supplier_channel_id=supplier.channelId
    )
    return

  # 3) 계약 템플릿/필드 구성
  t = (getattr(supplier, "contractTemplate", "") or "").upper()
  fields = []
  template_id = None

  if t == "A":
    # 단일 퍼센트
    pct = supplier.contractPercent
    if pct is None or float(pct) < 0 or float(pct) > 100:
      supplier.contractStatus = 'E'
      try:
        db.session.commit()
      except Exception:
        db.session.rollback()
      notify_contract_failed(
        recipient_email=recipient_email,
        supplier_name=supplier.companyName,
        reason="계약서 A: 수수료(%) 값이 유효하지 않습니다.",
        supplier_channel_id=supplier.channelId
      )
      return
    # 전송 필드 (필드 키는 템플릿에 맞춰 수정)
    fields = [
      {"id": "수수료", "value": f"수수료 {pct}% 를"}
    ]

    template_id = os.getenv("EFORMSIGN_TEMPLATE_ID_A")

  elif t == "B":
    # 임계금액 + 이하/초과 퍼센트
    th = supplier.contractThreshold
    pu = supplier.contractPercentUnder
    po = supplier.contractPercentOver
    ok = True
    try:
      ok = (th is not None and int(th) >= 0 and
            pu is not None and 0 <= float(pu) <= 100 and
            po is not None and 0 <= float(po) <= 100)
    except Exception:
      ok = False

    if not ok:
      supplier.contractStatus = 'E'
      try:
        db.session.commit()
      except Exception:
        db.session.rollback()
      notify_contract_failed(
        recipient_email=recipient_email,
        supplier_name=supplier.companyName,
        reason="계약서 B: 임계금액/수수료(%) 값이 유효하지 않습니다.",
        supplier_channel_id=supplier.channelId
      )
      return

    # fields = [
    #   {"name": "threshold", "value": str(int(th))},
    #   {"name": "percent_under", "value": str(pu)},
    #   {"name": "percent_over", "value": str(po)},
    # ]
    template_id = os.getenv("EFORMSIGN_TEMPLATE_ID_B")

  else:
    # 템플릿 미선택 → 발송 안 함(대신 관리자 확인 필요)
    supplier.contractStatus = 'E'
    try:
      db.session.commit()
    except Exception:
      db.session.rollback()
    notify_contract_failed(
      recipient_email=recipient_email,
      supplier_name=supplier.companyName,
      reason="계약서 템플릿이 선택되지 않았습니다.",
      supplier_channel_id=supplier.channelId
    )
    return

  if not template_id:
    supplier.contractStatus = 'E'
    try:
      db.session.commit()
    except Exception:
      db.session.rollback()
    notify_contract_failed(
      recipient_email=recipient_email,
      supplier_name=supplier.companyName,
      reason="EFORMSIGN 템플릿 ID가 설정되지 않았습니다.",
      supplier_channel_id=supplier.channelId
    )
    return

  # 4) 전송대기(P) 저장
  try:
    supplier.contractStatus = 'P'
    db.session.commit()
  except Exception:
    db.session.rollback()
    print(f"[{datetime.now()}] 계약 상태(P) 저장 실패 seq={supplier.seq}")

  # 5) 토큰 발급 → 문서 생성/전송
  try:
    svc = EformsignService()
    tr = svc.issue_access_token()
    print(
      f"[{datetime.now()}] eformsign 토큰 발급 성공 "
      f"seq={supplier.seq} company={supplier.companyName} api_url={tr.api_url} expires_in={tr.expires_in}"
    )

    recipient_name = (supplier.companyName or "공급사 담당자").strip()

    # ⚠️ create_document_from_template 의 fields 포맷은 실제 템플릿에 맞춰 조정 필요
    doc = svc.create_document_from_template(
      token=tr,
      template_id=template_id,
      recipient_name=recipient_name,
      recipient_email=recipient_email,
      fields=fields,
    )

    doc_id = doc.get("document_id") or None
    print(
      f"[{datetime.now()}] eformsign 문서 전송 성공 seq={supplier.seq} company={supplier.companyName} "
      f"recipient={recipient_email} document_id={doc_id or '(unknown)'}"
    )

    supplier.contractStatus = 'A'
    if doc_id:
      supplier.contractId = doc_id

    notify_contract_sent(
      recipient_email=recipient_email,
      supplier_name=supplier.companyName,
      document_id=doc_id,
      supplier_channel_id=supplier.channelId
    )

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
      f"[{datetime.now()}] eformsign 처리 실패 (토큰/문서) "
      f"seq={supplier.seq} company={supplier.companyName} status={status} payload={payload}"
    )
    supplier.contractStatus = 'E'
    try:
      db.session.commit()
    except Exception:
      db.session.rollback()
    notify_contract_failed(
      recipient_email=recipient_email,
      supplier_name=supplier.companyName,
      reason="eformsign 처리 실패 (토큰/문서)",
      supplier_channel_id=supplier.channelId
    )

  except Exception as e:
    print(f"[{datetime.now()}] eformsign 처리 실패(예상치 못한 오류) seq={supplier.seq} err={e}")
    supplier.contractStatus = 'E'
    try:
      db.session.commit()
    except Exception:
      db.session.rollback()
    notify_contract_failed(
      recipient_email=recipient_email,
      supplier_name=supplier.companyName,
      reason=str(e),
      supplier_channel_id=supplier.channelId
    )

