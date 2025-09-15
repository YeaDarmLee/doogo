# application/src/utils/cafe24_utils.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, Any, Optional
from datetime import datetime
from pytz import timezone

_KST = timezone('Asia/Seoul')

EVENT_CODE_MAP: Dict[str, str] = {
  "shipping_ready": "배송준비",
  "shipping_start": "배송시작",
  "shipping_reject": "배송거부",
  "shipping_delay": "배송지연",
  "shipping_resend": "재배송",
  "shipping_complete": "배송완료",
  "shipping_hold": "배송보류",
  "purchase_confirm": "구매확정",
  "order_cancel": "주문취소",
  "order_cancel_request": "취소요청",
  "order_exchange": "교환요청",
  "order_return": "반품요청",
}

SHIPPING_STATUS_MAP: Dict[str, str] = {
  "F": "배송전",
  "M": "배송중",
  "T": "배송완료",
}

BOARD_ROUTE: Dict[int, str] = {
  2: "broadcast_only",       # 공급사 입점
  4: "broadcast_and_vendor", # 상품후기
  6: "broadcast_and_vendor", # 상품 Q&A
}

BOARD_NAME_MAP: Dict[int, str] = {
  1: "공지사항",
  2: "공급사 입점",
  3: "자주묻는 질문",
  4: "상품후기",
  5: "멤버쉽가입",
  6: "상품 Q&A",
  7: "자료실",
  8: "이벤트",
  9: "1:1 맞춤상담",
  101: "브랜드 입점 문의",
  1001: "한줄메모",
  1002: "대량 구매 문의",
  3001: "대량주문",
}

def coalesce(payload: Dict[str, Any]) -> Dict[str, Any]:
  """
  Cafe24 웹훅/더미 페이로드 래퍼 정규화.
  - resource > data > (타입별 기본키)
  """
  return payload.get("resource") or payload.get("data") or payload.get("order") or payload.get("product") or payload

def parse_kst(ts: Optional[str]) -> datetime:
  """
  ISO8601(또는 'Z') 기반 문자열을 KST aware datetime 으로 변환.
  """
  if not ts:
    return datetime.utcnow().astimezone(_KST)
  try:
    ts = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts)
  except Exception:
    dt = datetime.utcnow()
  return dt.astimezone(_KST)

def fmt_money(v) -> str:
  """
  숫자/문자 금액을 '1,234원' 형식으로. 변환 불가 시 원본 반환.
  """
  try:
    n = float(v)
    return f"{n:,.0f}원"
  except Exception:
    return str(v or "")

def humanize_event(code: Optional[str]) -> str:
  if not code:
    return "-"
  c = str(code).strip().lower()
  return EVENT_CODE_MAP.get(c, code)

def humanize_shipping(status: Optional[str]) -> str:
  if not status:
    return "-"
  s = str(status).strip().upper()
  return SHIPPING_STATUS_MAP.get(s, status)

def get_board_route(board_no: int) -> str:
  """라우팅 정책: 'broadcast_only' | 'broadcast_and_vendor' | 'unknown'"""
  return BOARD_ROUTE.get(int(board_no), "unknown")

def get_board_name(board_no: int, default: str = "-") -> str:
  """게시판 번호 → 사람이 읽는 이름"""
  return BOARD_NAME_MAP.get(int(board_no), default)

def is_vendor_routed(board_no: int) -> bool:
  """벤더 채널에도 알릴 보드인가?"""
  return get_board_route(board_no) == "broadcast_and_vendor"