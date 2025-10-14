# -*- coding: utf-8 -*-
# application/src/service/settlement_service.py
"""
정산(엑셀) 생성 로직
- 한 번 조회(canceled=None, order_status=None) 후 shipping_fee(또는 detail 합)가 0 → '취소처리', 그 외 '배송완료' 로 자동 분류
- 공급사 필터: supplier_id (채널→supplierCode 권장)
- 금액: items.payment_amount 우선, 없으면 보정 계산 → 마지막에 /orders/paymentamount 로 비어있는 품목만 덮어쓰기
- shipping_fee_detail 이 dict 또는 list 로 올 수 있어 모두 처리
- 엑셀: '취소처리' 행은 빨간색 배경(정산내역 시트), 요약 4줄(노란색)은 시트 맨 아래에 추가 (별도 탭 생성 안 함)
  * 총 상품 결제 금액
  * 배송비
  * 수수료 15%
  * 총 합계 금액  = (총 상품 결제 금액 - 수수료) + 배송비
"""

import os
import time
from datetime import date, timedelta, datetime
from typing import Dict, Any, List, Optional, Tuple
import requests
from decimal import Decimal

from application.src.service.cafe24_oauth_service import get_access_token
from zoneinfo import ZoneInfo

from application.src.repositories.SupplierListRepository import SupplierListRepository

CAFE24_BASE_URL = os.getenv("CAFE24_BASE_URL", "").rstrip("/")
SETTLEMENT_STORE_NAME = os.getenv("SETTLEMENT_STORE_NAME", "두고")


# -------------------- 유틸 --------------------
def _toi(v) -> int:
  if v is None:
    return 0
  if isinstance(v, int):
    return v
  if isinstance(v, float):
    return int(v)
  if isinstance(v, str):
    s = v.strip().replace(",", "")
    if not s:
      return 0
    try:
      return int(Decimal(s))
    except Exception:
      return 0
  try:
    return int(v)
  except Exception:
    return 0

def _tod(v) -> Decimal:
  try:
    return Decimal(str(v))
  except Exception:
    return Decimal(0)

def _headers() -> Dict[str, str]:
  token = get_access_token()
  return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def _req(method: str, path: str, params: Dict[str, Any], try_max=6) -> Dict[str, Any]:
  url = f"{CAFE24_BASE_URL}{path}"
  last = None
  for i in range(1, try_max + 1):
    try:
      print(f"[settlement] {method} {url} try={i}/{try_max} params={params}")
      resp = requests.request(method, url, headers=_headers(), params=params, timeout=25)
      if resp.status_code == 429:
        ra = int(resp.headers.get("Retry-After", "1"))
        wait = max(1, ra)
        print(f"[settlement] 429 Too Many Requests → sleep {wait}s")
        time.sleep(wait); continue
      resp.raise_for_status()
      data = resp.json() if resp.content else {}
      print(f"[settlement] OK {url} status={resp.status_code} bytes={len(resp.content)}")
      return data or {}
    except Exception as e:
      last = e
      print(f"[settlement] FAIL {url} err={e}")
      time.sleep(min(1.2 * i, 5))
  raise RuntimeError(last)

def prev_month_range(today: date) -> Tuple[date, date]:
  first_this = today.replace(day=1)
  last_prev = first_this - timedelta(days=1)
  first_prev = last_prev.replace(day=1)
  return first_prev, last_prev

def prev_week_range(today: date) -> Tuple[date, date]:
  """
  지난 '완전한 1주(월~일)' 범위
  """
  d = today  # ← 불필요한 fallback 제거
  weekday = d.weekday()           # Mon=0..Sun=6
  end = d - timedelta(days=weekday + 1)  # 직전 일요일
  start = end - timedelta(days=6)        # 그 주 월요일
  return start, end

def last_day_of_month(d: date) -> date:
  if d.month == 12:
    return date(d.year, 12, 31)
  first_next = date(d.year, d.month + 1, 1)
  return first_next - timedelta(days=1)

def prev_biweekly_range(today: date) -> tuple[date, date]:
  """
  격주 정산용 '직전' 반월 구간을 반환.
  - today가 15일이면: 이번달 1~14일
  - today가 1일이면: 전월 15일~전월 말일
  - 그 외 날짜에서 수동 실행 시:
      * day <= 15  → 전월 15일~전월말
      * day > 15   → 이번달 1~14일
  """
  if today.day == 15:
    return date(today.year, today.month, 1), date(today.year, today.month, 14)
  if today.day == 1:
    prev_month = date(today.year, today.month, 1) - timedelta(days=1)
    return date(prev_month.year, prev_month.month, 15), last_day_of_month(prev_month)
  # 수동 실행 대비
  if today.day <= 15:
    prev_month = date(today.year, today.month, 1) - timedelta(days=1)
    return date(prev_month.year, prev_month.month, 15), last_day_of_month(prev_month)
  else:
    return date(today.year, today.month, 1), date(today.year, today.month, 14)

