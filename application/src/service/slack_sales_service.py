# application/src/service/sales_service.py
# -*- coding: utf-8 -*-
"""
/sales 집계 로직
- 주문단위 배송비가 0 → '취소주문', 그 외 → '판매주문' (정산 로직과 동일 기준)
- 총매출: 전체 품목 결제금액 합
- 취소매출: '취소주문'의 품목 결제금액 합
- 판매매출: 총매출 - 취소매출
- 배송비: 비취소(canceled=F) 주문의 주문당 배송비 합
- 수수료: 판매매출 * 15% (환경변수 SETTLEMENT_COMMISSION_RATE)
- 정산금액: 판매매출 - 수수료 + 배송비
- 수량: 총/판매/취소 품목 수량
- date_type: CAFE24_DATE_TYPE (기본 'order_date')
"""
import os
import time
from typing import Dict, Any, List, Tuple, Optional, Iterable
from decimal import Decimal
from datetime import date
import requests

from application.src.service.cafe24_oauth_service import get_access_token

CAFE24_BASE_URL = os.getenv("CAFE24_BASE_URL", "").rstrip("/")
COMMISSION_RATE = float(os.getenv("SETTLEMENT_COMMISSION_RATE", "0.15"))
DATE_TYPE = os.getenv("CAFE24_DATE_TYPE", "order_date")  # "pay_date" 가능


# -------------------- 유틸 --------------------
def _to_dec(v) -> Decimal:
  if v is None: return Decimal(0)
  try: return Decimal(str(v))
  except Exception: return Decimal(0)

def _to_int(v) -> int:
  if v is None: return 0
  try: return int(Decimal(str(v)))
  except Exception: return 0

def _safe_get(url: str, params: Dict[str, Any], token: str, tries: int = 6, tag: str = "-") -> requests.Response:
  for i in range(1, tries + 1):
    try:
      print(f"[sales:{tag}] GET {url} try={i}/{tries} params={params}")
      r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=25)
      if r.status_code == 429:
        ra = int(r.headers.get("Retry-After", "1"))
        time.sleep(max(1, ra)); continue
      r.raise_for_status()
      print(f"[sales:{tag}] OK {url} status={r.status_code} bytes={len(r.content)}")
      return r
    except Exception as e:
      print(f"[sales:{tag}] ERROR {url} ({e})")
      if i == tries: raise
      time.sleep(1.2 * i)
  raise RuntimeError(f"[sales:{tag}] GET exhausted: {url}")

def first_day_of_month(today: date) -> date:
  return today.replace(day=1)

# 주문 단위 배송비 (dict/list 모두 지원)
def _order_shipping_fee(order: Dict[str, Any]) -> int:
  sfd = order.get("shipping_fee_detail")
  if isinstance(sfd, dict):
    return _to_int(sfd.get("shipping_fee") or sfd.get("total_shipping_fee") or order.get("shipping_fee"))
  if isinstance(sfd, list):
    total = 0
    for x in sfd:
      if isinstance(x, dict):
        total += _to_int(x.get("shipping_fee") or x.get("total_shipping_fee"))
    return total or _to_int(order.get("shipping_fee"))
  return _to_int(order.get("shipping_fee"))


