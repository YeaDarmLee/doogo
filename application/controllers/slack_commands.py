# application/controllers/slack_commands.py
# -*- coding: utf-8 -*-
from flask import Blueprint, request, jsonify, make_response, current_app
from datetime import datetime
import requests, threading, traceback, json, os
from typing import Optional

from slack_sdk import WebClient

from application.src.service.slack_verify import verify_slack_request
from application.src.service.slack_sales_service import fetch_sales_summary, first_day_of_month
from application.src.service.settlement_service import make_settlement_excel, prev_month_range

from application.src.repositories.SupplierListRepository import SupplierListRepository

slack_commands = Blueprint("slack_commands", __name__, url_prefix="/slack/commands")


# -------------------- 공통 유틸 --------------------
def _fmt_currency(value):
  try:
    if value in (None, "", "None"):
      return "0원"
    return f"{int(float(str(value).replace(',', '') )):,}원"
  except Exception:
    return f"{value}원"

def _post_to_response_url(response_url: str, payload: dict):
  try:
    print(f"[slash] POST response_url payload_keys={list(payload.keys())}")
    requests.post(response_url, json=payload, timeout=10)
  except Exception:
    traceback.print_exc()

def _parse_period_text(text: str):
  if not text:
    return None
  try:
    if "~" not in text:
      return None
    a, b = [x.strip() for x in text.split("~", 1)]
    y1, m1, d1 = [int(x) for x in a.split("-")]
    y2, m2, d2 = [int(x) for x in b.split("-")]
    from datetime import date as _D
    return _D(y1, m1, d1), _D(y2, m2, d2)
  except Exception:
    return None

# -------------------- /sales --------------------
def _build_result_blocks(title: str, s: dict):
  """
  s: fetch_sales_summary 반환 dict
  출력 구성:
  - 주문 요약
  - 매출 요약
  - 수수료/정산
  - 수량 요약
  """
  header = { "type": "section", "text": { "type": "mrkdwn", "text": f"*{title}*" } }
  div = { "type": "divider" }

  orders_lines = "\n".join([
    f"• *총주문:* {s.get('orders',0)}건",
    f"• *판매주문:* {s.get('orders_sold',0)}건",
    f"• *취소주문:* {s.get('orders_canceled',0)}건",
  ])

  sales_lines = "\n".join([
    f"• *총매출:* {_fmt_currency(s.get('gross_amount',0))}",
    f"• *취소매출:* {_fmt_currency(s.get('cancel_amount',0))}",
    f"• *판매매출:* {_fmt_currency(s.get('sale_amount',0))}",
  ])

  settle_lines = "\n".join([
    f"• *배송비:* {_fmt_currency(s.get('shipping_amount',0))}",
    f"• *수수료(15%):* {_fmt_currency(s.get('commission_amount',0))}",
    f"• *정산금액:* {_fmt_currency(s.get('net_amount',0))}",
  ])

  qty_lines = "\n".join([
    f"• *총판매수량:* {s.get('items',0)}개",
    f"• *판매수량:* {s.get('items_sold',0)}개",
    f"• *취소수량:* {s.get('items_canceled',0)}개",
  ])

  return [
    header, div,
    { "type": "section", "text": { "type": "mrkdwn", "text": "*주문 요약*\n" + orders_lines } },
    div,
    { "type": "section", "text": { "type": "mrkdwn", "text": "*매출 요약*\n" + sales_lines } },
    div,
    { "type": "section", "text": { "type": "mrkdwn", "text": "*수수료/정산*\n" + settle_lines } },
    div,
    { "type": "section", "text": { "type": "mrkdwn", "text": "*수량 요약*\n" + qty_lines } },
  ]

def _resolve_supplier_code_by_channel(channel_id: str) -> Optional[str]:
  try:
    supplier = SupplierListRepository.find_by_channel_id(channel_id)
    if supplier and getattr(supplier, "supplierCode", None):
      return supplier.supplierCode
  except Exception as e:
    print(f"[slash:/sales] resolve supplierCode error: {e}")
  return None

def _worker_compute_and_respond_sales(app, form: dict):
  with app.app_context():
    response_url = form.get("response_url")
    channel_id = form.get("channel_id")
    channel_name = form.get("channel_name")
    user_id = form.get("user_id")
    text = (form.get("text") or "").strip()

    print(f"[slash:/sales] user={user_id} channel={channel_name}({channel_id}) text={text!r}")

    supplier_code = _resolve_supplier_code_by_channel(channel_id) if channel_id else None
    if supplier_code:
      print(f"[slash:/sales] supplierCode={supplier_code}")
    else:
      print(f"[slash:/sales] supplier mapping not found for channel={channel_id}")

    parsed = _parse_period_text(text)
    if parsed:
      start, end = parsed
      title_prefix = "매출 요약"
    else:
      today = datetime.now().date()
      start = first_day_of_month(today); end = today
      title_prefix = "이번 달 매출 요약"

    print(f"[slash:/sales] period {start} ~ {end}")

    try:
      summary = fetch_sales_summary(start, end, supply_id=supplier_code)
      print(f"[slash:/sales] summary={summary}")

      title = f"{title_prefix} ({start.isoformat()} ~ {end.isoformat()})"
      blocks = _build_result_blocks(title, summary)

      if response_url:
        _post_to_response_url(response_url, {
          "response_type": "ephemeral",
          "replace_original": True,
          "blocks": blocks
        })
    except Exception:
      traceback.print_exc()
      if response_url:
        _post_to_response_url(response_url, {
          "response_type": "ephemeral",
          "replace_original": True,
          "text": ":warning: 데이터 조회 중 오류가 발생했습니다."
        })

