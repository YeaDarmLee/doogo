# application/src/service/payments.py

from flask import Blueprint, render_template, request, jsonify
from flask_jwt_extended import jwt_required
import datetime as dt
import uuid
from application.src.service.toss_service import get_balance, list_payouts, get_seller, list_sellers, create_payouts_encrypted, cancel_payout

payments = Blueprint("payments", __name__, url_prefix="/payments")

def _parse_ymd(s: str) -> dt.date:
  return dt.datetime.strptime(s, "%Y-%m-%d").date()

@payments.route("/")
@jwt_required()
def index():
  # 1) 잔액 조회
  status, resp = get_balance()
  available = resp.get("entityBody", {}).get("availableAmount", {}).get("value", 0)
  pending   = resp.get("entityBody", {}).get("pendingAmount", {}).get("value", 0)

  # 2) 기간 기본값: 오늘 ~ 6일 전
  today = dt.date.today()
  default_start = today - dt.timedelta(days=6)
  default_end = today + dt.timedelta(days=6)

  # 3) 쿼리 파라미터 받기 (없으면 기본값)
  q_start = request.args.get("startDate")
  q_end   = request.args.get("endDate")
  try:
    start_date = _parse_ymd(q_start) if q_start else default_start
  except Exception:
    start_date = default_start
  try:
    end_date = _parse_ymd(q_end) if q_end else default_end
  except Exception:
    end_date = default_end

  # 보정: 시작 > 종료면 서로 스왑
  if start_date > end_date:
    start_date, end_date = end_date, start_date

  # 4) 지급 요청 목록 조회
  status, resp2 = list_payouts(
    limit=100,
    payoutDateGte=start_date.strftime("%Y-%m-%d"),
    payoutDateLte=end_date.strftime("%Y-%m-%d")
  )
  payout_items = resp2.get("entityBody", {}).get("items", [])

  # 5) 셀러 목록 조회
  status2, resp3 = list_sellers(limit=1000)
  seller_items = resp3.get("entityBody", {}).get("items", [])

  # 6) 매칭 (destination == seller.id)
  merged = []
  seller_map = {s["id"]: s for s in seller_items}

  requested_pay = 0
  for p in payout_items:
    status = p.get("status")
    sid = p.get("destination")
    raw = p.get("requestedAt")
    dt_obj = dt.datetime.fromisoformat(raw)  # tz까지 포함된 ISO8601 문자열 파싱
    formatted = dt_obj.strftime("%Y-%m-%d")  # 원하는 출력: '2025-09-27'
      
    if status == 'REQUESTED':
      requested_pay += int(p.get("amount", {}).get("value"))

    seller_info = seller_map.get(sid, {})
    merged.append({
      "payoutId": p.get("id"),
      "payoutDate": p.get("payoutDate"),
      "requestedAt": formatted,
      "status": status,
      "amount": p.get("amount", {}).get("value"),
      "currency": p.get("amount", {}).get("currency"),
      "transactionDescription": p.get("transactionDescription"),
      # 매칭된 셀러 정보
      "sellerId": seller_info.get("id"),
      "refSellerId": seller_info.get("refSellerId"),
      "businessType": seller_info.get("businessType"),
      "companyName": seller_info.get("company", {}).get("name"),
      "representativeName": seller_info.get("company", {}).get("representativeName"),
      "businessNumber": seller_info.get("company", {}).get("businessRegistrationNumber"),
      "email": seller_info.get("company", {}).get("email"),
      "phone": seller_info.get("company", {}).get("phone"),
      "status_seller": seller_info.get("status"),
      "accountNumber": seller_info.get("account", {}).get("accountNumber"),
      "bankCode": seller_info.get("account", {}).get("bankCode"),
      "holderName": seller_info.get("account", {}).get("holderName"),
    })

  # 5) 템플릿 렌더
  return render_template(
    "payments.html",
    pageName="payments",
    available=available,
    waitingPayment=requested_pay,
    availableExpected=available-requested_pay,
    pending=pending,
    items=merged,
    startDate=start_date.strftime("%Y-%m-%d"),
    endDate=end_date.strftime("%Y-%m-%d"),
  )


