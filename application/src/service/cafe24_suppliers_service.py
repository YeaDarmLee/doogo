# -*- coding: utf-8 -*-
from __future__ import annotations
import os
from typing import Dict, Any, Optional, List
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

  # ---------- Slack ----------
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

  # ---------- DB 매핑 ----------
  def _find_supplier_by_id(self, supplier_id: str):
    # Repo 메서드가 있으면 사용
    if SupplierListRepository and hasattr(SupplierListRepository, "findBySupplierID"):
      try:
        return SupplierListRepository.findBySupplierID(str(supplier_id))
      except Exception:
        pass
    # 폴백: 직접 쿼리
    if db and SupplierList and select:
      try:
        stmt = select(SupplierList).where(SupplierList.supplierID == str(supplier_id))
        return db.session.execute(stmt).scalar_one_or_none()
      except Exception:
        return None
    return None

  def _find_supplier_channels(self, supplier_code: str) -> List[str]:
    if not supplier_code:
      return []
    s = self._find_supplier_by_id(supplier_code)
    ch = getattr(s, "channelId", None) if s else None
    if not ch:
      return []

    # 이름(#channel) 저장된 경우 ID로 변환
    if ch.startswith("#"):
      ch_id = self._resolve_channel_id_by_name(ch.lstrip("#"))
      return [ch_id or ch]
    return [ch]

  # ---------- 파싱 ----------
  def _coalesce(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    return payload.get("resource") or payload.get("data") or payload

  def _extract_meta(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    d = self._coalesce(payload)
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
    lines.append(f"[Cafe24] 공급사 등록/갱신 🧩")
    lines.append(f"- 공급사 코드: {m['supplier_code'] or '-'}")
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
    lines.append(f"- 수신시각: {m['ts_kst'].strftime('%Y-%m-%d %H:%M:%S %Z')}")
    return "\n".join(lines)

  # ---------- 엔트리 ----------
  def notify_supplier_created(self, payload: Dict[str, Any], topic: str):
    d = self._extract_meta(payload)
    text = self._build_message(d, topic)
    
    # 신규 공급사 등록 알림
    self._post_to_channel(SLACK_BROADCAST_CHANNEL_ID, text)