@slack_commands.route("/sales", methods=["POST"])
def slash_sales():
  if not verify_slack_request(request):
    return make_response("invalid signature", 401)

  form = request.form or {}
  response_url = form.get("response_url")
  text = (form.get("text") or "").strip()
  print(f"[slash:/sales] form_keys={list(form.keys())} text={text!r}")

  # ACK 메시지에 기간 표기
  parsed = _parse_period_text(text)
  if parsed:
    start, end = parsed
    ack_text = f"매출을 조회하는 중입니다… ({start} ~ {end}) :hourglass_flowing_sand:"
  else:
    today = datetime.now().date()
    start = first_day_of_month(today); end = today
    ack_text = f"매출을 조회하는 중입니다… (이번 달 {start} ~ {end}) :hourglass_flowing_sand:"

  ack = { "response_type": "ephemeral", "text": ack_text }

  if response_url:
    app_obj = current_app._get_current_object()
    t = threading.Thread(target=_worker_compute_and_respond_sales, args=(app_obj, form), daemon=True)
    t.start()
  else:
    print("[slash:/sales] response_url missing -> cannot update message asynchronously")

  return jsonify(ack)

# -------------------- /settlement --------------------
@slack_commands.route("/settlement", methods=["POST"])
def slash_settlement():
  if not verify_slack_request(request):
    return make_response("invalid signature", 401)

  form = request.form or {}
  response_url = form.get("response_url")
  channel_id = form.get("channel_id")
  channel_name = form.get("channel_name")
  user_id = form.get("user_id")
  text = (form.get("text") or "").strip()

  print(f"[slash:/settlement] user={user_id} channel={channel_name}({channel_id}) text={text!r}")

  # 채널 → 공급사 (supplierCode 우선)
  supplier = SupplierListRepository.find_by_channel_id(channel_id) if channel_id else None
  supply_id = None
  if supplier:
    supply_id = getattr(supplier, "supplierCode", None)
  print(f"[slash:/settlement] supplier={getattr(supplier,'companyName',None)} supply_id={supply_id}")

  # 기간: 기본=지난달, 텍스트 "YYYY-MM-DD~YYYY-MM-DD" 허용
  def _parse_period(s: str):
    try:
      a, b = [x.strip() for x in s.split("~")]
      y1,m1,d1 = [int(x) for x in a.split("-")]
      y2,m2,d2 = [int(x) for x in b.split("-")]
      from datetime import date as _D
      return _D(y1,m1,d1), _D(y2,m2,d2)
    except Exception:
      return None

  today = datetime.now().date()
  if text and "~" in text:
    p = _parse_period(text)
    start, end = p if p else prev_month_range(today)
  else:
    start, end = prev_month_range(today)

  print(f"[slash:/settlement] period {start} ~ {end}")

  ack = {
    "response_type": "ephemeral",
    "text": f"정산 파일을 생성 중입니다… ({start} ~ {end}) :hourglass_flowing_sand:"
  }

  def _bg(app):
    with app.app_context():
      try:
        fpath, summary = make_settlement_excel(start, end, supply_id=supply_id, out_dir="/tmp")
        print(f"[slash:/settlement] excel_ready path={fpath} summary={summary}")

        bot_token = os.getenv("SLACK_BOT_TOKEN")
        cli = WebClient(token=bot_token)

        # 비공개 채널 대비 선참여 시도 (이미 참여 상태면 무시)
        try:
          cli.conversations_join(channel=channel_id)
        except Exception:
          pass

        initial_comment = (
          f"*정산서 업로드 완료*\n기간: {start} ~ {end}\n"
          f"배송완료 {summary['delivered_rows']}행 · 취소처리 {summary['canceled_rows']}행"
        )
        cli.files_upload_v2(
          channel=channel_id,  # 단수
          file=fpath,
          filename=os.path.basename(fpath),
          title=f"{start:%Y-%m} 정산서",
          initial_comment=initial_comment
        )
      except Exception as e:
        traceback.print_exc()
        if response_url:
          _post_to_response_url(response_url, {
            "response_type": "ephemeral",
            "replace_original": False,
            "text": f":warning: 정산 파일 생성/업로드 중 오류가 발생했습니다.\n{e}"
          })

  if response_url:
    app_obj = current_app._get_current_object()
    t = threading.Thread(target=_bg, args=(app_obj,), daemon=True)
    t.start()
  else:
    print("[slash:/settlement] response_url missing -> cannot update message asynchronously")

  return jsonify(ack)