# -------------------- 공개 함수 --------------------
def fetch_sales_summary(start_date: date, end_date: date, supply_id: Optional[str] = None) -> Dict[str, Any]:
  """
  /sales 에서 사용.
  반환:
    {
      "orders":int, "orders_sold":int, "orders_canceled":int,
      "gross_amount":int, "cancel_amount":int, "sale_amount":int,
      "shipping_amount":int, "commission_amount":int, "net_amount":int,
      "items":int, "items_sold":int, "items_canceled":int
    }
  """
  tag = hex(abs(hash(f"{start_date}-{end_date}-{supply_id}-{time.time()}")))[2:10]
  s = start_date.isoformat(); e = end_date.isoformat()
  print(f"[sales:{tag}] fetch_sales_summary {s}~{e} supplier_id={supply_id}")

  token = get_access_token()

  # 1) 총 주문 수(페이지 계획용)
  orders_count = _fetch_orders_count(s, e, supply_id, tag)
  print(f"[sales:{tag}] COUNT result={orders_count}")
  if orders_count == 0:
    summary = {
      "orders": 0, "orders_sold": 0, "orders_canceled": 0,
      "gross_amount": 0, "cancel_amount": 0, "sale_amount": 0,
      "shipping_amount": 0, "commission_amount": 0, "net_amount": 0,
      "items": 0, "items_sold": 0, "items_canceled": 0
    }
    print(f"[sales:{tag}] SUMMARY {summary}")
    return summary

  # 2) 주문/아이템 스캔(한 번에 총/취소/판매/수량/주문수까지 계산)
  scan = _collect_items_from_orders(s, e, supply_id, orders_count, tag)
  gross = int(scan["gross"])
  cancel_amount = int(scan["cancel_gross"])
  sale_amount = max(gross - cancel_amount, 0)

  # 3) 배송비(비취소 주문만, 주문당 1회)
  shipping_amount = _sum_shipping_amount(s, e, supply_id, tag)

  # 4) 수수료/정산금액
  commission_amount = int(round(sale_amount * COMMISSION_RATE))
  net = sale_amount - commission_amount + shipping_amount

  summary = {
    "orders": scan["orders_total"],
    "orders_sold": scan["orders_sold"],
    "orders_canceled": scan["orders_canceled"],

    "gross_amount": max(gross, 0),
    "cancel_amount": max(cancel_amount, 0),
    "sale_amount": max(sale_amount, 0),

    "shipping_amount": max(int(shipping_amount), 0),
    "commission_amount": max(int(commission_amount), 0),
    "net_amount": max(int(net), 0),

    "items": scan["qty_total"],
    "items_sold": scan["qty_sold"],
    "items_canceled": scan["qty_canceled"],
  }
  print(f"[sales:{tag}] SUMMARY {summary}")
  return summary


# -------------------- 내부 구현 --------------------
def _fetch_orders_count(s: str, e: str, supplier_id: Optional[str], tag: str) -> int:
  token = get_access_token()
  url = f"{CAFE24_BASE_URL}/api/v2/admin/orders/count"
  params = {"start_date": s, "end_date": e, "date_type": DATE_TYPE}
  if supplier_id: params["supplier_id"] = supplier_id
  r = _safe_get(url, params, token, tag=tag)
  payload = r.json() or {}
  return int(payload.get("count", 0))


