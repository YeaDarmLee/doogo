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

  # 보정: 시작 > 종료면 서로 스왑
  if start_date > end_date:
    start_date, end_date = end_date, start_date
  
  status, resp = list_settlements(start_date, end_date)

  return render_template(
    "settlements.html",
    pageName="settlements",
    items=resp,
    startDate=start_date.strftime("%Y-%m-%d"),
    endDate=end_date.strftime("%Y-%m-%d"),
  )
