# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json
from typing import Dict, Any, List, Optional
from datetime import datetime
from pytz import timezone

# Slack client (slackService 우선, 없으면 토큰으로 직접 생성)
try:
  from application.src.service import slackService as _slack_svc
  _slack_client = getattr(_slack_svc, "client", None)
except Exception:
  _slack_client = None

try:
  from slack_sdk import WebClient as _SlackClient
except Exception:
  _SlackClient = None

# Repository (공급사 매핑)
try:
  from application.src.repositories.SupplierListRepository import SupplierListRepository
except Exception:
  SupplierListRepository = None

# 폴백 직접 쿼리용 (선택)
try:
  from sqlalchemy import select
  from application.src.models import db
  from application.src.models.SupplierList import SupplierList
except Exception:
  db, SupplierList, select = None, None, None

_KST = timezone('Asia/Seoul')
SLACK_BROADCAST_CHANNEL_ID = os.getenv("SLACK_BROADCAST_CHANNEL_ID", "").strip()

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
    lines.append(f"[Cafe24] 🔔 신규주문이 발생하였습니다.")
    lines.append(f"- 주문번호: {meta['order_id']}")
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
      lines.append(f"- 공급사 코드: {meta['supplier_codes']}")

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
        print(f"[orders.notify][fail] ch={supplier.channelId} err={e}")
