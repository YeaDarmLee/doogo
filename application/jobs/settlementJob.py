# application/jobs/settlementJob.py
# -*- coding: utf-8 -*-
import os
from datetime import date
from pathlib import Path

from application.src.service.slack_service import upload_file
from application.src.service.settlement_service import (
  make_settlement_excel,
  prev_month_range,
  prev_week_range,
)
from application.src.repositories.SupplierListRepository import SupplierListRepository

def _ensure_outdir(path: str) -> str:
  p = Path(path or "/tmp")
  p.mkdir(parents=True, exist_ok=True)
  return str(p)

def _safe(s):
  return (s or "").strip() if isinstance(s, str) else (s if s is not None else "")

def _fmt_amount(v):
  try:
    return f"{int(v):,}원"
  except Exception:
    try:
      return f"{float(v):,.0f}원"
    except Exception:
      return str(v)

def run_monthly_settlement(out_dir: str = "/tmp"):
  """
  월별 정산 배치: 전월 1일 ~ 전월 말일
  - settlementPeriod == 'M' 대상만 처리
  """
  out_dir = _ensure_outdir(out_dir)
  today = date.today()
  start, end = prev_month_range(today)
  print(f"[monthly_settlement] period={start}~{end}")

  suppliers = SupplierListRepository.find_by_settlement_period("M", limit=500)
  print(f"[monthly_settlement] suppliers={len(suppliers)}")

  for s in suppliers:
    ch = _safe(s.channelId)
    sid = _safe(s.supplierCode)
    name = _safe(s.companyName)
    if not ch or not sid:
      print(f"[monthly_settlement] skip seq={s.seq} ch={ch} sid={sid} name={name}")
      continue
    try:
      fpath, summary = make_settlement_excel(start, end, supply_id=sid, out_dir=out_dir)
      print(f"[monthly_settlement] excel_ready seq={s.seq} company={name} path={fpath}")

      initial_comment = (
        f"*[자동 전송] {start:%Y-%m} 정산서*\n"
        f"기간: {start} ~ {end}\n"
        f"배송완료 {summary.get('delivered_rows',0)}행 · 취소처리 {summary.get('canceled_rows',0)}행\n"
        f"총 상품 결제 금액: {_fmt_amount(summary.get('gross_amount',0))} / "
        f"배송비: {_fmt_amount(summary.get('shipping_amount',0))}\n"
        f"수수료: {_fmt_amount(summary.get('commission_amount',0))} / "
        f"총 합계 금액: {_fmt_amount(summary.get('final_amount',0))}"
      )

      ok = upload_file(
        ch,
        filepath=fpath,
        filename=os.path.basename(fpath),
        title=f"{start:%Y-%m} 정산서",
        initial_comment=initial_comment
      )
      if not ok:
        print(f"[monthly_settlement] upload_fail channel={ch} company={name}")
      else:
        print(f"[monthly_settlement] upload_ok channel={ch} company={name}")

    except Exception as e:
      print(f"[monthly_settlement] error seq={s.seq} ch={ch} name={name} err={e}")

def run_weekly_settlement(out_dir: str = "/tmp"):
  """
  주별 정산 배치: 지난 주 (월~일)
  - settlementPeriod == 'W' 대상만 처리
  """
  out_dir = _ensure_outdir(out_dir)
  today = date.today()
  start, end = prev_week_range(today)
  print(f"[weekly_settlement] period={start}~{end}")

  suppliers = SupplierListRepository.find_by_settlement_period("W", limit=500)
  print(f"[weekly_settlement] suppliers={len(suppliers)}")

  for s in suppliers:
    ch = _safe(s.channelId)
    sid = _safe(s.supplierCode)
    name = _safe(s.companyName)
    if not ch or not sid:
      print(f"[weekly_settlement] skip seq={s.seq} ch={ch} sid={sid} name={name}")
      continue
    try:
      fpath, summary = make_settlement_excel(start, end, supply_id=sid, out_dir=out_dir)
      print(f"[weekly_settlement] excel_ready seq={s.seq} company={name} path={fpath}")

      title = f"{start:%Y-%m-%d}~{end:%Y-%m-%d} 주간 정산서"
      initial_comment = (
        f"*[자동 전송] {title}*\n"
        f"기간: {start} ~ {end}\n"
        f"배송완료 {summary.get('delivered_rows',0)}행 · 취소처리 {summary.get('canceled_rows',0)}행\n"
        f"총 상품 결제 금액: {_fmt_amount(summary.get('gross_amount',0))} / "
        f"배송비: {_fmt_amount(summary.get('shipping_amount',0))}\n"
        f"수수료: {_fmt_amount(summary.get('commission_amount',0))} / "
        f"총 합계 금액: {_fmt_amount(summary.get('final_amount',0))}"
      )

      ok = upload_file(
        ch,
        filepath=fpath,
        filename=os.path.basename(fpath),
        title=title,
        initial_comment=initial_comment
      )
      if not ok:
        print(f"[weekly_settlement] upload_fail channel={ch} company={name}")
      else:
        print(f"[weekly_settlement] upload_ok channel={ch} company={name}")

    except Exception as e:
      print(f"[weekly_settlement] error seq={s.seq} ch={ch} name={name} err={e}")
