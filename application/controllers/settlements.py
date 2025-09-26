# application/src/service/settlements.py
from flask import Blueprint, render_template, request, jsonify
from flask_jwt_extended import jwt_required
import datetime as dt
import json
from application.src.service.toss_service import get_balance, list_settlements

settlements = Blueprint("settlements", __name__, url_prefix="/settlements")

def _parse_ymd(s: str) -> dt.date:
  return dt.datetime.strptime(s, "%Y-%m-%d").date()

@settlements.route("/")
@jwt_required()
def index():
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

  if start_date > end_date:
    start_date, end_date = end_date, start_date
  
  status, resp = list_settlements(start_date, end_date)

  # 🔹 합계 계산
  def _num(v):
    try:
      return float(v)
    except Exception:
      return 0.0

  total_amount = 0.0
  total_fee = 0.0
  total_payout = 0.0

  for i in (resp or []):
    amount = _num(i.get("amount"))
    fee = _num(i.get("fee"))
    payout = i.get("payOutAmount")
    payout = _num(payout) if payout is not None else amount - fee

    total_amount += amount
    total_fee += fee
    total_payout += payout

  totals = {
    "amount": total_amount,
    "fee": total_fee,
    "payout": total_payout,
  }

  return render_template(
    "settlements.html",
    pageName="settlements",
    items=resp,
    totals=totals,  # ⬅️ 합계 전달
    startDate=start_date.strftime("%Y-%m-%d"),
    endDate=end_date.strftime("%Y-%m-%d"),
  )