# -------------------- 조회 --------------------
def _count_orders(start: date, end: date, supply_id: Optional[str], *,
          canceled: Optional[bool] = None,
          order_status: Optional[str] = None,
          date_type: str = "order_date") -> int:
  params = {
    "start_date": start.isoformat(),
    "end_date": end.isoformat(),
    "date_type": date_type,
  }
  if supply_id:
    params["supplier_id"] = supply_id
  if canceled is not None:
    params["canceled"] = "T" if canceled else "F"
  if order_status:
    params["order_status"] = order_status
  data = _req("GET", "/api/v2/admin/orders/count", params)
  return int(data.get("count") or data.get("result", {}).get("count", 0) or 0)

def _list_orders(start: date, end: date, supply_id: Optional[str], *,
         canceled: Optional[bool] = None,
         order_status: Optional[str] = None,
         date_type: str = "order_date") -> List[Dict[str, Any]]:
  total = _count_orders(start, end, supply_id, canceled=canceled,
              order_status=order_status, date_type=date_type)
  print(f"[settlement] COUNT total={total} canceled={canceled} order_status={order_status} supply_id={supply_id}")
  if total <= 0:
    return []

  all_rows: List[Dict[str, Any]] = []
  limit = 1000
  for offset in range(0, total, limit):
    params = {
      "start_date": start.isoformat(),
      "end_date": end.isoformat(),
      "date_type": date_type,
      "limit": min(limit, total - offset),
      "offset": offset,
      "embed": "items,receivers,buyer",
    }
    if supply_id:
      params["supplier_id"] = supply_id
    if canceled is not None:
      params["canceled"] = "T" if canceled else "F"
    if order_status:
      params["order_status"] = order_status

    data = _req("GET", "/api/v2/admin/orders", params)
    rows = (data or {}).get("orders") or []
    print(f"[settlement] LIST got={len(rows)} offset={offset}")
    all_rows.extend(rows)

  return all_rows

def _fetch_payment_amounts(order_item_codes: List[str]) -> Dict[str, int]:
  """
  /orders/paymentamount 로 품목별 결제액 조회 (보조 용도)
  """
  result: Dict[str, int] = {}
  if not order_item_codes:
    return result

  CHUNK = 100
  for i in range(0, len(order_item_codes), CHUNK):
    chunk = order_item_codes[i:i + CHUNK]
    params = {"order_item_code": ",".join(chunk)}
    data = _req("GET", "/api/v2/admin/orders/paymentamount", params)
    items = (data or {}).get("items") or (data or {}).get("result", {}).get("items") or []
    for row in items:
      code = str(row.get("order_item_code") or "")
      if code:
        result[code] = _toi(row.get("payment_amount"))
    print(f"[settlement] PAY chunk={len(chunk)} rows={len(items)}")
  return result


# -------------------- 필드 도우미 --------------------
def _first_receiver(order: Dict[str, Any]) -> Dict[str, Any]:
  receivers = order.get("receivers") or []
  return receivers[0] if receivers else {}

def _receiver_addr_full(r: Dict[str, Any]) -> str:
  full = r.get("address_full")
  if full:
    return full
  a1 = r.get("address1") or ""
  a2 = r.get("address2") or ""
  return f"{a1} {a2}".strip()

def _receiver_phone(r: Dict[str, Any]) -> str:
  return r.get("cellphone") or r.get("phone") or "-"

def _receiver_carrier(r: Dict[str, Any]) -> str:
  return r.get("shipping_company_name") or r.get("delivery_company") or r.get("carrier_name") or "-"

def _receiver_tracking(r: Dict[str, Any]) -> str:
  return r.get("invoice_no") or r.get("tracking_no") or r.get("waybill_no") or "-"

def _order_shipping_fee(order: Dict[str, Any]) -> int:
  """
  주문 단위 배송비.
  shipping_fee_detail 이 dict 또는 list 로 올 수 있으므로 모두 처리.
  """
  sfd = order.get("shipping_fee_detail")
  # dict 형태
  if isinstance(sfd, dict):
    return _toi(
      sfd.get("shipping_fee")
      or sfd.get("total_shipping_fee")
      or order.get("shipping_fee")
    )
  # list 형태 (여러 배송건/수령지)
  if isinstance(sfd, list):
    total = 0
    for x in sfd:
      if isinstance(x, dict):
        total += _toi(
          x.get("shipping_fee")
          or x.get("total_shipping_fee")
        )
    if total == 0:
      total = _toi(order.get("shipping_fee"))
    return total
  # 키 자체가 없거나 형식이 다르면 최상위로 폴백
  return _toi(order.get("shipping_fee"))

