# application/jobs/settlementJob.py
# -*- coding: utf-8 -*-
import os
from datetime import date, datetime
from pathlib import Path

from application.src.service.slack_service import upload_file_with_button
from application.src.service.settlement_service import (
  prev_month_range,
  prev_week_range,
)
from application.src.repositories.SupplierListRepository import SupplierListRepository
from application.src.repositories.SettlementRepository import SettlementRepository
# from application.src.repositories.SettlementDetailRepository import SettlementDetailRepository  # 상세 저장 필요 시

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

def _yyyy_mm(d: date) -> str:
  return d.strftime("%Y-%m")

def run_monthly_settlement(out_dir: str = "/tmp"):
  """
  월별 정산 배치: 전월 1일 ~ 전월 말일
  - settlementPeriod == 'M' 대상만 처리
  - 엑셀 생성 및 슬랙 업로드, 동시에 정산 헤더를 DB에 upsert
  """
  out_dir = _ensure_outdir(out_dir)
  today = date.today()
  start, end = prev_month_range(today)
  print(f"[monthly_settlement] period={start}~{end}")

  suppliers = SupplierListRepository.find_by_settlement_period("M", limit=500)
  print(f"[monthly_settlement] suppliers={len(suppliers)}")

  for s in suppliers:
    ch = _safe(getattr(s, "channelId", None))
    sid = _safe(getattr(s, "supplierCode", None))
    name = _safe(getattr(s, "companyName", None))
    if not ch or not sid:
      print(f"[monthly_settlement] skip seq={s.seq} ch={ch} sid={sid} name={name}")
      continue

    try:
      # 1) 정산 엑셀 생성 및 슬랙 업로드
      fpath, summary = upload_file_with_button(
        supply_id=sid,
        channel=ch,
        start=start,
        end=end
      )
      
      # 2) 헤더 upsert (슬랙 전송 전: READY)
      header = SettlementRepository.upsert_header(
        supplier_seq=s.seq,
        supplier_code=sid,
        period_type="M",
        period_start=start,
        period_end=end,
        month=_yyyy_mm(start),
        gross_amount=int(summary.get("gross_amount", 0) or 0),
        shipping_amount=int(summary.get("shipping_amount", 0) or 0),
        commission_rate=float(summary.get("commission_rate", 0.0) or 0.0),   # summary에 수수료율 포함 시
        commission_amount=int(summary.get("commission_amount", 0) or 0),
        final_amount=int(summary.get("final_amount", 0) or 0),
        status="READY",
        deposit_due_dt=None,
        sent_at=None,
        slack_channel_id=ch,
        slack_file_ts=None,
        excel_file_path=fpath,
      )

      # (선택) 상세 저장: make_settlement_excel이 상세 rows를 반환한다면 주석 제거
      # SettlementDetailRepository.delete_by_header(header.id)
      # SettlementDetailRepository.insert_many(header.id, sid, summary.get("rows", []))
      
      SettlementRepository.update_status(
        header.id,
        status="SENT",
        sent_at=datetime.now(),
        slack_channel_id=ch,
        # slack_file_ts=...,  # upload_file이 ts를 리턴하도록 바꾸면 여기에 반영
        excel_file_path=fpath,
      )
      print(f"[monthly_settlement] upload_ok channel={ch} company={name}")

    except Exception as e:
      print(f"[monthly_settlement] error seq={s.seq} ch={ch} name={name} err={e}")

def run_weekly_settlement(out_dir: str = "/tmp"):
  """
  주별 정산 배치: 지난 주 (월~일)
  - settlementPeriod == 'W' 대상만 처리
  - 엑셀 생성 및 슬랙 업로드, 동시에 정산 헤더를 DB에 upsert
  """
  out_dir = _ensure_outdir(out_dir)
  today = date.today()
  start, end = prev_week_range(today)
  print(f"[weekly_settlement] period={start}~{end}")

  suppliers = SupplierListRepository.find_by_settlement_period("W", limit=500)
  print(f"[weekly_settlement] suppliers={len(suppliers)}")

  for s in suppliers:
    ch = _safe(getattr(s, "channelId", None))
    sid = _safe(getattr(s, "supplierCode", None))
    name = _safe(getattr(s, "companyName", None))
    if not ch or not sid:
      print(f"[weekly_settlement] skip seq={s.seq} ch={ch} sid={sid} name={name}")
      continue

    try:
      # 1) 정산 엑셀 생성 및 슬랙 업로드
      fpath, summary = upload_file_with_button(
        supply_id=sid,
        channel=ch,
        start=start,
        end=end
      )

      # 2) 헤더 upsert (슬랙 전송 전: READY)
      header = SettlementRepository.upsert_header(
        supplier_seq=s.seq,
        supplier_code=sid,
        period_type="W",
        period_start=start,
        period_end=end,
        month=None,  # 주간은 굳이 안 채워도 됨(필요시 start 기준 'YYYY-MM' 넣기)
        gross_amount=int(summary.get("gross_amount", 0) or 0),
        shipping_amount=int(summary.get("shipping_amount", 0) or 0),
        commission_rate=float(summary.get("commission_rate", 0.0) or 0.0),
        commission_amount=int(summary.get("commission_amount", 0) or 0),
        final_amount=int(summary.get("final_amount", 0) or 0),
        status="READY",
        deposit_due_dt=None,
        sent_at=None,
        slack_channel_id=ch,
        slack_file_ts=None,
        excel_file_path=fpath,
      )

      # (선택) 상세 저장
      # SettlementDetailRepository.delete_by_header(header.id)
      # SettlementDetailRepository.insert_many(header.id, sid, summary.get("rows", []))

      SettlementRepository.update_status(
        header.id,
        status="SENT",
        sent_at=datetime.now(),
        slack_channel_id=ch,
        # slack_file_ts=...,  # upload_file 리턴값 확장 시 반영
        excel_file_path=fpath,
      )
      print(f"[weekly_settlement] upload_ok channel={ch} company={name}")

    except Exception as e:
      print(f"[weekly_settlement] error seq={s.seq} ch={ch} name={name} err={e}")
