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

  # ========= 기존: 공급사 Slack 처리 배치 (30초 간격) =========
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

  # ========= 신규: 매월 1일 09:00 전월 정산서 일괄 전송 =========
  def monthly_settlement_job():
    """
    전월 1일 ~ 전월 말일 정산 엑셀을 생성하여 각 공급사 채널(channelId)로 업로드
    - /정산 커맨드와 동일한 생성·업로드 로직 재사용
    """
    with app.app_context():
      from datetime import date
      from sqlalchemy import select, and_
      from slack_sdk import WebClient
      from slack_sdk.errors import SlackApiError

      from application.src.models import db
      from application.src.models.SupplierList import SupplierList
      from application.src.service.settlement_service import make_settlement_excel, prev_month_range

      bot_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
      if not bot_token:
        app.logger.warning("[monthly_settlement] SLACK_BOT_TOKEN missing → skip")
        return

      cli = WebClient(token=bot_token)

      today = date.today()
      start, end = prev_month_range(today)  # 전월 기간 재사용
      app.logger.info(f"[monthly_settlement] period={start}~{end}")

      # 채널이 있고 공급사 코드가 있는 대상만
      rows = (
        db.session.execute(
          select(SupplierList)
          .where(
            and_(
              SupplierList.channelId.isnot(None),
              SupplierList.supplierCode.isnot(None)
            )
          )
        ).scalars().all()
      )
      app.logger.info(f"[monthly_settlement] suppliers={len(rows)}")

      for s in rows:
        ch = (s.channelId or "").strip()
        sid = (s.supplierCode or "").strip()
        name = (s.companyName or "").strip()
        if not (ch and sid):
          continue

        try:
          fpath, summary = make_settlement_excel(start, end, supply_id=sid, out_dir="/tmp")
          app.logger.info(f"[monthly_settlement] excel_ready seq={s.seq} company={name} path={fpath}")

          # 비공개 채널 대비 선참여 시도(이미 멤버면 Slack이 무시)
          try:
            cli.conversations_join(channel=ch)
          except Exception:
            pass

          initial_comment = (
            f"*[자동 전송] {start:%Y-%m} 정산서*\n"
            f"기간: {start} ~ {end}\n"
            f"배송완료 {summary.get('delivered_rows',0)}행 · 취소처리 {summary.get('canceled_rows',0)}행\n"
            f"총 상품 결제 금액: {summary.get('gross_amount',0):,}원 / 배송비: {summary.get('shipping_amount',0):,}원\n"
            f"수수료: {summary.get('commission_amount',0):,}원 / 총 합계 금액: {summary.get('final_amount',0):,}원"
          )

          cli.files_upload_v2(
            channel=ch,
            file=fpath,
            filename=os.path.basename(fpath),
            title=f"{start:%Y-%m} 정산서",
            initial_comment=initial_comment
          )
          app.logger.info(f"[monthly_settlement] upload_ok channel={ch} company={name}")

        except SlackApiError as e:
          data = getattr(e, "response", None)
          status = getattr(data, "status_code", None)
          payload = getattr(data, "data", None)
          app.logger.error(f"[monthly_settlement] slack_error seq={s.seq} ch={ch} name={name} status={status} data={payload}")
        except Exception as e:
          app.logger.exception(f"[monthly_settlement] error seq={s.seq} ch={ch} name={name} err={e}")

  # 매월 1일 09:00(KST) 실행
  scheduler.add_job(
    monthly_settlement_job,
    trigger='cron',
    day=1, hour=9, minute=0,
    id='monthly_settlement_broadcast',
    max_instances=1,
    coalesce=True,            # 누락분 합침
    misfire_grace_time=3600,  # 최대 1시간 유예
    replace_existing=True
  )

  scheduler.start()
  atexit.register(lambda: scheduler.shutdown(wait=False))  # 프로세스 종료 시에만 정리
  _scheduler = scheduler
  app.logger.info("APScheduler started")
  return scheduler
