# application/src/service/payments.py

from flask import Blueprint, render_template, request, jsonify
from flask_jwt_extended import jwt_required
import datetime as dt
import json
from application.src.service.toss_service import get_balance, list_payouts, get_seller, list_sellers, create_payouts_encrypted

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
  default_end = today

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
  status2, resp3 = list_sellers(limit=100)
  seller_items = resp3.get("entityBody", {}).get("items", [])

  # 6) 매칭 (destination == seller.id)
  merged = []
  seller_map = {s["id"]: s for s in seller_items}

  for p in payout_items:
      sid = p.get("destination")
      seller_info = seller_map.get(sid, {})
      merged.append({
          "payoutId": p.get("id"),
          "payoutDate": p.get("payoutDate"),
          "status": p.get("status"),
          "amount": p.get("amount", {}).get("value"),
          "currency": p.get("amount", {}).get("currency"),
          "transactionDescription": p.get("transactionDescription"),
          # 매칭된 셀러 정보
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
    status, resp = list_sellers(limit=100)
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
def ajax_add_payment():
    try:
        data = request.get_json(force=True)

        # Toss API 호출용 지급 요청 바디 구성
        item = {
            "refPayoutId": f"PO-{data['refSellerId']}-{int(dt.datetime.now().timestamp())}",
            "destination": data["refSellerId"],  # 셀러 ID
            "scheduleType": data["scheduleType"],
            "amount": {
                "currency": "KRW",
                "value": int(data["amount"])
            },
            "transactionDescription": data.get("company_name", "")
        }
        if data["scheduleType"] == "SCHEDULED":
            item["payoutDate"] = data["payoutDate"]

        status, resp = create_payouts_encrypted(item)

        if status == 200:
            return jsonify({"code": 20000, "resp": resp})
        else:
            return jsonify({"code": status, "message": "Toss API 오류", "detail": resp}), status

    except Exception as e:
        return jsonify({"code": 50000, "message": "예외 발생", "detail": str(e)}), 500
