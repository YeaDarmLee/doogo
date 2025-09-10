# application/src/service/sales_service.py
# -*- coding: utf-8 -*-
"""
/sales 집계 로직 (Count → Orders 페이징 → items.payment_amount 합산)
- supplier_id: 채널 매핑에서 받은 supplierCode 를 그대로 전달
- 주문건수: /api/v2/admin/orders/count 의 count
- 총매출: /api/v2/admin/orders?embed=items 의 items.payment_amount 합산
- 정산예상: 총매출 * 0.85 (원단위 반올림)
- 판매수량: items.quantity 합
- print 로그 촘촘히 포함
"""

import os
import time
from typing import Dict, Any, List, Tuple, Optional, Iterable
from decimal import Decimal, InvalidOperation
from datetime import date, datetime
import requests

from application.src.service.cafe24_oauth_service import get_access_token

CAFE24_BASE_URL = os.getenv("CAFE24_BASE_URL", "").rstrip("/")


# -------------------- 유틸 --------------------
def _to_dec(v) -> Decimal:
  if v is None:
    return Decimal(0)
  try:
    return Decimal(str(v))
  except Exception:
    return Decimal(0)

def _to_int(v) -> int:
  if v is None:
    return 0
  try:
    return int(Decimal(str(v)))
  except Exception:
    return 0

def _chunked(it: Iterable[Any], size: int) -> Iterable[List[Any]]:
  buf: List[Any] = []
  for x in it:
    buf.append(x)
    if len(buf) >= size:
      yield buf
      buf = []
  if buf:
    yield buf

def _safe_get(url: str, params: Dict[str, Any], token: str, tries: int = 6, tag: str = "-") -> requests.Response:
  for i in range(1, tries + 1):
    try:
      print(f"[sales:{tag}] GET {url} try={i}/{tries} params={params}")
      r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=25)
      if r.status_code == 429:
        ra = int(r.headers.get("Retry-After", "1"))
        wait = max(1, ra)
        print(f"[sales:{tag}] 429 Too Many Requests → sleep {wait}s")
        time.sleep(wait); continue
      r.raise_for_status()
      print(f"[sales:{tag}] OK {url} status={r.status_code} bytes={len(r.content)}")
      return r
    except Exception as e:
      print(f"[sales:{tag}] ERROR {url} ({e})")
      if i == tries:
        raise
      time.sleep(1.2 * i)
  raise RuntimeError(f"[sales:{tag}] GET exhausted: {url}")


# -------------------- 공개 함수 --------------------
def first_day_of_month(today: date) -> date:
  return today.replace(day=1)


def fetch_sales_summary(start_date: date, end_date: date, supply_id: Optional[str] = None) -> Dict[str, Any]:
  """
  /sales 에서 사용.
  - supply_id: 채널에서 가져온 supplierCode (없으면 전체 집계)
  - 반환: {"orders":int, "gross_amount":int, "net_amount":int, "items":int}
  """
  tag = hex(abs(hash(f"{start_date}-{end_date}-{supply_id}-{time.time()}")))[2:10]
  s = start_date.isoformat()
  e = end_date.isoformat()
  print(f"[sales:{tag}] fetch_sales_summary {s}~{e} supplier_id={supply_id}")

  # 1) 주문 수
  orders_count = _fetch_orders_count(s, e, supply_id, tag)
  print(f"[sales:{tag}] COUNT result={orders_count}")
  if orders_count == 0:
    summary = {"orders": 0, "gross_amount": 0, "net_amount": 0, "items": 0}
    print(f"[sales:{tag}] SUMMARY {summary}")
    return summary

  # 2) 주문 목록 페이징으로 품목코드/수량/금액 수집 (items.payment_amount 사용)
  _, total_qty, inline_gross = _collect_items_from_orders(s, e, supply_id, orders_count, tag)

  # 3) 정산예상 = 85%
  gross = int(inline_gross)
  net = int((Decimal(gross) * Decimal("0.85")).quantize(Decimal("1.")))

  summary = {
    "orders": int(orders_count),
    "gross_amount": gross,
    "net_amount": net,
    "items": int(total_qty),
  }
  print(f"[sales:{tag}] SUMMARY {summary}")
  return summary


def fetch_order_list(start_date: date, end_date: date, supply_id: Optional[str] = None) -> List[Dict[str, Any]]:
  """
  /sales_detail 용 간단 리스트.
  - supplier_id 로 필터한 주문을 날짜 구간 전체 받아서 표시용 필드만 추림
  - order_price 가 없으면 payment_amount 로 대체
  """
  tag = hex(abs(hash(f"detail-{start_date}-{end_date}-{supply_id}-{time.time()}")))[2:10]
  s = start_date.isoformat(); e = end_date.isoformat()
  token = get_access_token()

  url = f"{CAFE24_BASE_URL}/api/v2/admin/orders"
  limit = 1000
  offset = 0
  max_offset = 15000

  results: List[Dict[str, Any]] = []

  while True:
    params = {
      "start_date": s,
      "end_date": e,
      "date_type": "pay_date",
      "limit": limit,
      "offset": offset,
      # 표시용 필드만 (items 불필요)
      "fields": "order_id,order_date,order_price,payment_amount,payment_method,payment_method_name,paymethod,pg_name",
    }
    if supply_id:
      params["supplier_id"] = supply_id

    r = _safe_get(url, params, token, tag=tag)
    data = r.json() or {}
    orders = data.get("orders") or []
    print(f"[sales:{tag}] LIST detail got={len(orders)} offset={offset}")

    for o in orders:
      pay_method = (
        o.get("payment_method")
        or o.get("payment_method_name")
        or o.get("paymethod")
        or o.get("pg_name")
        or "-"
      )
      results.append({
        "order_id": o.get("order_id"),
        "order_date": o.get("order_date"),
        "order_price": (o.get("order_price") or o.get("payment_amount")),
        "payment_amount": o.get("payment_amount"),
        "payment_method": pay_method,
      })

    got = len(orders)
    if got < limit or offset > max_offset:
      break
    offset += got

  print(f"[sales:{tag}] LIST detail total={len(results)}")
  return results