def _buyer_name(order: Dict[str, Any]) -> str:
  buyer = order.get("buyer") or {}
  return buyer.get("name") or order.get("billing_name") or "-"

def _product_name(item: Dict[str, Any]) -> str:
  return item.get("product_name") or item.get("product_name_default") \
    or f"{item.get('product_no','')}/{item.get('variant_code','')}".strip()

def _filter_items_by_supplier(items: List[Dict[str, Any]], supply_id: Optional[str]) -> List[Dict[str, Any]]:
  if not supply_id:
    return items or []
  out = []
  for it in items or []:
    # Cafe24 환경에 따라 supplier_id / supplier_code / owner_code 표현이 다를 수 있으므로 폭넓게 매칭
    sid = it.get("supplier_id") or it.get("supplier_code") or it.get("owner_code")
    if str(sid) == str(supply_id):
      out.append(it)
  return out


# -------------------- 테이블 구성 --------------------
def build_settlement_rows(orders: List[Dict[str, Any]], *,
              supply_id: Optional[str]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
  """
  정산 엑셀용 행 구성 + (상태 카운트) 반환
  - shipping_fee(또는 detail 합)가 0이면 '취소처리', 그 외 '배송완료'
  - items.payment_amount 를 우선 사용, 비면 보정 후 마지막에 /orders/paymentamount 로 보강
  """
  rows: List[Dict[str, Any]] = []
  codes: List[str] = []

  delivered_cnt = 0
  canceled_cnt = 0

  for o in orders:
    rcv = _first_receiver(o)
    shipfee = _order_shipping_fee(o)
    buyer_name = _buyer_name(o)
    order_date_str = o.get("order_date") or o.get("payment_date") or ""

    status_label = "취소처리" if _toi(shipfee) == 0 else "배송완료"
    if status_label == "취소처리":
      canceled_cnt += 1
    else:
      delivered_cnt += 1

    items = _filter_items_by_supplier(o.get("items") or [], supply_id)
    for it in items:
      code = str(it.get("order_item_code") or "")
      qty = _toi(it.get("quantity"))

      pay = it.get("payment_amount")
      if pay is None:
        base = _toi(it.get("product_price")) + _toi(it.get("option_price"))
        disc = _toi(it.get("additional_discount_price")) \
           + _toi(it.get("coupon_discount_price")) \
           + _toi(it.get("app_item_discount_amount"))
        pay = (base - disc) * qty

      if code:
        codes.append(code)

      rows.append({
        "주문일시": order_date_str,
        "쇼핑몰": SETTLEMENT_STORE_NAME,
        "주문자명": buyer_name,
        "수령인": rcv.get("name") or "-",
        "수령인 주소(전체)": _receiver_addr_full(rcv),
        "수령인전화번호": _receiver_phone(rcv),
        "상품명": _product_name(it),
        "총 수량": qty,
        "총 상품구매금액": _toi(pay),
        "배송업체": _receiver_carrier(rcv),
        "운송장번호": _receiver_tracking(rcv),
        "총 배송비(전체 품목에 표시)": _toi(shipfee),
        "상태": status_label,
        "order_item_code": code,  # 보조 매핑용
        "order_id": o.get("order_id"),
      })

  # 보조: paymentamount API 로 '총 상품구매금액'이 비었던 품목만 덮어쓰기
  paymap = _fetch_payment_amounts([c for c in codes if c])
  if paymap:
    for row in rows:
      code = row.get("order_item_code")
      if code and _toi(row.get("총 상품구매금액")) == 0 and code in paymap:
        row["총 상품구매금액"] = paymap[code]

  # 내부키 제거(엑셀엔 노출하지 않음)
  for row in rows:
    row.pop("order_item_code", None)

  print(f"[settlement] AUTO status counts -> 배송완료:{delivered_cnt} / 취소처리:{canceled_cnt}")
  return rows, {"delivered_rows": delivered_cnt, "canceled_rows": canceled_cnt}


# -------------------- 엑셀 생성 --------------------
def make_settlement_excel(start: date, end: date, supply_id: Optional[str], *, out_dir: str = "/tmp") -> Tuple[str, Dict[str, Any]]:
  """
  지정 기간/공급사 기준 정산 엑셀 생성 후 (파일경로, 요약) 반환
  - 한 번 조회 후 상태 자동 분류
  - 정산내역 시트 맨 아래에 요약 4줄(노란색) 추가
    총 상품 결제 금액 / 배송비 / 수수료 X% / 총 합계 금액
  """
  supplier = SupplierListRepository.findBySupplierCode(supply_id)
  
  orders = _list_orders(start, end, supply_id, canceled=None, order_status=None, date_type="order_date")
  rows, counts = build_settlement_rows(orders, supply_id=supply_id)

  import pandas as pd
  df = pd.DataFrame(rows, columns=[
    "주문일시","쇼핑몰","주문자명","수령인","수령인 주소(전체)","수령인전화번호",
    "상품명","총 수량","총 상품구매금액","배송업체","운송장번호","총 배송비(전체 품목에 표시)","상태","order_id"
  ])

  # ✅ 총 상품 결제 금액 = '취소처리' 제외한 품목 금액 합
  if not df.empty:
    df_delivered = df[df["상태"] != "취소처리"]
    items_total = int(df_delivered["총 상품구매금액"].sum())
  else:
    items_total = 0

  # ✅ 배송비 = 주문 단위로 1회만 합산(공급사 품목이 하나라도 매칭된 주문 + 배송완료로 분류된 주문)
  shipping_total = 0
  if not df.empty:
    # order_id 별 대표 행 1개만 추려 금액 중복 방지
    seen_orders = set()
    for _, row in df_delivered.iterrows():
      oid = row.get("order_id")
      if not oid or oid in seen_orders:
        continue
      seen_orders.add(oid)
      shipping_total += _toi(row.get("총 배송비(전체 품목에 표시)"))

  # ✅ 수수료 = items_total * rate
  commission = 0
  if supplier.contractTemplate == 'A':
    commission = int(round(items_total * (supplier.contractPercent/100)))
  elif supplier.contractTemplate == 'B':
    if items_total > 10000000:
      commission_under = int(round(10000000 * (supplier.contractPercentUnder/100)))
      commission_over = int(round((items_total-10000000) * (supplier.contractPercentOver/100)))
      commission = commission_under + commission_over
    else:
      commission = int(round(items_total * (supplier.contractPercentUnder/100)))

  # ✅ 총 합계 금액 = (items_total - commission) + shipping_total
  final_total = items_total - commission + shipping_total

  os.makedirs(out_dir, exist_ok=True)
  fname = f"{start.year:04d}{start.month:02d}_정산서.xlsx"
  fpath = os.path.join(out_dir, fname)

  with pd.ExcelWriter(fpath, engine="xlsxwriter") as xw:
    # 상세 시트
    export_df = df.drop(columns=["order_id"])
    export_df.to_excel(xw, index=False, sheet_name="정산내역")

    wb = xw.book
    ws = xw.sheets["정산내역"]

    # 🔴 '취소처리' 행 전체 빨간색 표시 (A:M)
    if not export_df.empty:
      red_fmt = wb.add_format({"bg_color": "#FFC7CE"})
      last_row = len(export_df)  # 1-based data rows + header at row 0
      ws.conditional_format(1, 0, last_row, 12, {  # col 0..12 (A..M)
        "type": "formula",
        "criteria": '=$M2="취소처리"',
        "format": red_fmt
      })

    # ✅ 맨 아래 요약 4줄(노란색) 추가: 한 행 띄우고 시작
    yellow_fmt_label = wb.add_format({"bg_color": "#FFF2CC", "bold": True, "border": 1})
    yellow_fmt_value = wb.add_format({"bg_color": "#FFF2CC", "num_format": "#,##0", "border": 1})
    # ✅ C열 이후는 포맷 없이 완전 빈칸으로 둡니다 (아무것도 쓰지 않음)

    start_row = (len(export_df) + 2)  # 빈 줄 하나 비우고 시작
    summary_rows = [
      ("총 상품 결제 금액", items_total),
      ("배송비", shipping_total),
      ("수수료", commission),
      ("총 합계 금액", final_total),
    ]

    for i, (label, value) in enumerate(summary_rows):
      r = start_row + i
      # A: 라벨(노란 박스)
      ws.write(r, 0, label, yellow_fmt_label)
      # B: 값(노란 박스, 숫자 포맷)
      ws.write_number(r, 1, value, yellow_fmt_value)
      # C 이후는 아무것도 쓰지 않음 → 포맷/채움 없음(빈칸 유지)

    # 열 너비 약간 보정 (선택)
    ws.set_column(0, 0, 20)  # A
    ws.set_column(1, 1, 18)  # B
    # C 이후는 기존 값 유지 (변경 없음)

  print(f"[settlement] EXCEL saved {fpath}  items_total={items_total}  ship_total={shipping_total}  commission={commission}  final={final_total}")
  return fpath, {
    "orders": len(df),
    "gross_amount": items_total,    # 총 상품 결제 금액(취소제외)
    "net_amount": items_total - commission,  # 상품 결제 금액에서 수수료 차감값 (참고)
    "delivered_rows": counts.get("delivered_rows", 0),
    "canceled_rows": counts.get("canceled_rows", 0),
    # 참고용으로 추가 값도 반환(필요 시 사용)
    "shipping_amount": shipping_total,
    "commission_amount": commission,
    "final_amount": final_total,
  }
