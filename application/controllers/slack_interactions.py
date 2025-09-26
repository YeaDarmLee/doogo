# -*- coding: utf-8 -*-
import os, hmac, hashlib, time, json
import datetime, time, uuid
from flask import Blueprint, request, current_app
from slack_sdk.errors import SlackApiError
from typing import Optional, List, Dict, Any  # 파일 상단에 추가

from application.src.service.slack_service import ensure_client, _sleep_if_rate_limited
from application.src.repositories.SupplierListRepository import SupplierListRepository

from application.src.service.toss_service import list_sellers, create_payouts_encrypted

slack_actions = Blueprint("slack_actions", __name__, url_prefix="/slack")

_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")

def _verify_slack_signature(req) -> bool:
  """
  Slack 서명 검증 (5분 리플레이 보호)
  """
  try:
    ts = req.headers.get("X-Slack-Request-Timestamp", "")
    sig = req.headers.get("X-Slack-Signature", "")
    if not ts or not sig:
      return False
    if abs(time.time() - int(ts)) > 60 * 5:
      return False
    base = f"v0:{ts}:{req.get_data(as_text=True)}"
    my = "v0=" + hmac.new(_SIGNING_SECRET.encode(), base.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(my, sig)
  except Exception:
    return False

def _chat_update(
  channel: str,
  ts: str,
  *,
  text: str,
  blocks: Optional[List[Dict[str, Any]]] = None
) -> None:
  """
  메시지 교체(업데이트). blocks가 없으면 텍스트만.
  """
  cli = ensure_client()
  kwargs = {"channel": channel, "ts": ts, "text": text}
  if blocks:
    kwargs["blocks"] = blocks
  for _ in range(2):
    try:
      cli.chat_update(**kwargs)
      return
    except SlackApiError as e:
      if _sleep_if_rate_limited(e):
        continue
      current_app.logger.error(f"[chat.update.fail] ch={channel} ts={ts} err={getattr(e, 'response', {}).get('data', {})}")
      return

@slack_actions.route("/interactions", methods=["POST"])
def interactions():
  # 1) 서명 검증
  if _SIGNING_SECRET and not _verify_slack_signature(request):
    return "invalid signature", 403

  # 2) 페이로드 파싱
  payload_str = request.form.get("payload", "{}")
  try:
    payload = json.loads(payload_str)
  except Exception:
    return "", 200

  if payload.get("type") != "block_actions":
    return "", 200

  actions = payload.get("actions") or []
  if not actions:
    return "", 200

  action = actions[0]
  action_id = action.get("action_id")

  if action_id == "payout_confirm":
    _handle_payout_confirm(payload, action)
    # Slack은 3초 내 응답 권장 → 바로 200 OK
    return "", 200

  return "", 200

def _handle_payout_confirm(payload: dict, action: dict):
  """
  '정산 확정하기' 버튼 처리:
  - 버튼 value(JSON) → 토스 지급요청 바디 구성
  - 처리 중 표시 → 요청 → 성공/실패로 메시지 업데이트
  """
  ch = (payload.get("channel") or {}).get("id")
  msg = payload.get("message") or {}
  ts = msg.get("ts")

  # 버튼 value(JSON) 파싱
  try:
    val = json.loads(action.get("value") or "{}")
  except Exception:
    val = {}

  # 필수값 체크
  supply_id = val.get("supply_id")
  channel   = val.get("channel")
  start  = val.get("start")
  end  = val.get("end")
  final_amount  = int(val.get("final_amount") or 0)
  
  today = datetime.date.today()
  payout_date = (today + datetime.timedelta(days=2)).strftime("%Y-%m-%d")
  
  s = SupplierListRepository.findBySupplierCode(supply_id)

  # 처리 중 상태로 즉시 업데이트 (버튼 제거)
  _chat_update(
    ch, ts,
    text="⏳ 정산 확정을 처리 중입니다...",
    blocks=[{
      "type": "section",
      "text": { "type": "mrkdwn", "text": "⏳ *정산 확정을 처리 중입니다...*" }
    }]
  )
  
  status, resp2 = list_sellers(limit=1000)
  seller_items = resp2.get("entityBody", {}).get("items", [])
  
  # 매칭 찾기
  match_seller = next(
    (item for item in seller_items if item.get("refSellerId") == s.supplierCode),
    None
  )
  
  # 고유 refPayoutId 생성 (중복 방지)
  ref_base = (s.supplierCode or "").strip()
  stamp    = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
  suffix   = uuid.uuid4().hex[:6]
  ref_payout_id = f"{ref_base}-{stamp}-{suffix}"

  # 지급요청 바디 구성
  item = {
    "refPayoutId": ref_payout_id,
    "destination": match_seller["id"],
    "scheduleType": "SCHEDULED",
    "payoutDate": payout_date,
    "amount": {
      "currency": "KRW",
      "value": final_amount
    },
    "transactionDescription": "정기정산",
    "metadata": {
      "period": f"{start}-{end}"
    }
  }

  # 요청 실행
  try:
    status, resp = create_payouts_encrypted(item)
    print(status, resp)
    
    # 성공 메시지
    _chat_update(
      ch, ts,
      text=":money_with_wings: 정산이 확정되어 지급 요청을 보냈습니다.",
      blocks=[{
        "type": "section",
        "text": {
          "type": "mrkdwn",
          "text": f":money_with_wings: *정산 확정 완료*\n• 정산금액: `{final_amount}`\n• 정산 예정일: `{payout_date}`\n• 정산 기간: `{start}-{end}`"
        }
      }]
    )
  except Exception as e:
    _chat_update(
      ch, ts,
      text=f":x: 예기치 못한 오류\n```{str(e)[:500]}```"
    )
