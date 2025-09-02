from datetime import datetime
from sqlalchemy import text, select
from sqlalchemy.exc import SQLAlchemyError
from application.src.models import db
from application.src.models.SupplierList import SupplierList
from application.src.service.slackService import create_slack_channel_only

# (선택) SlackApiError 상세 로깅을 위해 추가
try:
  from slack_sdk.errors import SlackApiError
except Exception:
  SlackApiError = Exception  # SDK 미존재 시 안전 가드

def _acquire_lock(lock_key: str) -> bool:
  """
  전역 락으로 다중 인스턴스 동시 실행 방지.
  MySQL GET_LOCK(key, timeout_sec=0): 즉시 시도, 획득 실패 시 0/NULL.
  """
  got = db.session.execute(text("SELECT GET_LOCK(:k, 0)"), {"k": lock_key}).scalar()
  return bool(got)

def _release_lock(lock_key: str):
  """
  락 해제. 실패해도 잡 전체에는 영향 없도록 무시.
  """
  try:
    db.session.execute(text("SELECT RELEASE_LOCK(:k)"), {"k": lock_key})
  except Exception:
    pass

def process_pending_suppliers(batch_size: int = 10, lock_key: str = "job_supplier_slack"):
  """
  공급사 Slack 채널 자동 생성 배치
  1) 전역 락 획득 (다중 인스턴스 경쟁 방지)
  2) stateCode IS NULL 인 건을 소량(batch) 조회
  3) 각 항목을 'P'(Processing)로 선점 마킹 후 commit
  4) Slack 채널 생성 시도:
     - 성공: s.channelId 저장 + stateCode='A' 로 전이
     - 실패: stateCode='E' 로 전이
  """
  if not _acquire_lock(lock_key):
    # 다른 인스턴스가 실행 중
    return

  try:
    # 1) 대기건 조회 (stateCode가 NULL인 항목)
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

    # 2) 선점 마킹(P) 저장
    for s in pending:
      s.stateCode = 'P'
    db.session.commit()

    # 3) 실제 처리
    for s in pending:
      try:
        # 3-1) Slack 리소스 생성
        #   create_slack_channel_only(s)는 dict를 반환해야 함: {"ok": True, "channel_id": "...", "channel_name": "..."}
        res = create_slack_channel_only(s)

        # 3-2) 채널 ID 필수 확인
        channel_id = (res or {}).get("channel_id")
        if not channel_id:
          # 반환값 이상(예: Slack에서 생성되었는데 파싱 실패) → 실패로 간주
          s.stateCode = 'E'
          db.session.commit()
          print(f"[{datetime.now()}] Slack 처리 실패(채널ID 없음) seq={s.seq} company={s.companyName} res={res}")
          continue

        # 3-3) DB에 채널 ID 저장 + 상태 전이
        s.channelId = channel_id
        s.stateCode = 'A'
        db.session.commit()
        print(f"[{datetime.now()}] Slack 처리 성공 seq={s.seq} company={s.companyName} channel_id={channel_id}")

      except SlackApiError as e:
        # Slack SDK 에러 상세 로깅
        resp = getattr(e, "response", None)
        status = getattr(resp, "status_code", None)
        data = getattr(resp, "data", None)
        err_code = (data.get("error") if isinstance(data, dict) else None)

        s.stateCode = 'E'
        db.session.commit()
        print(
          f"[{datetime.now()}] Slack 처리 실패(SlackApiError) "
          f"seq={s.seq} company={s.companyName} "
          f"status={status} error={err_code} data={data}"
        )

      except Exception as e:
        # 기타 예외
        s.stateCode = 'E'
        db.session.commit()
        print(f"[{datetime.now()}] Slack 처리 실패 seq={s.seq} company={s.companyName} err={e}")

  except SQLAlchemyError as e:
    # 쿼리/커밋 오류
    db.session.rollback()
    print("DB error during job:", e)

  except Exception as e:
    # 그 외 예외
    db.session.rollback()
    print("Unexpected error during job:", e)

  finally:
    _release_lock(lock_key)
