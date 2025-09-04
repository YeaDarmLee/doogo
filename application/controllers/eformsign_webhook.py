# application/src/service/eformsign_webhook.py
# -*- coding: utf-8 -*-

from flask import Blueprint, request, jsonify
import json
from datetime import datetime

eformsign_webhook = Blueprint("eformsign_webhook", __name__)

@eformsign_webhook.route("/webhooks/eformsign", methods=["POST"])
def handle_eformsign_webhook():
  """
  eformsign Webhook 수신 엔드포인트
  - 현재는 수신 바디/헤더를 안전하게 로그로만 출력
  - 추후 검증/상태 업데이트 로직 추가 예정
  """
  # 헤더/바디 추출
  hdr = {k: v for k, v in request.headers.items()}
  try:
    body = request.get_json(silent=True) or {}
  except Exception:
    body = {}

  # 유용한 필드 뽑아보기 (문서 이벤트 기준)
  event_type = body.get("event_type")
  doc = body.get("document") or {}
  doc_id = doc.get("id")
  status = doc.get("status")
  template_id = doc.get("template_id")
  updated_date = doc.get("updated_date")

  # 안전한 로그 (토큰/민감정보 제외)
  print(
    f"[{datetime.now()}] eformsign webhook 수신 "
    f"event_type={event_type} status={status} doc_id={doc_id} "
    f"template_id={template_id} updated_date={updated_date}"
  )

  # 전체 바디를 보고 싶으면(일시적으로만 권장):
  print(json.dumps(body, ensure_ascii=False, indent=2))

  # 200 OK
  return jsonify({"ok": True}), 200
