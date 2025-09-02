from datetime import datetime
from sqlalchemy import text, select
from sqlalchemy.exc import SQLAlchemyError
from application.src.models import db
from application.src.models.SupplierList import SupplierList
from application.src.service.slackService import create_slack_channel_only

def _acquire_lock(lock_key: str) -> bool:
  # MySQL GET_LOCK(key, timeout_sec=0) : 즉시 시도, 실패 시 0/NULL 반환
  got = db.session.execute(text("SELECT GET_LOCK(:k, 0)"), {"k": lock_key}).scalar()
  return bool(got)

def _release_lock(lock_key: str):
  try:
    db.session.execute(text("SELECT RELEASE_LOCK(:k)"), {"k": lock_key})
  except Exception:
    pass

def process_pending_suppliers(batch_size: int = 10, lock_key: str = "job_supplier_slack"):
  """
  1) 전역 락 획득 (다중 인스턴스 경쟁 방지)
  2) stateCode IS NULL 인 것들을 소량(batch) 조회 → 'P'로 마킹(commit)
  3) 각 항목 Slack 생성 시도 → 성공 'A', 실패 'E'
  """
  if not _acquire_lock(lock_key):
    # 다른 인스턴스가 처리 중
    return

  try:
    # 1) 대기건 조회
    pending = db.session.execute(
      select(SupplierList)
        .where(SupplierList.stateCode.is_(None))
        .order_by(SupplierList.seq.asc())
        .limit(batch_size)
    ).scalars().all()

    if not pending:
      return

    # 2) 'P' 처리중 마킹 (선점)
    for s in pending:
      s.stateCode = 'P'
    db.session.commit()

    # 3) 실제 처리
    for s in pending:
      try:
        _ = create_slack_channel_only(s)
        # 성공
        s.stateCode = 'A'
        db.session.commit()
      except Exception as e:
        # 실패 → 오류 상태로 표시
        s.stateCode = 'E'
        db.session.commit()
        # 운영 로그
        print(f"[{datetime.now()}] Slack 처리 실패 seq={s.seq} company={s.companyName} err={e}")

  except SQLAlchemyError as e:
    db.session.rollback()
    print("DB error during job:", e)
  except Exception as e:
    db.session.rollback()
    print("Unexpected error during job:", e)
  finally:
    _release_lock(lock_key)
