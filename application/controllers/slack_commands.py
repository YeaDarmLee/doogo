# application/src/service/slack_commands.py
from flask import Blueprint, request, jsonify, make_response, current_app
from datetime import datetime, date
import requests, threading, traceback, json
from typing import Optional

from application.src.service.slack_verify import verify_slack_request
from application.src.service.sales_service import fetch_sales_summary, first_day_of_month, fetch_order_list
from application.src.repositories.SupplierListRepository import SupplierListRepository

slack_commands = Blueprint("slack_commands", __name__, url_prefix="/slack/commands")

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

def _build_result_blocks(title: str, orders: int, gross: int, net: Optional[int], items: Optional[int]):
  lines = [
    f"*주문건수:* {orders}건",
    f"*총매출:* {_fmt_currency(gross)}"
  ]
  if net is not None:
    lines.append(f"*정산예상:* {_fmt_currency(net)}")
  if items is not None:
    lines.append(f"*판매수량:* {items}개")

  return [
    { "type": "section", "text": { "type": "mrkdwn", "text": f"*{title}*" } },
    { "type": "divider" },
    { "type": "section", "text": { "type": "mrkdwn", "text": "\n".join(lines) } }
  ]

def _resolve_supplier_code_by_channel(channel_id: str) -> Optional[str]:
  """
  채널ID로 공급사 찾기
  - 반드시 supplier.supplierCode 사용 (요청 사항)
  """
  try:
    supplier = SupplierListRepository.find_by_channel_id(channel_id)
    if supplier and getattr(supplier, "supplierCode", None):
      return supplier.supplierCode
  except Exception as e:
    print(f"[slash:/sales] resolve supplierCode error: {e}")
  return None

def _worker_compute_and_respond(app, form: dict):
  """
  백그라운드 스레드: 반드시 app.app_context() 안에서 DB 호출
  """
  with app.app_context():
    response_url = form.get("response_url")
    channel_id = form.get("channel_id")
    channel_name = form.get("channel_name")
    user_id = form.get("user_id")

    print(f"[slash:/sales] user={user_id} channel={channel_name}({channel_id})")

    # 채널ID로 공급사 찾기 (없으면 전체 집계)
    supplier_code = _resolve_supplier_code_by_channel(channel_id) if channel_id else None
    if supplier_code:
      print(f"[slash:/sales] supplierCode={supplier_code}")
    else:
      print(f"[slash:/sales] supplier mapping not found for channel={channel_id}")

    today = datetime.now().date()
    start = first_day_of_month(today)
    end = today
    print(f"[slash:/sales] period {start} ~ {end}")

    try:
      # supply_id ← supplierCode를 그대로 전달
      summary = fetch_sales_summary(start, end, supply_id=supplier_code)
      print(f"[slash:/sales] summary={summary}")

      title = f"이번 달 매출 요약 ({start.isoformat()} ~ {end.isoformat()})"
      blocks = _build_result_blocks(
        title=title,
        orders=summary.get("orders", 0),
        gross=summary.get("gross_amount", 0),
        net=summary.get("net_amount"),
        items=summary.get("items"),
      )
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
  print(f"[slash:/sales] form_keys={list(form.keys())}")

  ack = {
    "response_type": "ephemeral",
    "text": "매출을 조회하는 중입니다… 잠시만 기다려 주세요 :hourglass_flowing_sand:"
  }

  if response_url:
    app_obj = current_app._get_current_object()
    t = threading.Thread(target=_worker_compute_and_respond, args=(app_obj, form), daemon=True)
    t.start()
  else:
    print("[slash:/sales] response_url missing -> cannot update message asynchronously")

  return jsonify(ack)

