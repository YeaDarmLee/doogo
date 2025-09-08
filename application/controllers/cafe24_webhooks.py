# -*- coding: utf-8 -*-
from datetime import datetime
from flask import Blueprint, request, jsonify

from application.src.service.cafe24_webhook_service import Cafe24WebhookService

cafe24_webhooks_bp = Blueprint("cafe24_webhooks", __name__)
_service = Cafe24WebhookService()


@cafe24_webhooks_bp.route("/health", methods=["GET"])
def health():
  return jsonify(ok=True, ts=datetime.utcnow().isoformat())


@cafe24_webhooks_bp.route("/webhooks/cafe24/events", methods=["POST"])
def events():
  raw = request.get_data() or b""
  headers = {k: v for k, v in request.headers.items()}  # Case-insensitive Dict 유사
  remote_ip = request.remote_addr or ""

  result = _service.handle_event(raw, headers, remote_ip)
  # 웹훅은 빠른 200 OK가 중요
  return jsonify(result), 200
