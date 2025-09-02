from apscheduler.schedulers.background import BackgroundScheduler
from pytz import timezone

def start_scheduler(app):
  """
  Flask 앱과 함께 백그라운드 스케줄러를 기동.
  """
  tz = timezone('Asia/Seoul')
  scheduler = BackgroundScheduler(timezone=tz)

  # 잡 함수는 app_context 내에서 실행되도록 래핑
  from application.jobs.supplierJobs import process_pending_suppliers

  def job_wrapper():
    with app.app_context():
      process_pending_suppliers(batch_size=10)

  # 예: 30초마다 실행, 중복 인스턴스 방지(잡 레벨) + 누락 분 합침
  scheduler.add_job(
    job_wrapper,
    'interval',
    seconds=5,
    id='supplier_slack_job',
    max_instances=1,
    coalesce=True,
    misfire_grace_time=60
  )

  scheduler.start()

  # 앱 종료 시 스케줄러도 종료
  @app.teardown_appcontext
  def _shutdown(exception=None):
    try:
      if scheduler.running:
        scheduler.shutdown(wait=False)
    except Exception:
      pass

  # 외부에서 참조 가능하면 좋을 때 반환
  return scheduler
