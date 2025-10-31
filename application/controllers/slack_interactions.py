# -*- coding: utf-8 -*-
import os, hmac, hashlib, time, json
import datetime, time, uuid
from flask import Blueprint, request, current_app
from slack_sdk.errors import SlackApiError
from typing import Optional, List, Dict, Any
from datetime import date, timedelta, datetime as dt
from decimal import Decimal, ROUND_HALF_UP

from application.src.service.slack_service import ensure_client, _sleep_if_rate_limited
from application.src.repositories.SupplierListRepository import SupplierListRepository
from application.src.repositories.SupplierDetailRepository import SupplierDetailRepository

from application.src.service.toss_service import list_sellers, create_payouts_encrypted
from application.src.service.barobill_service import BaroBillClient, BaroBillError

slack_actions = Blueprint("slack_actions", __name__, url_prefix="/slack")

_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")

# YYYY-MM-DD 문자열 세트를 date 객체 세트로 변환
_HOLIDAYS_STR = {
  # ==== 2025 ====
  "2025-01-01","2025-01-28","2025-01-29","2025-01-30",
  "2025-03-01","2025-03-03",
  "2025-05-05","2025-05-06",
  "2025-06-06",
  "2025-08-15",
  "2025-10-03","2025-10-05","2025-10-06","2025-10-07","2025-10-08","2025-10-09",
  "2025-12-25",
  # ==== 2026 ====
  "2026-01-01",
  "2026-02-16","2026-02-17","2026-02-18",
  "2026-03-01","2026-03-02",
  "2026-05-05","2026-05-24","2026-05-25",
  "2026-06-06",
  "2026-08-15","2026-08-17",
  "2026-09-24","2026-09-25","2026-09-26",
  "2026-10-03","2026-10-05",
  "2026-10-09",
  "2026-12-25",
}
_HOLIDAYS = {dt.strptime(d, "%Y-%m-%d").date() for d in _HOLIDAYS_STR}

def _is_business_day(d: date) -> bool:
  # 월(0)~금(4) && 공휴일 아님
  return d.weekday() < 5 and d not in _HOLIDAYS

def _next_business_day(d: date) -> date:
  cur = d
  while not _is_business_day(cur):
    cur += timedelta(days=1)
  return cur
def compute_payout_date(base: date, *, prefer_one_day: bool = True) -> date:
  """
  prefer_one_day=True  → +1일이 영업일이면 그대로, 아니면 +1일부터 다음 영업일
  prefer_one_day=False → +2일 기준으로 다음 영업일
  """
  if prefer_one_day:
    d1 = base + timedelta(days=1)
    return d1 if _is_business_day(d1) else _next_business_day(d1)
  else:
    d2 = base + timedelta(days=2)
    return _next_business_day(d2)

def split_vat(total: int, vat_rate: Decimal = Decimal("0.1")) -> tuple[int, int]:
  """
  total: 부가세 포함 금액
  vat_rate: 0.1 = 10%
  return: (공급가액, 세액)
  """
  total_dec = Decimal(total)
  supply = (total_dec / (Decimal(1) + vat_rate)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
  tax = total_dec - supply
  return int(supply), int(tax)

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
  #  - True  : +1 우선(영업일이면 그 날, 아니면 그 다음 영업일)
  #  - False : 기존처럼 +2 기준으로 다음 영업일
  payout_dt = compute_payout_date(today, prefer_one_day=True)
  payout_date = payout_dt.strftime("%Y-%m-%d")
  
  s = SupplierListRepository.findBySupplierCode(supply_id)

  # 📌 final_amount가 0이면 바로 종료
  if final_amount <= 0:
    _chat_update(
      ch, ts,
      text=":zzz: 이번 기간에는 매출이 없어 정산할 내용이 없습니다.",
      blocks=[{
        "type": "section",
        "text": {
          "type": "mrkdwn",
          "text": f":zzz: *정산 불필요*\n• 정산금액: `0`\n• 정산 기간: `{start}-{end}`"
        }
      }]
    )
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
    
    sd = SupplierDetailRepository.findBySupplierSeq(s.seq)
    
    baro = BaroBillClient()

    supply, tax = split_vat(final_amount)
    res = baro.regist_and_issue_taxinvoice(
      target_corp_num=sd.businessRegistrationNumber,
      target_corp_name=s.companyName,
      target_ceo=sd.representativeName,
      target_addr=sd.bizAddr,
      target_contact=s.manager,
      target_tel=s.number,
      target_email=sd.companyEmail,
      target_id=s.supplierID,
      amount_total=f"{supply}",
      tax_total=f"{tax}",
      total_amount=f"{final_amount}",
      items=[
        {"name": "판매 지급 수수료", "qty": "1", "unit_price": f"{final_amount}", "amount": f"{supply}", "tax": f"{tax}"}
      ],
    )
    if res == 1:
      print('성공')
    else:
      print(f"⚠️ [에러] 발행 응답 코드: {res}")
  except Exception as e:
    _chat_update(
      ch, ts,
      text=f":x: 예기치 못한 오류\n```{str(e)[:500]}```"
    )
