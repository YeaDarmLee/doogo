# application/src/service/cafe24_products_service.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import os
from typing import Dict, Any, List, Optional
from datetime import datetime
from pytz import timezone

from application.src.service import slackService as _slack_svc
from application.src.repositories.SupplierListRepository import SupplierListRepository
from slack_sdk import WebClient as _SlackClient

_slack_client = getattr(_slack_svc, "client", None)
_KST = timezone('Asia/Seoul')

SLACK_BROADCAST_CHANNEL_ID = os.getenv("SLACK_BROADCAST_CHANNEL_ID", "").strip()

class Cafe24ProductsService:
  """
  Cafe24 '상품 등록' 웹훅 처리:
    - payload 파싱(관대한 키 수용)
    - supplier_code CSV → SupplierList.channelId 매핑
    - 매핑된 채널들로 Slack 메시지 전송
    - 매핑이 0건이면 .env의 SLACK_BROADCAST_CHANNEL_ID/NAME 채널로 폴백
  """
  def __init__(self, slack_channel_env: str = "SLACK_BROADCAST_CHANNEL_ID"):
    self.fallback_channel = os.getenv(slack_channel_env, "").strip()
    # 이름으로만 설정된 경우를 대비해 lazy-resolve
    self.fallback_channel_name = os.getenv("SLACK_BROADCAST_CHANNEL_NAME", "").strip()

  # ----------------------------
  # 유틸
  # ----------------------------
  def _coalesce(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    # 다양한 래퍼 키를 관대하게 수용
    return payload.get("resource") or payload.get("data") or payload.get("product") or {}

  def _parse_dt_kst(self, ts: Optional[str]) -> datetime:
    # ISO(예: 2025-09-08T10:00:00+09:00) 또는 Z → +00:00 치환
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

  def _resolve_channel_id_by_name(self, name: str) -> Optional[str]:
    """
    공용 채널 이름만 설정된 경우 ID 조회.
    - 공개 채널 또는 봇이 멤버인 비공개 채널만 탐색 가능
    """
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

  # ----------------------------
  # 메시지 생성/전송
  # ----------------------------
  def _build_message(self, d: Dict[str, Any], topic: str) -> str:
    name = d.get("product_name") or d.get("name") or "-"
    code = d.get("product_code") or d.get("code") or ""
    no   = d.get("product_no") or d.get("id") or ""
    sku  = d.get("custom_product_code") or d.get("sku") or ""
    supplier_codes = d.get("supplier_code") or ""
    price = d.get("selling_price") or d.get("price") or d.get("retail_price") or ""
    stock = d.get("stock") or d.get("total_stock") or d.get("quantity") or d.get("qty") or ""
    created = (
      d.get("created_at") or d.get("regist_date") or
      d.get("insert_date") or d.get("updated_at")
    )
    created_kst = self._parse_dt_kst(created)

    id_line_parts = []
    if code: id_line_parts.append(f"코드:{code}")
    if no:   id_line_parts.append(f"번호:{no}")
    if sku:  id_line_parts.append(f"SKU:{sku}")

    lines = []
    lines.append(f"*[Cafe24]* :receipt: *새로운 상품이 등록되었습니다.*")
    lines.append(f"```- 상품명: {name}")
    if id_line_parts:
      lines.append(f"- 식별자: " + " / ".join(id_line_parts))
    if supplier_codes:
      lines.append(f"- 공급사 코드: {supplier_codes}")
    if price not in ("", None, 0, "0", "0.00"):
      lines.append(f"- 판매가: {self._fmt_money(price)}")
    if stock not in ("", None):
      lines.append(f"- 재고: {stock}")
    lines.append(f"- 등록시각: {created_kst.strftime('%Y-%m-%d %H:%M:%S %Z')}```")
    return "\n".join(lines)

  def _post_to_channel(self, channel_id: str, text: str):
    cli = self._ensure_slack_client()
    cli.chat_postMessage(channel=channel_id, text=text)

  # ----------------------------
  # 엔트리 포인트
  # ----------------------------
  def notify_product_created(self, payload: Dict[str, Any], topic: str):
    d = self._coalesce(payload)
    supplier_codes = d.get("supplier_code") or ""
    msg = self._build_message(d, topic or "products/created")

    self._post_to_channel(SLACK_BROADCAST_CHANNEL_ID, msg)
    try:
      supplier = SupplierListRepository.findBySupplierCode(supplier_codes)
      self._post_to_channel(supplier.channelId, msg)
    except Exception as e:
      # 로깅은 Flask logger에 맡기는 편이 깔끔하지만 여기선 안전하게 print
      print(f"[products.notify][fail] ch={supplier_codes} err={e}")