@payments.route("/ajax/search-seller", methods=["GET"])
@jwt_required()
def ajax_search_seller():
  q = (request.args.get("q") or "").strip()
  if not q:
    return jsonify({"code": 40001, "message": "q가 필요합니다."}), 400

  try:
    # 1) 실제 seller_id 형태라면 단건 조회
    if q.startswith("seller_"):
      status, resp = get_seller(q)
      if status == 200 and resp.get("entityBody"):
        return jsonify({"code": 20000, "item": resp["entityBody"]})
      # 실패 시 계속해서 목록 검색 fallback

    # 2) 목록에서 refSellerId 부분 일치로 검색
    status, resp = list_sellers(limit=1000)
    if status != 200:
      return jsonify({"code": status, "message": "Toss API 오류", "detail": resp}), status

    items = resp.get("entityBody", {}).get("items", [])
    # 우선순위: refSellerId 부분일치 → company.name 부분일치 → id 부분일치
    qlow = q.lower()
    found = None
    for it in items:
      refid = (it.get("refSellerId") or "").lower()
      cname = (it.get("company", {}).get("name") or "").lower()
      sid   = (it.get("id") or "").lower()
      if qlow in refid or qlow in cname or qlow in sid:
        found = it
        break

    return jsonify({"code": 20000, "item": found})

  except Exception as e:
    return jsonify({"code": 50000, "message": "예외 발생", "detail": str(e)}), 500

@payments.route("/ajax/add", methods=["POST"])
@jwt_required()
def ajax_add_payment():
  try:
    data = request.get_json(force=True)
    
    today = dt.date.today()
    period = today.strftime("%Y-%m-%d")
    
    ref_base = (data["refSellerId"] or "").strip()
    stamp    = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    suffix   = uuid.uuid4().hex[:6]
    ref_payout_id = f"{ref_base}-{stamp}-{suffix}"

    # Toss API 호출용 지급 요청 바디 구성
    item = {
      "refPayoutId": ref_payout_id,
      "destination": data["supplier_id"],
      "scheduleType": data["scheduleType"],
      "amount": {
        "currency": "KRW",
        "value": int(data["amount"])
      },
      "transactionDescription": "수동정산",
      "metadata": {
        "period": period
      },
    }
    if data["scheduleType"] == "SCHEDULED":
      item["payoutDate"] = data["payoutDate"]

    status, resp = create_payouts_encrypted(item)
    print(status, resp)

    if status == 200:
      return jsonify({"code": 20000, "resp": resp})
    else:
      return jsonify({"code":  (resp.get("error", {}) or {}).get("code"), "message": (resp.get("error", {}) or {}).get("message"), "detail": resp}), status

  except Exception as e:
    print(e)
    return jsonify({"code": 50000, "message": "예외 발생", "detail": str(e)}), 500

@payments.route("/ajax/cancel", methods=["POST"])
@jwt_required()
def ajax_cancel_payment():
  """
  지급요청 '취소' 처리 엔드포인트
  - 프런트는 우선 refPayoutId를, 없으면 (refSellerId, amount, payoutDate) 보조키 전달
  - 실제 외부 결제사(TossPayments 등) 호출 로직은 이 함수 안에서 처리
  """
  data = request.get_json(silent=True) or {}

  payout_id = (data.get("payoutId") or "").strip()

  try:
    status, resp = cancel_payout(payout_id)

    # Toss 응답 맵핑(예시)
    if 200 <= status < 300:
      # (선택) DB 상태를 'CANCELED'로 갱신
      return jsonify({"code": 20000, "message": "취소 완료", "data": resp})
    elif status == 404:
      return jsonify({"code": 40400, "message": "지급 요청을 찾을 수 없습니다.", "data": resp}), 404
    elif status == 409:
      # 상태가 SCHEDULED가 아니거나 지급일 경과 등의 케이스
      return jsonify({"code": 40900, "message": "취소할 수 없는 상태입니다.", "data": resp}), 409
    else:
      return jsonify({"code": 50010, "message": "취소 처리 중 오류가 발생했습니다.", "data": resp}), 500

  except Exception as e:
    print(e)
    return jsonify({"code": 50000, "message": f"서버 오류: {e}"}), 500