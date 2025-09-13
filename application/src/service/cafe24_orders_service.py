# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json
from typing import Dict, Any, List, Optional
from datetime import datetime
from pytz import timezone

from slack_sdk import WebClient as _SlackClient
from application.src.service import slackService as _slack_svc
from application.src.repositories.SupplierListRepository import SupplierListRepository

_slack_client = getattr(_slack_svc, "client", None)
_KST = timezone('Asia/Seoul')
SLACK_BROADCAST_CHANNEL_ID = os.getenv("SLACK_BROADCAST_CHANNEL_ID", "").strip()

# ─────────────────────────────────────────────────────────────
# Cafe24 코드 치환 테이블
#   - 이벤트 코드(event_code) 예: shipping_start, shipping_complete, purchase_confirm 등
#   - 배송상태(shipping_status) 예: F/M/T
# 문서 기준 대표값만 우선 반영, 필요 시 계속 확장 가능
# ─────────────────────────────────────────────────────────────
EVENT_CODE_MAP: Dict[str, str] = {
  "shipping_ready": "배송준비",
  "shipping_start": "배송시작",
  "shipping_reject": "배송거부",
  "shipping_delay": "배송지연",
  "shipping_resend": "재배송",
  "shipping_complete": "배송완료",
  "shipping_hold": "배송보류",

  # 구매/주문 관련(샘플로 자주 쓰이는 것 위주)
  "purchase_confirm": "구매확정",
  "order_cancel": "주문취소",
  "order_cancel_request": "취소요청",
  "order_exchange": "교환요청",
  "order_return": "반품요청",
}

SHIPPING_STATUS_MAP: Dict[str, str] = {
  "F": "배송전",   # Before shipping
  "M": "배송중",   # On delivery
  "T": "배송완료", # Delivered
}

def humanize_event_code(code: Optional[str]) -> str:
  if not code:
    return "-"
  c = str(code).strip().lower()
  return EVENT_CODE_MAP.get(c, code)

def humanize_shipping_status(status: Optional[str]) -> str:
  if not status:
    return "-"
  s = str(status).strip().upper()
  return SHIPPING_STATUS_MAP.get(s, status)


class Cafe24OrdersService:
  """
  Cafe24 주문 이벤트 처리:
    - payload 파싱(관대한 키)
    - supplier_code CSV → SupplierList.channelId 매핑
    - 매핑된 채널들로 Slack 메시지 전송
    - 매핑이 없으면 .env의 SLACK_BROADCAST_CHANNEL_ID/NAME 채널로 폴백
  """
  def __init__(self, slack_channel_env: str = "SLACK_BROADCAST_CHANNEL_ID"):
    self.fallback_channel = os.getenv(slack_channel_env, "").strip()
    self.fallback_channel_name = os.getenv("SLACK_BROADCAST_CHANNEL_NAME", "").strip()

  # ------------- Slack ----------
  def _ensure_slack_client(self):
    global _slack_client
    if _slack_client:
      return _slack_client
    if _SlackClient:
      token = os.getenv("SLACK_BOT_TOKEN", "").strip()
      if token:
        _slack_client = _SlackClient(token=token)
        return _slack_client
    raise RuntimeError("Slack client not available (SLACK_BOT_TOKEN 필요)")

  def _post_to_channel(self, channel_id: str, text: str):
    if not channel_id:
      # 방송 채널 미설정 시 안전하게 무시
      return
    cli = self._ensure_slack_client()
    cli.chat_postMessage(channel=channel_id, text=text)

  def _resolve_channel_id_by_name(self, name: str) -> Optional[str]:
    try:
      cli = self._ensure_slack_client()
      cursor = None
      types = "public_channel,private_channel"
      for _ in range(20):
        resp = cli.conversations_list(limit=1000, cursor=cursor, types=types)
        for ch in resp.get("channels", []):
          if ch.get("name") == name:
            return ch.get("id")
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
          break
    except Exception:
      return None
    return None

  # ------------- 파싱 유틸 ----------
  def _coalesce(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    # 다양한 래퍼를 관대하게 수용
    return payload.get("resource") or payload.get("data") or payload.get("order") or {}

  def _parse_dt_kst(self, ts: Optional[str]) -> datetime:
    if not ts:
      return datetime.utcnow().astimezone(_KST)
    try:
      ts = ts.replace("Z", "+00:00")
      dt = datetime.fromisoformat(ts)
    except Exception:
      dt = datetime.utcnow()
    return dt.astimezone(_KST)

  def _fmt_money(self, v) -> str:
    try:
      n = float(v)
      return f"{n:,.0f}원"
    except Exception:
      return str(v or "")

  # ------------- 주문 메타/아이템 ----------
  def _extract_order_meta(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    d = self._coalesce(payload)
    order_id = d.get("order_id") or d.get("id") or d.get("order_no") or ""
    paid_flag = (d.get("paid") == "T") or str(d.get("paid") or "").lower() in ("true", "t", "1")

    # 결제완료면 payment_date 우선, 아니면 order_date
    ts = d.get("payment_date") if paid_flag else d.get("order_date") or d.get("ordered_at") or d.get("created_at")
    dt_kst = self._parse_dt_kst(ts)

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
    d = self._coalesce(payload)

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
        amt = self._fmt_money(it["amt"]) if it.get("amt") not in (None, "", 0, "0", "0.00") and meta["currency"] == "KRW" else (it.get("amt") or "")
        tail = f" · 코드:{it['code']}" if it.get("code") else ""
        amt_part = f" ({amt})" if amt else ""
        lines.append(f"  · {it['name']} × {it['qty']}{amt_part}{tail}")
      if len(items) > 20:
        lines.append(f"  · 외 {len(items) - 20}건…")

    if meta["currency"] == "KRW":
      lines.append(f"- 주문합계: {self._fmt_money(meta['total'])}")
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
    d = self._coalesce(payload)
    meta = self._extract_order_meta(payload)
    items = self._extract_items(payload)

    channels = meta.get("supplier_codes", "").split(",")

    # 메시지
    text = self._build_message(meta, items, topic)
    self._post_to_channel(SLACK_BROADCAST_CHANNEL_ID, text)

    # 전송
    for ch in channels:
      try:
        supplier = SupplierListRepository.findBySupplierCode(ch)
        self._post_to_channel(supplier.channelId, text)
      except Exception as e:
        print(f"[orders.notify][fail] ch={getattr(supplier, 'channelId', None)} err={e}")

  def _extract_supplier_codes(self, payload: Dict[str, Any]) -> List[str]:
    d = self._coalesce(payload)
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

  # (이전 호환용) 이벤트 코드를 한글로
  def _map_shipping_code(self, event_code: str) -> str:
    return humanize_event_code(event_code)

  def notify_order_shipping_updated(self, payload: Dict[str, Any], topic: str):
    d = self._coalesce(payload)
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
    lines.append(f"- 업데이트 내용: {humanize_event_code(event_code)} (raw: {event_code})")
    if shipping_status:
      lines.append(f"- 배송상태: {humanize_shipping_status(shipping_status)} (raw: {shipping_status})")
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
      lines.append(f"- 주문합계: {self._fmt_money(meta['total'])}")
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

    # 1) 방송 채널
    self._post_to_channel(SLACK_BROADCAST_CHANNEL_ID, text)

    # 2) 공급사 채널
    for code in supplier_codes:
      try:
        supplier = SupplierListRepository.findBySupplierCode(code)
        ch_id = getattr(supplier, "channelId", None)
        if ch_id:
          self._post_to_channel(ch_id, text)
      except Exception as e:
        print(f"[orders.shipping][fail] supplier_code={code} err={e}")
