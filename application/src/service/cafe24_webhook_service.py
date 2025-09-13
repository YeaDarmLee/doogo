# application/src/service/cafe24_webhook_service.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import os, json, hashlib, hmac
from typing import Dict, Any, Optional
from datetime import datetime
from flask import current_app

from application.src.service.cafe24_orders_service import Cafe24OrdersService
from application.src.service.cafe24_products_service import Cafe24ProductsService
from application.src.service.cafe24_suppliers_service import Cafe24SuppliersService
from application.src.service.cafe24_boards_service import Cafe24BoardsService
from application.src.repositories.WebhookEventRepository import WebhookEventRepository

class Cafe24WebhookService:
  """
  Cafe24 웹훅 엔트리:
    1) event_no 기반 라우팅 (우선순위 1)
    2) 없거나 파싱 실패 시 topic 문자열로 폴백 (우선순위 2)
    3) 웹훅 이벤트 DB 저장 + 멱등 처리 (WebhookEventRepository 가 있을 때)
    4) (선택) HMAC-SHA256 시그니처 검증 (CAFE24_CLIENT_SECRET 있으면)
  """
  def __init__(self):
    self.secret = os.getenv("CAFE24_CLIENT_SECRET", "")  # 없으면 검증 생략
    self.orders = Cafe24OrdersService()
    self.products = Cafe24ProductsService()
    self.suppliers = Cafe24SuppliersService()
    self.boards = Cafe24BoardsService()

  # ---------- 로깅 ----------
  def _log(self, msg: str):
    try:
      current_app.logger.info(f"[cafe24.webhook] {msg}")
    except Exception:
      print(f"[cafe24.webhook] {msg}")

  # ---------- 파싱 유틸 ----------
  def _coalesce(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    # cafe24 더미 기준: 주요 필드는 resource 안에 위치
    return payload.get("resource") or payload.get("data") or payload

  def _event_no(self, payload: Dict[str, Any]) -> Optional[int]:
    # 더미는 최상위에 event_no 존재, 혹시 몰라 resource 에도 탐색
    try:
      ev = payload.get("event_no")
      if ev is None:
        ev = self._coalesce(payload).get("event_no")
      return int(ev) if ev is not None else None
    except Exception:
      return None

  def _topic_from(self, headers: Dict[str, str], payload: Dict[str, Any]) -> str:
    # 헤더 우선, 없으면 resource.event_code → payload.topic → unknown
    x = headers.get("X-Cafe24-Topic")
    if x: return x
    r = self._coalesce(payload)
    return r.get("event_code") or payload.get("topic") or "unknown"

  # ---------- 시그니처 ----------
  def _sig_ok(self, raw: bytes, headers: Dict[str, str]) -> bool:
    if not self.secret:
      return True  # 초기엔 검증 생략
    header_sig = headers.get("X-Cafe24-Hmac-Sha256") or headers.get("X-Cafe24-Signature")
    if not header_sig:
      return False
    # cafe24 쪽 서명 포맷(HEX/B64)은 환경에 따라 다를 수 있음 → 우선 hex 비교
    mac_hex = hmac.new(self.secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    try:
      # 우선 hex 비교
      if hmac.compare_digest(mac_hex, header_sig):
        return True
      # 혹시 base64 로 오는 경우 대비(간단 비교)
      import base64
      mac_b64 = base64.b64encode(bytes.fromhex(mac_hex)).decode("utf-8")
      return hmac.compare_digest(mac_b64, header_sig)
    except Exception:
      return False

  # ---------- 멱등 키 ----------
  def _make_dedupe_key(self, event_no: Optional[int], topic: str, raw: bytes, webhook_id: str) -> str:
    base = f"{event_no or ''}|{topic or ''}|{webhook_id or ''}|{hashlib.sha256(raw).hexdigest()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:64]

  # ---------- DB 저장(옵션) ----------
  def _maybe_persist(self, raw: bytes, headers: Dict[str, str], payload: Dict[str, Any], event_no: Optional[int], topic: str):
    if not WebhookEventRepository:
      return {"persisted": False, "dup": False}

    sig_ok = self._sig_ok(raw, headers)
    webhook_id = headers.get("X-Cafe24-Webhook-Id") or ""
    dedupe_key = self._make_dedupe_key(event_no, topic, raw, webhook_id)

    try:
      exists = WebhookEventRepository.get_by_dedupe(dedupe_key)
    except Exception as e:
      self._log(f"persist check error: {e}")
      return {"persisted": False, "dup": False}

    if exists:
      self._log(f"dup event: event_no={event_no} topic={topic} key={dedupe_key[:10]}...")
      return {"persisted": False, "dup": True}

    try:
      pretty = json.dumps(payload, ensure_ascii=False)
    except Exception:
      pretty = (raw[:4000]).decode("utf-8", "ignore")

    try:
      WebhookEventRepository.insert(
        dedupe_key=dedupe_key,
        webhook_id=webhook_id,
        topic=str(topic or ""),
        sig_verified=bool(sig_ok),
        body_json=pretty
      )
      self._log(f"saved event: event_no={event_no} topic={topic} key={dedupe_key[:10]}...")
      return {"persisted": True, "dup": False}
    except Exception as e:
      self._log(f"persist insert error: {e}")
      return {"persisted": False, "dup": False}

  # ---------- 메인 엔트리 ----------
  def handle_event(self, raw: bytes, headers: Dict[str, str], remote_ip: str) -> Dict[str, Any]:
    try:
      payload = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
      payload = {}

    event_no = self._event_no(payload)
    print(event_no)
    topic = (self._topic_from(headers, payload) or "").lower()

    self._log(f"recv ip={remote_ip} event_no={event_no} topic={topic}")
    self._log(f"body={(raw[:4000]).decode('utf-8','ignore')}")

    # 1) DB 저장(+멱등)
    persist_info = self._maybe_persist(raw, headers, payload, event_no, topic)

    # 2) 라우팅
    routed = None
    try:
      ## 쇼핑몰 > 게시판 ##
      if event_no == 90033:
        # 쇼핑몰에 게시물이 등록된 경우
        self.boards.notify_board_created(payload, topic)
        routed = "board.created"

      ## 쇼핑몰 > 공급사 ##
      elif event_no == 90090:
        # 쇼핑몰에 공급사가 등록된 경우
        self.suppliers.notify_supplier_created(payload, f"event/{event_no}")
        routed = "suppliers.created"

      ## 쇼핑몰 > 상품 ##
      elif event_no == 90001:
        # 쇼핑몰에 상품이 등록된 경우
        self.products.notify_product_created(payload, f"event/{event_no}")
        routed = "products.created"

      ## 쇼핑몰 > 주문 ##
      elif event_no == 90023:
        # 쇼핑몰에 주문이 접수된 경우
        self.orders.notify_order_created(payload, f"event/{event_no}")
        routed = "orders.created"

      elif event_no == 90024:
        # 쇼핑몰 주문의 배송상태가 변경된 경우
        self.orders.notify_order_shipping_updated(payload, f"event/{event_no}")
        routed = "orders.shipping_updated"

      if not routed:
        self._log(f"no route matched: event_no={event_no}, topic={topic}")
    except Exception as e:
      self._log(f"route error: {e}")
      return {
        "ok": False,
        "error": str(e),
        "event_no": event_no,
        "topic": topic,
        "persisted": persist_info.get("persisted"),
        "dup": persist_info.get("dup"),
      }

    # 3) 응답
    return {
      "ok": True,
      "event_no": event_no,
      "topic": topic,
      "routed": routed,
      "persisted": persist_info.get("persisted"),
      "dup": persist_info.get("dup"),
      "ts": datetime.utcnow().isoformat()
    }
