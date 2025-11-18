# -*- coding: utf-8 -*-
from datetime import datetime, date
from flask import Blueprint, request, jsonify

from application.src.service.slack_service import upload_file_with_button

settlement_api = Blueprint("settlement_api", __name__, url_prefix="/api/settlement")


def _parse_date(value):
  """
  start / end 로 들어오는 값을 date로 통일.
  - "2025-11-01" 형식 문자열 권장
  - date / datetime 도 받아서 date로 변환
  """
  if isinstance(value, date) and not isinstance(value, datetime):
    return value
  if isinstance(value, datetime):
    return value.date()
  if isinstance(value, str):
    # "2025-11-01T00:00:00" 같이 올 수도 있으니 앞 10자리만 자르고 파싱
    s = value.strip()[:10]
    return datetime.strptime(s, "%Y-%m-%d").date()
  raise ValueError(f"invalid date: {value!r}")


@settlement_api.route("/run", methods=["POST"])
def run_settlement_manual():
  """
  수동 정산 슬랙 발송용 엔드포인트

  요청(JSON 예시)
  {
    "supply_id": "S00000KF",
    "channel": "C0123456789"   // 또는 "#정산-농업회사법인"
    "start": "2025-11-01",
    "end": "2025-11-15"
  }

  동작
  - make_settlement_excel(start, end, supply_id) 호출
  - upload_file_with_button(...) 으로 슬랙 업로드 + 버튼 전송
  - 파일 경로와 summary를 그대로 돌려줌
  """
  data = request.get_json(silent=True) or {}

  supply_id = (data.get("supply_id") or "").strip()
  channel = (data.get("channel") or "").strip()
  start_raw = data.get("start")
  end_raw = data.get("end")

  if not supply_id or not channel or not start_raw or not end_raw:
    return jsonify({
      "ok": False,
      "message": "supply_id, channel, start, end는 모두 필수입니다.",
    }), 400

  try:
    start = _parse_date(start_raw)
    end = _parse_date(end_raw)
  except Exception:
    return jsonify({
      "ok": False,
      "message": "start/end는 YYYY-MM-DD 형식 문자열 또는 date여야 합니다.",
    }), 400

  # 여기서 바로 정산 엑셀 생성 + 슬랙 업로드 + 버튼 메시지
  fpath, summary = upload_file_with_button(
    supply_id=supply_id,
    channel=channel,
    start=start,
    end=end,
  )

  return jsonify({
    "ok": True,
    "file_path": fpath,
    "summary": summary,
  }), 200