# -------------------- 내부 구현 --------------------
def _fetch_orders_count(s: str, e: str, supplier_id: Optional[str], tag: str) -> int:
  token = get_access_token()
  url = f"{CAFE24_BASE_URL}/api/v2/admin/orders/count"
  params = {"start_date": s, "end_date": e, "date_type": "pay_date"}
  if supplier_id:
    params["supplier_id"] = supplier_id
  r = _safe_get(url, params, token, tag=tag)
  payload = r.json() or {}
  return int(payload.get("count", 0))


def _collect_items_from_orders(s: str, e: str, supplier_id: Optional[str], count: int, tag: str) -> Tuple[List[str], int, int]:
  """
  /orders 를 limit=1000, offset 증가로 돌려서
  - items.order_item_code 모으기 (디버깅용 유지)
  - items.quantity 합산하기
  - items.payment_amount 합산하기 (핵심)
  Fallback: fields 로 items 가 비면 fields 제거로 재시도
  """
  token = get_access_token()
  url = f"{CAFE24_BASE_URL}/api/v2/admin/orders"

  limit = 1000
  offset = 0
  max_offset = 15000

  all_codes: List[str] = []
  total_qty = 0
  inline_gross = Decimal(0)
  fetched = 0

  while fetched < count and offset <= max_offset:
    to_fetch = min(limit, count - fetched)
    params = {
      "start_date": s,
      "end_date": e,
      "date_type": "pay_date",
      "embed": "items",
      "fields": "order_id,items(order_item_code,quantity)",  # 1차: 가벼운 응답
      "limit": to_fetch,
      "offset": offset,
    }
    if supplier_id:
      params["supplier_id"] = supplier_id

    r = _safe_get(url, params, token, tag=tag)
    data = r.json() or {}
    orders = data.get("orders") or []
    print(f"[sales:{tag}] LIST got={len(orders)} offset={offset}")

    # 기본 소스는 1차 응답
    orders_src = orders

    # 1차 응답에서 items 스캔
    got_codes, got_qty = _scan_items(orders)
    if got_codes == 0:
      # ✔ 폴백: fields 제거로 재호출
      print(f"[sales:{tag}] items missing → fallback WITHOUT fields")
      fb = {
        "start_date": s, "end_date": e, "date_type": "pay_date",
        "embed": "items", "limit": to_fetch, "offset": offset
      }
      if supplier_id:
        fb["supplier_id"] = supplier_id
      r2 = _safe_get(url, fb, token, tag=tag)
      d2 = r2.json() or {}
      orders2 = d2.get("orders") or []
      got_codes, got_qty = _scan_items(orders2)
      orders_src = orders2  # 🔧 핵심: 폴백 응답을 소스로 사용

    # ✅ 코드/수량 수집
    all_codes.extend([c for c in _iter_item_codes(orders_src)])
    total_qty += got_qty

    # ✅ 금액 집계: items.payment_amount 우선, 없으면 보정 계산
    for o in orders_src:
      for it in (o.get("items") or []):
        # 공급사 매치: supplier_id / supply_id / supplier_code 중 하나라도 같으면 포함
        sid = it.get("supplier_id") or it.get("supply_id") or it.get("supplier_code")
        if supplier_id and sid and str(sid) != str(supplier_id):
          continue

        pay = it.get("payment_amount")
        if pay is None:
          # 보정: (상품가+옵션가-할인들) * 수량
          base = _to_dec(it.get("product_price")) + _to_dec(it.get("option_price"))
          disc = _to_dec(it.get("additional_discount_price")) + _to_dec(it.get("coupon_discount_price")) + _to_dec(it.get("app_item_discount_amount"))
          qty = _to_int(it.get("quantity"))
          pay = (base - disc) * qty
        inline_gross += _to_dec(pay)

    fetched += len(orders)
    if len(orders) < to_fetch:
      break
    offset += len(orders)
    if offset > max_offset:
      print(f"[sales:{tag}] WARNING offset>15000, remaining orders will be skipped")
      break

  print(f"[sales:{tag}] COLLECT items codes={len(all_codes)} qty={total_qty} inline_gross={int(inline_gross)}")
  return all_codes, total_qty, int(inline_gross)


def _scan_items(orders: List[Dict[str, Any]]) -> Tuple[int, int]:
  """주문 리스트에서 items 수량/코드 스캔 (로그 집계용 리턴)"""
  codes = 0; qty_sum = 0
  for o in orders:
    its = o.get("items") or []
    for it in its:
      if it.get("order_item_code"):
        codes += 1
      qty_sum += _to_int(it.get("quantity"))
  return codes, qty_sum


def _iter_item_codes(orders: List[Dict[str, Any]]):
  for o in orders:
    its = o.get("items") or []
    for it in its:
      code = it.get("order_item_code")
      if code:
        yield str(code)
