# scheduler.py
import os, atexit, logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone

_scheduler = None  # 중복 시작 방지

def start_scheduler(app):
  global _scheduler
  if _scheduler and _scheduler.running:
    return _scheduler

  # 디버그 리로더 부모 프로세스에서는 시작하지 않음
  if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
    return None

  tz = timezone('Asia/Seoul')
  scheduler = BackgroundScheduler(timezone=tz)

  # 로깅(동작 확인용)
  aps_log = logging.getLogger("apscheduler")
  if not aps_log.handlers:
    aps_log.setLevel(logging.INFO)
    aps_log.addHandler(logging.StreamHandler())

  # ========= 기존: 공급사 Slack 처리 배치 (30초 간격) =========
  from application.jobs.supplierJobs import process_pending_suppliers

  def supplier_job():
    with app.app_context():
      app.logger.info("[supplier_slack_job] started")
      try:
        process_pending_suppliers(batch_size=10)
      except Exception as e:
        app.logger.exception(e)
      finally:
        app.logger.info("[supplier_slack_job] finished")

  scheduler.add_job(
    supplier_job,
    trigger='interval',
    seconds=30,
    id='supplier_slack_job',
    max_instances=1,
    coalesce=True,
    misfire_grace_time=60,
    replace_existing=True
  )

  # ========= 신규: 주별 정산 (지난 주 월~일) =========
  from application.jobs.settlementJob import run_weekly_settlement

  def weekly_settlement_job():
    with app.app_context():
      app.logger.info("[settlement_weekly_job] started")
      try:
        run_weekly_settlement(out_dir="/tmp")
      except Exception as e:
        app.logger.exception(e)
      finally:
        app.logger.info("[settlement_weekly_job] finished")

  scheduler.add_job(
    weekly_settlement_job,
    trigger=CronTrigger(day_of_week='mon', hour=8, minute=0, second=0),  # 매주 월 08:00(KST)
    id='settlement_weekly_job',
    max_instances=1,
    coalesce=True,
    misfire_grace_time=3600,
    replace_existing=True
  )

  # ========= 신규: 월별 정산 (전월 1일~말일) =========
  from application.jobs.settlementJob import run_monthly_settlement

  def monthly_settlement_job():
    with app.app_context():
      app.logger.info("[settlement_monthly_job] started")
      try:
        run_monthly_settlement(out_dir="/tmp")
      except Exception as e:
        app.logger.exception(e)
      finally:
        app.logger.info("[settlement_monthly_job] finished")

  scheduler.add_job(
    monthly_settlement_job,
    trigger=CronTrigger(day='1', hour=8, minute=0, second=0),  # 매월 1일 08:00(KST)
    id='settlement_monthly_job',
    max_instances=1,
    coalesce=True,
    misfire_grace_time=3600,
    replace_existing=True
  )
  
  # ========= 신규: 격주 정산 (전월 1일~말일) =========
  from application.jobs.settlementJob import run_biweekly_settlement  # ⬅ import

  def biweekly_settlement_job():
    with app.app_context():
      app.logger.info("[settlement_biweekly_job] started")
      try:
        run_biweekly_settlement(out_dir="/tmp")
      except Exception as e:
        app.logger.exception(e)
      finally:
        app.logger.info("[settlement_biweekly_job] finished")

  scheduler.add_job(
    biweekly_settlement_job,
    trigger=CronTrigger(day='1,15', hour=8, minute=0, second=0),  # 매월 1일/15일 08:00(KST)
    id='settlement_biweekly_job',
    max_instances=1,
    coalesce=True,
    misfire_grace_time=3600,
    replace_existing=True
  )

  scheduler.start()
  atexit.register(lambda: scheduler.shutdown(wait=False))  # 프로세스 종료 시에만 정리
  _scheduler = scheduler
  app.logger.info("APScheduler started (supplier/weekly/monthly)")
  return scheduler
