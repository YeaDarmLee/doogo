# -*- coding: utf-8 -*-
from __future__ import annotations
import os
from typing import Dict, Any, Optional, List
from datetime import datetime
from pytz import timezone

from application.src.repositories.SupplierListRepository import SupplierListRepository

from application.src.utils.slack_utils import post_text
from application.src.utils.cafe24_utils import coalesce

_KST = timezone('Asia/Seoul')
SLACK_BROADCAST_CHANNEL_ID = os.getenv("SLACK_BROADCAST_CHANNEL_ID", "").strip()

class Cafe24SuppliersService:
  """
  Cafe24 '공급사 등록/변경' 웹훅 처리:
    - payload 파싱
    - supplier_code → SupplierList.channelId 매핑
    - 매핑된 채널(또는 브로드캐스트)로 Slack 알림
    - (선택) 향후 upsert 로직으로 DB 동기화 확장 가능
  """
  def __init__(self, slack_channel_env: str = "SLACK_BROADCAST_CHANNEL_ID"):
    self.fallback_channel = os.getenv(slack_channel_env, "").strip()
    self.fallback_channel_name = os.getenv("SLACK_BROADCAST_CHANNEL_NAME", "").strip()

  # ---------- 파싱 ----------
  def _extract_meta(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    d = coalesce(payload)
    return {
      "supplier_code": d.get("supplier_code") or "",
      "supplier_name": d.get("supplier_name") or "",
      "status": d.get("status") or "",
      "use_supplier": d.get("use_supplier") or "",
      "supplier_type": d.get("supplier_type") or "",
      "payment_type": d.get("payment_type") or "",
      "commission": d.get("commission") or "",
      "payment_period": d.get("payment_period") or "",
      "mall_id": d.get("mall_id") or "",
      "event_shop_no": d.get("event_shop_no") or "",
      "ts_kst": datetime.utcnow().astimezone(_KST),
    }

  def _build_message(self, m: Dict[str, Any], topic: str) -> str:
    lines = []
    lines.append(f"*[Cafe24]* :speaker: *공급사 등록/갱신*")
    lines.append(f"```- 공급사 코드: {m['supplier_code'] or '-'}")
    if m["supplier_name"]:
      lines.append(f"- 공급사명: {m['supplier_name']}")
    if m["status"]:
      lines.append(f"- 상태: {m['status']}")
    if m["use_supplier"]:
      lines.append(f"- 사용여부: {m['use_supplier']}")
    if m["supplier_type"]:
      lines.append(f"- 유형: {m['supplier_type']}")
    if m["payment_type"]:
      lines.append(f"- 정산방식: {m['payment_type']} / 주기:{m['payment_period'] or '-'} / 수수료:{m['commission'] or '-'}")
    if m["mall_id"]:
      lines.append(f"- 몰: {m['mall_id']} (shop_no: {m['event_shop_no']})")
    lines.append(f"- 수신시각: {m['ts_kst'].strftime('%Y-%m-%d %H:%M:%S %Z')}```")
    return "\n".join(lines)

  # ---------- 엔트리 ----------
  def notify_supplier_created(self, payload: Dict[str, Any], topic: str):
    d = self._extract_meta(payload)
    text = self._build_message(d, topic)
    
    # 신규 공급사 등록 알림
    post_text(SLACK_BROADCAST_CHANNEL_ID, text)
