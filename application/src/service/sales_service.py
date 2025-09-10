# application/src/service/sales_service.py
# -*- coding: utf-8 -*-

from datetime import date
from typing import Dict, Any, Optional, Tuple
import os
import time
import requests
from decimal import Decimal, InvalidOperation
from application.src.service.cafe24_oauth_service import get_access_token

CAFE24_BASE_URL = os.getenv("CAFE24_BASE_URL")


# -----------------------------
# 유틸: 안전 숫자 변환
# -----------------------------
def _to_decimal(v: Any) -> Decimal:
  """문자/숫자/None을 Decimal로 안전 변환"""
  if v is None:
    return Decimal(0)
  if isinstance(v, Decimal):
    return v
  if isinstance(v, (int, float)):
    return Decimal(str(v))
  if isinstance(v, str):
    s = v.strip().replace(",", "")
    if s == "":
      return Decimal(0)
    try:
      return Decimal(s)
    except InvalidOperation:
      return Decimal(0)
  return Decimal(0)

def _to_int(v: Any) -> int:
  """정수로 안전 변환 (문자 '10', Decimal 등 허용)"""
  if v is None:
    return 0
  if isinstance(v, int):
    return v
  if isinstance(v, float):
    return int(v)
  if isinstance(v, str):
    s = v.strip().replace(",", "")
    if s == "":
      return 0
    try:
      return int(Decimal(s))
    except InvalidOperation:
      return 0
  if isinstance(v, Decimal):
    return int(v)
  return 0


# -----------------------------
# 안전 GET (429 Retry 포함)
# -----------------------------
def _safe_get(url: str, token: str, params: Optional[Dict[str, Any]] = None, *, max_retry: int = 3) -> requests.Response:
  """
  Cafe24 API 호출 시 429(Too Many Requests) 대응을 포함한 안전 GET.
  Retry-After 헤더가 있으면 해당 초만큼 대기 후 재시도.
  """
  for _ in range(max_retry):
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=10)
    if r.status_code == 429:
      ra = int(r.headers.get("Retry-After", "1"))
      time.sleep(max(ra, 1))
      continue
    r.raise_for_status()
    return r
  # 마지막 시도도 실패하면 예외
  r.raise_for_status()
  return r  # pragma: no cover


# -----------------------------
# 공개 API
# -----------------------------
def fetch_sales_summary(start_date: date, end_date: date, supply_id: Optional[str] = None) -> Dict:
  """
  Cafe24 Orders API를 호출하여 start_date~end_date 구간 매출 요약을 반환.
  - supply_id 가 주어지면 '공급사별' 집계를 수행(주문 품목 embed 사용, 정확/최적화)
  - supply_id 가 없으면 몰 전체 집계(주문 리스트만 사용, 빠름)

  반환 스키마:
  {
    "orders": int,         # 주문건수
    "gross_amount": int,   # 총매출(원) - order_price 없으면 payment_amount로 대체
    "net_amount": int,     # 실결제/정산 기준(원) - payment_amount
    "items": int           # 판매수량(개) - 공급사 기준일 때 품목 qty 합계, 전체 집계는 0
  }
  """
  token = get_access_token()

  if supply_id:
    # 공급사 기준: embed=items 로 주문+품목을 한 번에 받아 정확 집계
    ord_cnt, gross_dec, net_dec, items_total = _aggregate_by_supply(
      token, start_date, end_date, supply_id
    )
    return {
      "orders": ord_cnt,
      "gross_amount": int(gross_dec),
      "net_amount": int(net_dec),
      "items": items_total
    }

  # 전체 집계(빠름): 주문 리스트만 사용
  ord_cnt, gross_dec, net_dec = _aggregate_overall(token, start_date, end_date)
  return {
    "orders": ord_cnt,
    "gross_amount": int(gross_dec),
    "net_amount": int(net_dec),
    "items": 0  # 전체 집계에서는 품목 호출 생략(경량). 필요 시 확장 가능
  }


def first_day_of_month(today: date) -> date:
  return today.replace(day=1)


# -----------------------------
# 내부 구현: 전체 집계(주문 리스트 기반)
# -----------------------------
def _aggregate_overall(token: str, start_date: date, end_date: date) -> Tuple[int, Decimal, Decimal]:
  """
  몰 전체 집계: 주문 리스트에서 payment_amount와 order_price를 합산.
  order_price가 응답에 없으면 총매출(gross)은 payment_amount로 대체.
  """
  base = f"{CAFE24_BASE_URL}/api/v2/admin/orders"
  params = {
    "start_date": start_date.strftime("%Y-%m-%d"),
    "end_date": end_date.strftime("%Y-%m-%d"),
    "limit": 50,
    # 금액 필드를 반드시 요청
    "fields": "order_id,order_date,order_price,payment_amount,product_count"
  }

  total_orders = 0
  gross_amount_dec = Decimal(0)
  net_amount_dec = Decimal(0)

  next_url = base
  next_params = params

  while True:
    r = _safe_get(next_url, token, next_params)
    payload = r.json()

    orders = payload.get("orders", []) or []
    total_orders += len(orders)

    for o in orders:
      # 실결제/정산 기준(최종금액)
      net_amount_dec += _to_decimal(o.get("payment_amount"))
      # 총매출: order_price가 없으면 payment_amount로 대체
      gross_amount_dec += _to_decimal(o.get("order_price", o.get("payment_amount")))

    # pagination
    next_link = _find_next_link(payload)
    if not next_link:
      break
    next_url, next_params = next_link, None  # href에 쿼리 포함됨

  return total_orders, gross_amount_dec, net_amount_dec