@slack_commands.route("/sales_detail", methods=["POST"])
def slash_sales_detail():
  if not verify_slack_request(request):
    return make_response("invalid signature", 401)

  form = request.form or {}
  channel_id = form.get("channel_id")
  channel_name = form.get("channel_name")
  user_id = form.get("user_id")

  print(f"[slash:/sales_detail] user={user_id} channel={channel_name}({channel_id})")

  supplier_code = None
  if channel_id:
    supplier = SupplierListRepository.find_by_channel_id(channel_id)
    if supplier and getattr(supplier, "supplierCode", None):
      supplier_code = supplier.supplierCode
      print(f"[slash:/sales_detail] supplierCode={supplier_code}")
    else:
      print("[slash:/sales_detail] supplier mapping not found")

  today = datetime.now().date()
  start = first_day_of_month(today)
  end = today
  print(f"[slash:/sales_detail] period {start} ~ {end}")

  try:
    orders = fetch_order_list(start, end, supply_id=supplier_code)
    print(f"[slash:/sales_detail] fetched orders={len(orders)}")
  except Exception as e:
    traceback.print_exc()
    return jsonify({
      "response_type": "ephemeral",
      "text": f":x: 오류가 발생해 */매출상세* 조회에 실패했습니다.\n{e}"
    })

  if not orders:
    return jsonify({
      "response_type": "ephemeral",
      "text": f"이번 달 주문 내역이 없습니다. ({start} ~ {end})"
    })

  page_size = 10
  offset = 0
  slice_orders = orders[offset:offset+page_size]

  blocks = [
    { "type": "section", "text": { "type": "mrkdwn", "text": f"*이번 달 주문 상세 ({start} ~ {end})*" } },
    { "type": "divider" }
  ]

  for o in slice_orders:
    gross = o.get("order_price") or o.get("payment_amount")
    net = o.get("payment_amount")
    pay = o.get("payment_method") or "-"
    blocks.append({
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": (
          f"*주문ID:* {o['order_id']}\n"
          f"*주문일시:* {o['order_date']}\n"
          f"*총액:* {_fmt_currency(gross)} / *결제액:* {_fmt_currency(net)}\n"
          f"*결제방식:* {pay}"
        )
      }
    })
    blocks.append({ "type": "divider" })

  if len(orders) > page_size:
    cursor = {
      "start": start.isoformat(),
      "end": end.isoformat(),
      "supply_id": supplier_code,
      "offset": page_size,
      "page_size": page_size
    }
    blocks.append({
      "type": "actions",
      "elements": [
        {
          "type": "button",
          "action_id": "sales_detail_more",
          "text": { "type": "plain_text", "text": "더보기" },
          "value": json.dumps(cursor)
        }
      ]
    })

  return jsonify({
    "response_type": "ephemeral",
    "blocks": blocks
  })

@slack_commands.route("/interactive", methods=["POST"])
def interactive():
  if not verify_slack_request(request):
    return make_response("invalid signature", 401)

  payload_raw = request.form.get("payload")
  if not payload_raw:
    print("[interactive] payload missing")
    return jsonify({})

  payload = json.loads(payload_raw)
  action = (payload.get("actions") or [{}])[0]
  action_id = action.get("action_id")
  response_url = payload.get("response_url")
  print(f"[interactive] action_id={action_id}")

  if action_id != "sales_detail_more":
    print("[interactive] unsupported action_id")
    return jsonify({})

  try:
    cursor = json.loads(action.get("value") or "{}")
    start = cursor.get("start")
    end = cursor.get("end")
    supply_id = cursor.get("supply_id")
    offset = int(cursor.get("offset", 0))
    page_size = int(cursor.get("page_size", 10))
    print(f"[interactive] cursor start={start} end={end} supply_id={supply_id} offset={offset} size={page_size}")

    from datetime import date as _D
    def _to_date(s): y, m, d = [int(x) for x in s.split("-")]; return _D(y, m, d)

    orders = fetch_order_list(_to_date(start), _to_date(end), supply_id=supply_id)
    next_slice = orders[offset:offset+page_size]
    print(f"[interactive] next_slice={len(next_slice)} / total={len(orders)}")

    more_blocks = []
    for o in next_slice:
      gross = o.get("order_price") or o.get("payment_amount")
      net = o.get("payment_amount")
      pay = o.get("payment_method") or "-"
      more_blocks.append({
        "type": "section",
        "text": {
          "type": "mrkdwn",
          "text": (
            f"*주문ID:* {o['order_id']}\n"
            f"*주문일시:* {o['order_date']}\n"
            f"*총액:* {_fmt_currency(gross)} / *결제액:* {_fmt_currency(net)}\n"
            f"*결제방식:* {pay}"
          )
        }
      })
      more_blocks.append({ "type": "divider" })

    new_offset = offset + page_size
    if new_offset < len(orders):
      next_cursor = {
        "start": start,
        "end": end,
        "supply_id": supply_id,
        "offset": new_offset,
        "page_size": page_size
      }
      more_blocks.append({
        "type": "actions",
        "elements": [
          {
            "type": "button",
            "action_id": "sales_detail_more",
            "text": { "type": "plain_text", "text": "더보기" },
            "value": json.dumps(next_cursor)
          }
        ]
      })

    _post_to_response_url(response_url, {
      "response_type": "ephemeral",
      "replace_original": False,
      "blocks": more_blocks
    })
  except Exception:
    traceback.print_exc()
    if response_url:
      _post_to_response_url(response_url, {
        "response_type": "ephemeral",
        "replace_original": False,
        "text": ":warning: 더보기 처리 중 오류가 발생했습니다."
      })

  return jsonify({})
