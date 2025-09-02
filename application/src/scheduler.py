# scheduler_boot.py
import os, atexit, logging
from apscheduler.schedulers.background import BackgroundScheduler
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

  from application.jobs.supplierJobs import process_pending_suppliers

  def job_wrapper():
    with app.app_context():
      app.logger.info("[supplier_slack_job] started")
      try:
        process_pending_suppliers(batch_size=10)
      except Exception as e:
        app.logger.exception(e)
      finally:
        app.logger.info("[supplier_slack_job] finished")

  scheduler.add_job(
    job_wrapper,
    trigger='interval',
    seconds=30,
    id='supplier_slack_job',
    max_instances=1,
    coalesce=True,
    misfire_grace_time=60,
    replace_existing=True  # 리로드 시 기존 id 교체
  )

  scheduler.start()
  atexit.register(lambda: scheduler.shutdown(wait=False))  # 프로세스 종료 시에만 정리
  _scheduler = scheduler
  app.logger.info("APScheduler started")
  return scheduler
