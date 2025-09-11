# application/jobs/settlementMonthlyJob.py
# -*- coding: utf-8 -*-
import os
from datetime import date, datetime
from typing import List

from sqlalchemy import select, and_

from application.src.models import db
from application.src.models.SupplierList import SupplierList
from application.src.service.settlement_service import make_settlement_excel, prev_month_range
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "").strip()

def _list_target_suppliers(limit: int = 500) -> List[SupplierList]:
  """
  대상: 채널이 있고(s.channelId), 공급사 식별자가 있는(s.supplierCode) 공급사
  """
  rows = (
    db.session.execute(
      select(SupplierList)
      .where(
        and_(
          SupplierList.channelId.isnot(None),
          SupplierList.supplierCode.isnot(None)
        )
      )
      .order_by(SupplierList.seq.asc())
      .limit(limit)
    ).scalars().all()
  )
  return rows

def broadcast_monthly_settlements(batch_limit: int = 500, out_dir: str = "/tmp"):
  """
  매월 1일 09:00에 실행 → 전월 정산서를 각 공급사 채널로 업로드
  """
  if not SLACK_BOT_TOKEN:
    print("[monthly_settlement] SLACK_BOT_TOKEN is empty; skip")
    return

  cli = WebClient(token=SLACK_BOT_TOKEN)
  today = date.today()
  start, end = prev_month_range(today)  # 전월 1일 ~ 전월 말일
  print(f"[monthly_settlement] period={start}~{end}")

  suppliers = _list_target_suppliers(limit=batch_limit)
  print(f"[monthly_settlement] suppliers={len(suppliers)}")

  for s in suppliers:
    ch = (s.channelId or "").strip()
    sid = (s.supplierCode or "").strip()
    name = (s.companyName or "").strip()
    if not (ch and sid):
      continue

    try:
      fpath, summary = make_settlement_excel(start, end, supply_id=sid, out_dir=out_dir)
      print(f"[monthly_settlement] excel_ready seq={s.seq} company={name} path={fpath} summary={summary}")

      # 비공개 채널 대비 join 선 시도 (이미 멤버면 Slack이 무시)
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
      print(f"[monthly_settlement] upload_ok channel={ch} company={name}")

    except SlackApiError as e:
      data = getattr(e, "response", None)
      status = getattr(data, "status_code", None)
      payload = getattr(data, "data", None)
      print(f"[monthly_settlement] slack_error seq={s.seq} ch={ch} name={name} status={status} data={payload}")
    except Exception as e:
      print(f"[monthly_settlement] error seq={s.seq} ch={ch} name={name} err={e}")