def _collect_items_from_orders(
  s: str, e: str, supplier_id: Optional[str], count: int, tag: str
) -> Dict[str, int]:
  """
  /orders 페이징 순회
  - 주문단위 배송비 0 → 취소주문, 그 외 → 판매주문
  - items.payment_amount 합산: gross / cancel_gross (주문 분류에 따라)
  - 수량 합산: qty_total / qty_sold / qty_canceled
  - 주문수: orders_sold / orders_canceled (해당 공급사 품목이 1개라도 포함된 주문만 카운트)
  * fields 사용, 누락/빈아이템이면 fields 제거 폴백
  """
  token = get_access_token()
  url = f"{CAFE24_BASE_URL}/api/v2/admin/orders"

  limit = 1000
  offset = 0
  max_offset = 15000

  qty_total = qty_sold = qty_canceled = 0
  gross = Decimal(0)
  cancel_gross = Decimal(0)

  seen_sold = set()
  seen_canceled = set()

  item_fields = ",".join([
    "order_item_code","quantity","payment_amount",
    "product_price","option_price","additional_discount_price",
    "coupon_discount_price","app_item_discount_amount",
    "supplier_id","supply_id","supplier_code","owner_code"
  ])
  base_params = {
    "start_date": s, "end_date": e, "date_type": DATE_TYPE,
    "embed": "items",
    "fields": f"order_id,shipping_fee,shipping_fee_detail,items({item_fields})"
  }
  if supplier_id: base_params["supplier_id"] = supplier_id

  fetched = 0
  while fetched < count and offset <= max_offset:
    to_fetch = min(limit, count - fetched)
    params = dict(base_params); params.update({"limit": to_fetch, "offset": offset})

    r = _safe_get(url, params, token, tag=tag)
    data = r.json() or {}
    orders = data.get("orders") or []
    print(f"[sales:{tag}] LIST got={len(orders)} offset={offset}")

    # 폴백 조건: 주문 없음 or 배치 아이템 합 0 or 배송비/결제액 필드 부족
    batch_items_count = sum(len(o.get("items") or []) for o in orders)
    need_fallback = (not orders) or (batch_items_count == 0)
    if not need_fallback and orders:
      smp = orders[0]; smp_items = (smp.get("items") or [])
      if ("shipping_fee" not in smp and "shipping_fee_detail" not in smp) \
         or (smp_items and ("payment_amount" not in smp_items[0])):
        need_fallback = True

    if need_fallback:
      print(f"[sales:{tag}] items/shipfee missing or empty({batch_items_count}) → fallback WITHOUT fields")
      fb = {
        "start_date": s, "end_date": e, "date_type": DATE_TYPE,
        "embed": "items", "limit": to_fetch, "offset": offset
      }
      if supplier_id: fb["supplier_id"] = supplier_id
      r2 = _safe_get(url, fb, token, tag=tag)
      d2 = r2.json() or {}
      orders_src = d2.get("orders") or []
    else:
      orders_src = orders

    # 집계
    for o in orders_src:
      shipfee = _order_shipping_fee(o)
      is_canceled_order = (_to_int(shipfee) == 0)

      order_has_supplier_item = False

      for it in (o.get("items") or []):
        sid = it.get("supplier_id") or it.get("supply_id") or it.get("supplier_code") or it.get("owner_code")
        if supplier_id and sid and str(sid) != str(supplier_id):
          continue

        order_has_supplier_item = True

        qty = _to_int(it.get("quantity"))
        qty_total += qty

        pay = it.get("payment_amount")
        if pay is None:
          base = _to_dec(it.get("product_price")) + _to_dec(it.get("option_price"))
          disc = _to_dec(it.get("additional_discount_price")) + _to_dec(it.get("coupon_discount_price")) + _to_dec(it.get("app_item_discount_amount"))
          pay = (base - disc) * qty

        gross += _to_dec(pay)

        if is_canceled_order:
          qty_canceled += qty
          cancel_gross += _to_dec(pay)
        else:
          qty_sold += qty

      # 주문 카운트(해당 공급사 품목이 하나라도 있던 주문만)
      if order_has_supplier_item:
        oid = o.get("order_id")
        if is_canceled_order:
          seen_canceled.add(oid)
        else:
          seen_sold.add(oid)

    fetched += len(orders_src)
    if len(orders_src) < to_fetch: break
    offset += len(orders_src)
    if offset > max_offset:
      print(f"[sales:{tag}] WARNING offset>15000, remaining orders will be skipped")
      break

  result = {
    "orders_sold": len(seen_sold),
    "orders_canceled": len(seen_canceled),
    "orders_total": len(seen_sold) + len(seen_canceled),

    "qty_total": qty_total,
    "qty_sold": qty_sold,
    "qty_canceled": qty_canceled,

    "gross": int(gross),
    "cancel_gross": int(cancel_gross),
  }
  print(f"[sales:{tag}] COLLECT {result}")
  return result


def _sum_shipping_amount(s: str, e: str, supplier_id: Optional[str], tag: str) -> int:
  """비취소(canceled=F) 주문의 주문당 배송비 합"""
  token = get_access_token()
  # count
  cnt_url = f"{CAFE24_BASE_URL}/api/v2/admin/orders/count"
  cnt_params = {"start_date": s, "end_date": e, "date_type": DATE_TYPE, "canceled": "F"}
  if supplier_id: cnt_params["supplier_id"] = supplier_id
  r_cnt = _safe_get(cnt_url, cnt_params, token, tag=tag)
  total = _to_int((r_cnt.json() or {}).get("count"))

  if total == 0:
    print(f"[sales:{tag}] SHIP no orders")
    return 0

  url = f"{CAFE24_BASE_URL}/api/v2/admin/orders"
  limit = 1000
  offset = 0
  acc = 0

  while offset < total:
    to_fetch = min(limit, total - offset)
    params = {
      "start_date": s, "end_date": e, "date_type": DATE_TYPE,
      "canceled": "F", "limit": to_fetch, "offset": offset
    }
    if supplier_id: params["supplier_id"] = supplier_id

    r = _safe_get(url, params, token, tag=tag)
    orders = (r.json() or {}).get("orders") or []
    for o in orders:
      acc += _order_shipping_fee(o)

    offset += len(orders)
    if len(orders) < to_fetch: break

  print(f"[sales:{tag}] SHIP_AMOUNT total={acc}")
  return int(acc)
