# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json
from typing import Dict, Any, List, Optional
from datetime import datetime
from pytz import timezone

from application.src.repositories.SupplierListRepository import SupplierListRepository

from application.src.service import slack_service as SU
from application.src.utils.cafe24_utils import (
  coalesce, parse_kst, fmt_money, humanize_event, humanize_shipping
)

class Cafe24OrdersService:
  """
  Cafe24 주문 이벤트 처리:
    - payload 파싱(관대한 키)
    - supplier_code CSV → SupplierList.channelId 매핑
    - 매핑된 채널들로 Slack 메시지 전송
    - 매핑이 없으면 .env의 SLACK_BROADCAST_CHANNEL_ID/NAME 채널로 폴백
  """
  
  # ------------- 주문 메타/아이템 ----------
  def _extract_order_meta(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    d = coalesce(payload)
    order_id = d.get("order_id") or d.get("id") or d.get("order_no") or ""
    paid_flag = (d.get("paid") == "T") or str(d.get("paid") or "").lower() in ("true", "t", "1")

    # 결제완료면 payment_date 우선, 아니면 order_date
    ts = d.get("payment_date") if paid_flag else d.get("order_date") or d.get("ordered_at") or d.get("created_at")
    dt_kst = parse_kst(ts)

    # 총액 후보: actual_payment_amount(실결제) → order_price_amount(주문금액)
    total = d.get("actual_payment_amount")
    try:
      if total is None or float(total) <= 0:
        total = d.get("order_price_amount") or 0
    except Exception:
      total = d.get("order_price_amount") or total or 0

    return {
      "order_id": order_id,
      "ordered_at": dt_kst,
      "paid": paid_flag,
      "total": total,
      "currency": d.get("currency") or "KRW",
      "place": d.get("order_place_name") or d.get("order_place_id") or "",
      "buyer_name": d.get("buyer_name") or "",
      "buyer_email": d.get("buyer_email") or "",
      "supplier_codes": d.get("supplier_code") or "",  # CSV
    }

  def _extract_items(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    d = coalesce(payload)

    # 1) 배열 형태(items/line_items)가 있으면 우선 사용
    items = d.get("items") or d.get("line_items")
    if isinstance(items, list) and items:
      out = []
      for it in items:
        name = it.get("product_name") or it.get("name") or ""
        qty = it.get("quantity") or it.get("qty") or 1
        amt = (
          it.get("sale_price") or it.get("price") or
          it.get("product_price") or it.get("item_price")
        )
        code = it.get("product_code") or it.get("code") or ""
        out.append({"name": name, "qty": qty, "amt": amt, "code": code})
      return out

    # 2) 더미/일부 API: CSV 문자열 조합
    names = (d.get("ordering_product_name") or "").split(",") if d.get("ordering_product_name") else []
    codes = (d.get("ordering_product_code") or "").split(",") if d.get("ordering_product_code") else []
    out = []
    m = max(len(names), len(codes))
    for i in range(m):
      out.append({
        "name": names[i].strip() if i < len(names) else "",
        "qty": 1,
        "amt": None,
        "code": codes[i].strip() if i < len(codes) else ""
      })
    return out

  # ------------- 메시지 ----------
  def _build_message(self, meta: Dict[str, Any], items: List[Dict[str, Any]], topic: str) -> str:
    lines: List[str] = []
    status = "결제완료" if meta["paid"] else "미결제"
    lines.append(f"*[Cafe24]* :bell: *신규주문이 발생하였습니다.*")
    lines.append(f"```- 주문번호: {meta['order_id']}")
    lines.append(f"- 주문시각: {meta['ordered_at'].strftime('%Y-%m-%d %H:%M:%S %Z')} ({status})")

    if items:
      lines.append("- 품목:")
      for it in items[:20]:
        amt = fmt_money(it["amt"]) if it.get("amt") not in (None, "", 0, "0", "0.00") and meta["currency"] == "KRW" else (it.get("amt") or "")
        tail = f" · 코드:{it['code']}" if it.get("code") else ""
        amt_part = f" ({amt})" if amt else ""
        lines.append(f"  · {it['name']} × {it['qty']}{amt_part}{tail}")
      if len(items) > 20:
        lines.append(f"  · 외 {len(items) - 20}건…")

    if meta["currency"] == "KRW":
      lines.append(f"- 주문합계: {fmt_money(meta['total'])}")
    else:
      lines.append(f"- 주문합계: {meta['total']} {meta['currency']}")

    if meta["place"]:
      lines.append(f"- 주문경로: {meta['place']}")
    if meta["buyer_name"]:
      lines.append(f"- 구매자: {meta['buyer_name']} ({meta['buyer_email']})")

    # 디버깅용: 공급사코드 표시(운영 중엔 빼도 됨)
    if meta.get("supplier_codes"):
      lines.append(f"- 공급사 코드: {meta['supplier_codes']}```")
    else:
      lines.append("```")

    return "\n".join(lines)

  # ------------- 엔트리 ----------
  def notify_order_created(self, payload: Dict[str, Any], topic: str):
    d = coalesce(payload)
    meta = self._extract_order_meta(payload)
    items = self._extract_items(payload)

    channels = meta.get("supplier_codes", "").split(",")

    # 메시지
    text = self._build_message(meta, items, topic)

    # 전송
    for ch in channels:
      try:
        supplier = SupplierListRepository.findBySupplierCode(ch)
        SU.post_text(supplier.channelId, text)
      except Exception as e:
        print(f"[orders.notify][fail] ch={getattr(supplier, 'channelId', None)} err={e}")

  def _extract_supplier_codes(self, payload: Dict[str, Any]) -> List[str]:
    d = coalesce(payload)
    out = set()
    # 1) 상위 CSV
    csv_codes = (d.get("supplier_code") or "")
    if csv_codes:
      for c in csv_codes.split(","):
        c = c.strip()
        if c:
          out.add(c)
    # 2) extra_info 배열 내 supplier_code
    try:
      for row in d.get("extra_info") or []:
        c = (row or {}).get("supplier_code")
        if c:
          out.add(str(c).strip())
    except Exception:
      pass
    return list(out)

  def notify_order_shipping_updated(self, payload: Dict[str, Any], topic: str):
    d = coalesce(payload)
    meta = self._extract_order_meta(payload)

    event_code = d.get("event_code") or ""
    shipping_status = d.get("shipping_status") or ""
    supplier_codes = self._extract_supplier_codes(payload)

    # 대표 품목 간단 표시
    items = self._extract_items(payload)

    # 메시지 구성
    lines: List[str] = []
    lines.append(f"*[Cafe24]* :truck: *배송상태가 변경되었습니다.*")
    lines.append(f"```- 주문번호: {meta['order_id']}")
    lines.append(f"- 업데이트 내용: {humanize_event(event_code)} (raw: {event_code})")
    if shipping_status:
      lines.append(f"- 배송상태: {humanize_shipping(shipping_status)} (raw: {shipping_status})")
    lines.append(f"- 주문시각: {meta['ordered_at'].strftime('%Y-%m-%d %H:%M:%S %Z')}")
    if items:
      lines.append("- 품목:")
      for it in items[:10]:
        nm = it.get("name") or ""
        qty = it.get("qty") or 1
        tail = f" · 코드:{it.get('code')}" if it.get("code") else ""
        lines.append(f"  · {nm} × {qty}{tail}")
      if len(items) > 10:
        lines.append(f"  · 외 {len(items) - 10}건…")
    if meta['currency'] == "KRW":
      lines.append(f"- 주문합계: {fmt_money(meta['total'])}")
    else:
      lines.append(f"- 주문합계: {meta['total']} {meta['currency']}")
    if meta["place"]:
      lines.append(f"- 주문경로: {meta['place']}")
    if meta["buyer_name"]:
      lines.append(f"- 구매자: {meta['buyer_name']} ({meta['buyer_email']})")
    if supplier_codes:
      lines.append(f"- 공급사 코드: {', '.join(supplier_codes)}```")
    else:
      lines.append("```")

    text = "\n".join(lines)

    # 공급사 채널
    for code in supplier_codes:
      try:
        supplier = SupplierListRepository.findBySupplierCode(code)
        ch_id = getattr(supplier, "channelId", None)
        if ch_id:
          SU.post_text(ch_id, text)
      except Exception as e:
        print(f"[orders.shipping][fail] supplier_code={code} err={e}")
