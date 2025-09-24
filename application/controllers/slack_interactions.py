# -*- coding: utf-8 -*-
import os, hmac, hashlib, time, json
from flask import Blueprint, request, current_app
from slack_sdk.errors import SlackApiError
from typing import Optional, List, Dict, Any  # 파일 상단에 추가

from application.src.service.slack_service import ensure_client, _sleep_if_rate_limited
from application.src.service.toss_service import TossPayoutsError

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
  ref_id = val.get("settlement_id")   # refPayoutId
  dest   = val.get("destination")     # seller id
  sched  = val.get("schedule_type") or "EXPRESS"
  amt_v  = int(val.get("amount_value") or 0)
  amt_c  = val.get("amount_currency") or "KRW"
  desc   = (val.get("transaction_description") or "정산")[:7]  # 최대 7자

  if not (ch and ts and ref_id and dest and amt_v > 0):
    _chat_update(ch, ts, text=":warning: 지급요청 파라미터가 올바르지 않습니다. 담당자에게 문의해 주세요.")
    return

  # 처리 중 상태로 즉시 업데이트 (버튼 제거)
  _chat_update(
    ch, ts,
    text="⏳ 정산 확정을 처리 중입니다...",
    blocks=[{
      "type": "section",
      "text": { "type": "mrkdwn", "text": "⏳ *정산 확정을 처리 중입니다...*" }
    }]
  )

  # Toss 클라이언트
  # toss = current_app.extensions.get("toss_client")
  # if not toss:
  #   _chat_update(ch, ts, text=":warning: 지급요청 클라이언트를 찾을 수 없습니다.")
  #   return

  # 지급요청 바디 구성
  item = {
    "refPayoutId": ref_id,
    "destination": dest,
    "scheduleType": sched,
    "amount": {"currency": amt_c, "value": amt_v},
    "transactionDescription": desc
  }
  if sched == "SCHEDULED" and val.get("payout_date"):
    item["payoutDate"] = val["payout_date"]

  # 요청 실행
  try:
    # jwe = toss.request_payouts(item)  # ENCRYPTION(JWE) 응답 문자열
    # 성공 메시지
    _chat_update(
      ch, ts,
      text=":white_check_mark: 정산이 확정되어 지급 요청을 보냈습니다.",
      blocks=[{
        "type": "section",
        "text": {
          "type": "mrkdwn",
          "text": f":white_check_mark: *정산 확정 완료*\n• 참조ID: `{ref_id}`\n• 대상: `{dest}`\n• 금액: {amt_v:,} {amt_c}\n• 방식: {sched}"
        }
      }]
    )
  except TossPayoutsError as e:
    _chat_update(
      ch, ts,
      text=f":x: 지급요청 실패\n```{str(e)[:500]}```"
    )
  except Exception as e:
    _chat_update(
      ch, ts,
      text=f":x: 예기치 못한 오류\n```{str(e)[:500]}```"
    )
