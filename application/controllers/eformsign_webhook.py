# application/src/service/eformsign_webhook.py
# -*- coding: utf-8 -*-

import os
import json
from datetime import datetime
from flask import Blueprint, request, jsonify

from application.src.models import db
from application.src.models.SupplierList import SupplierList

from application.src.service.slackService import post_message_to_channel

# 마지막 수단: slack_sdk 직접 사용 (토큰은 .env 의 SLACK_BOT_TOKEN)
try:
  from slack_sdk import WebClient as _SlackClient
  _slack_client = _SlackClient(token=os.getenv("SLACK_BOT_TOKEN"))
except Exception:
  _slack_client = None

eformsign_webhook = Blueprint("eformsign_webhook", __name__)
SLACK_BROADCAST_CHANNEL_ID = os.getenv("SLACK_BROADCAST_CHANNEL_ID", "").strip()

def _notify_slack(channel_id: str, text: str):
  """
  채널로 간단 알림 전송 (slackService 우선, 없으면 slack_sdk fallback)
  """
  if not channel_id or not text:
    return False

  # slackService 사용
  try:
    post_message_to_channel(channel_id, text)
    return True
  except Exception:
    pass

  # slack_sdk fallback
  if _slack_client:
    try:
      _slack_client.chat_postMessage(channel=channel_id, text=text)
      return True
    except Exception:
      pass

  print(f"[{datetime.now()}] [SLACK_NOTIFY_FAIL] channel={channel_id} text={text[:200]}")
  return False


@eformsign_webhook.route("/webhooks/eformsign", methods=["POST"])
def handle_eformsign_webhook():
  """
  eformsign Webhook 수신 엔드포인트
    1) event_type == "document" 체크
    2) template_id == EFORMSIGN_TEMPLATE_ID 체크
    3) editor_id 로 SupplierList 조회
    4) 문서ID 보정 저장 (contractId)
    5) 상태 처리:
       - status == 'doc_complete' → contractStatus='SS' 저장 + Slack 알림
       - 그 외(status 존재 시) → contractStatus=status 저장(알림 X)
  """
  body = request.get_json(silent=True) or {}
  event_type = body.get("event_type")
  doc = body.get("document") or {}
  template_id = doc.get("template_id")
  editor_email = doc.get("editor_id")
  document_id = doc.get("id")
  status = doc.get("status")
  updated_ms = doc.get("updated_date")

  print(
    f"[{datetime.now()}] [eformsign webhook] "
    f"event_type={event_type} template_id={template_id} editor_id={editor_email} "
    f"status={status} doc_id={document_id} updated={updated_ms}"
  )

  # 1) 이벤트 타입 체크
  if event_type != "document":
    return jsonify({"ok": True, "skipped": "non-document event"}), 200

  # 2) 템플릿 ID 검증
  env_tid = (os.getenv("EFORMSIGN_TEMPLATE_ID") or "").strip()
  if env_tid and template_id != env_tid:
    print(f"[{datetime.now()}] [SKIP] template_id mismatch: got={template_id} expected={env_tid}")
    return jsonify({"ok": True, "skipped": "template mismatch"}), 200

  # 3) editor_id(이메일) 로 공급사 찾기
  if not editor_email:
    print(f"[{datetime.now()}] [SKIP] editor_id missing in webhook body")
    return jsonify({"ok": True, "skipped": "editor_id missing"}), 200

  try:
    supplier: SupplierList | None = (
      db.session.query(SupplierList)
      .filter(SupplierList.email == editor_email)
      .order_by(SupplierList.seq.desc())
      .first()
    )
  except Exception as e:
    print(f"[{datetime.now()}] [DB_ERROR] find supplier by email failed err={e}")
    return jsonify({"ok": False, "error": "db lookup failed"}), 500

  if not supplier:
    print(f"[{datetime.now()}] [NOT_FOUND] supplier by email={editor_email}")
    return jsonify({"ok": True, "skipped": "supplier not found"}), 200

  # 계약서 ID 보정 저장
  if document_id:
    if supplier.contractId and supplier.contractId != document_id:
      print(
        f"[{datetime.now()}] [WARN] contractId mismatch seq={supplier.seq} "
        f"old={supplier.contractId} new={document_id}"
      )
    supplier.contractId = document_id

  # 5) 상태 처리
  try:
    if status == "doc_complete":
      supplier.contractStatus = 'SS'  # 최종완료
      db.session.commit()
      print(
        f"[{datetime.now()}] [CONTRACT_DONE] seq={supplier.seq} company={supplier.companyName} "
        f"email={editor_email} doc_id={document_id}"
      )

      # Slack 알림 (doc_complete만)
      if supplier.channelId:
        msg = (
          f":page_with_curl: *계약서 작성 완료* / 공급사: {supplier.companyName} / 이메일: `{editor_email}` / 상태: {status}"
        )
        _notify_slack(supplier.channelId, msg)
        _notify_slack(SLACK_BROADCAST_CHANNEL_ID, msg)
        
        text = (
          f":tada: `{supplier.companyName}` 공급사 지원 채널이 생성되었습니다.\n"
          f"관리자 링크 바로가기: https://eclogin.cafe24.com/Shop/?url=Init&login_mode=3\n"
          f"아이디: `onedayboxb2b` / 공급사 ID: `{supplier.supplierID}` / PW: `{supplier.supplierPW}`\n"
          f"첫 로그인 할 때 비밀번호 재설정이 나오니 로그인 후 원하시는 비밀번호로 셋팅해주시면 됩니다.:smile:\n"
          f":round_pushpin: 운영 관련 공지와 사용 가이드는 <#C09DBG0UYCS> 및 <#C09EAJ46Z5J> 채널을 꼭 참고해주세요."
        )
        _notify_slack(supplier.channelId, text=text)
      else:
        print(f"[{datetime.now()}] [INFO] no channelId for seq={supplier.seq}, skip Slack notify")

    else:
      # 그 외 중간 상태는 DB만 저장(알림 X)
      if status:
        supplier.contractStatus = status
      db.session.commit()
      print(
        f"[{datetime.now()}] [CONTRACT_PROGRESS] seq={supplier.seq} "
        f"company={supplier.companyName} status={status}"
      )

  except Exception as e:
    db.session.rollback()
    print(f"[{datetime.now()}] [DB_ERROR] status update failed seq={supplier.seq} err={e}")
    return jsonify({"ok": False, "error": "db update failed"}), 500

  return jsonify({"ok": True}), 200
