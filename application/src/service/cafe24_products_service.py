# application/src/service/cafe24_products_service.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import os
from typing import Dict, Any, List, Optional
from datetime import datetime
from pytz import timezone

from application.src.repositories.SupplierListRepository import SupplierListRepository

from application.src.utils.slack_utils import post_text
from application.src.utils.cafe24_utils import coalesce, parse_kst, fmt_money

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
    created_kst = parse_kst(created)

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
      lines.append(f"- 판매가: {fmt_money(price)}")
    if stock not in ("", None):
      lines.append(f"- 재고: {stock}")
    lines.append(f"- 등록시각: {created_kst.strftime('%Y-%m-%d %H:%M:%S %Z')}```")
    return "\n".join(lines)

  # ----------------------------
  # 엔트리 포인트
  # ----------------------------
  def notify_product_created(self, payload: Dict[str, Any], topic: str):
    d = coalesce(payload)
    supplier_code = d.get("supplier_code") or ""
    msg = self._build_message(d, topic or "products/created")

    post_text(SLACK_BROADCAST_CHANNEL_ID, msg)
    try:
      supplier = SupplierListRepository.findBySupplierCode(supplier_code)
      post_text(supplier.channelId, msg)
    except Exception as e:
      # 로깅은 Flask logger에 맡기는 편이 깔끔하지만 여기선 안전하게 print
      print(f"[products.notify][fail] ch={supplier_code} err={e}")