# -----------------------------
# 내부 구현: 공급사별 집계(주문 품목 기반, embed=items)
# -----------------------------
def _aggregate_by_supply(token: str, start_date: date, end_date: date, supply_id: str) -> Tuple[int, Decimal, Decimal, int]:
  """
  공급사 기준으로 정확 집계(최적화 버전):
  - /admin/orders?embed=items 로 주문 + 품목을 한 번에 받아 품목 단위로 필터링/합산.
  - 추가 /items 호출이 없어 레이트리밋(429) 위험이 크게 줄어듦.
  - 금액 계산:
    * item.payment_amount 가 있으면 "해당 라인 합계"로 간주하여 그대로 사용
    * 없으면 price(또는 sale_price) * quantity 로 계산
    * 공급사별 총매출(gross)은 정산금액(net)과 동일하게 처리(주문 단위 order_price 배분은 불확실)
  """
  base_orders = f"{CAFE24_BASE_URL}/api/v2/admin/orders"
  params = {
    "start_date": start_date.strftime("%Y-%m-%d"),
    "end_date": end_date.strftime("%Y-%m-%d"),
    "limit": 50,
    "embed": "items",  # ← 핵심: 주문 응답에 items 포함
    # 주문 레벨 금액을 굳이 쓰지 않으므로 fields는 최소화 가능하나,
    # 디버깅/확인을 위해 유지해도 무방
    "fields": "order_id,order_date,order_price,payment_amount"
  }

  total_orders = 0
  gross_amount_dec = Decimal(0)
  net_amount_dec = Decimal(0)
  items_total = 0

  next_url = base_orders
  next_params = params

  # 품목에서 공급사 식별자 후보 키 (몰/버전에 따라 달라질 수 있음)
  owner_keys = ("supply_id", "supplier_id", "supplier_code", "owner_code", "vendor_id", "provider_id")

  while True:
    r = _safe_get(next_url, token, next_params)  # 429 대응 포함
    payload = r.json()
    orders = payload.get("orders", []) or []

    for o in orders:
      items = o.get("items") or []  # embed=items 결과
      has_supply = False

      for it in items:
        # 공급사 식별자 추출 (첫 번째로 존재하는 키 사용)
        owner = None
        for k in owner_keys:
          v = it.get(k)
          if v is not None and str(v) != "":
            owner = v
            break

        if str(owner) != str(supply_id):
          continue

        has_supply = True
        qty = _to_int(it.get("quantity"))

        # 금액 확정:
        # - item.payment_amount 가 있으면 해당 라인 합계로 간주(곱셈 X)
        # - 없으면 price(or sale_price) * qty
        item_paid = _to_decimal(it.get("payment_amount"))
        if item_paid == 0:
          price_each = _to_decimal(it.get("price") or it.get("sale_price") or 0)
          item_paid = price_each * qty

        items_total += qty
        net_amount_dec += item_paid
        gross_amount_dec += item_paid  # 주문단위 총매출 배분이 필요하면 여기서 교체

      # 해당 공급사 품목이 1개 이상 포함된 주문만 주문건수로 카운트
      if has_supply:
        total_orders += 1

    # pagination
    next_link = _find_next_link(payload)
    if not next_link:
      break
    next_url, next_params = next_link, None  # href에 쿼리 포함됨

  return total_orders, gross_amount_dec, net_amount_dec, items_total

def fetch_order_list(start_date: date, end_date: date, supply_id: Optional[str] = None) -> list[dict]:
  """
  Cafe24 Orders API에서 start_date~end_date 주문 리스트 반환.
  supply_id 있으면 해당 공급사 품목이 있는 주문만 필터링.
  """
  token = get_access_token()
  base = f"{CAFE24_BASE_URL}/api/v2/admin/orders"
  params = {
    "start_date": start_date.strftime("%Y-%m-%d"),
    "end_date": end_date.strftime("%Y-%m-%d"),
    "limit": 50,  # 페이지네이션은 슬랙에서 slice로 처리하므로 여긴 넉넉히 가져옵니다.
    "embed": "items",
    # 결제수단 후보 포함(몰마다 다를 수 있어 여러 후보를 요청)
    "fields": (
      "order_id,order_date,order_price,payment_amount,"
      "payment_method,payment_method_name,paymethod,pg_name"
    )
  }

  results = []
  next_url, next_params = base, params
  owner_keys = ("supply_id", "supplier_id", "supplier_code", "owner_code", "vendor_id", "provider_id")

  while True:
    r = _safe_get(next_url, token, next_params)
    payload = r.json()
    orders = payload.get("orders", []) or []

    for o in orders:
      if supply_id:
        items = o.get("items") or []
        ok = False
        for it in items:
          owner = None
          for k in owner_keys:
            if it.get(k):
              owner = it.get(k)
              break
          if str(owner) == str(supply_id):
            ok = True
            break
        if not ok:
          continue

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
        "order_price": o.get("order_price"),
        "payment_amount": o.get("payment_amount"),
        "payment_method": pay_method,
      })

    next_link = _find_next_link(payload)
    if not next_link:
      break
    next_url, next_params = next_link, None

  return results


# -----------------------------
# 공통: next 링크 추출
# -----------------------------
def _find_next_link(payload: Dict[str, Any]) -> Optional[str]:
  links = payload.get("links") or []
  for l in links:
    if l.get("rel") == "next":
      href = l.get("href")
      if href:
        return href
  return None
