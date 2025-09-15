# application/src/service/supplier.py
# -*- coding: utf-8 -*-
from flask import Blueprint, render_template, request, jsonify
from sqlalchemy.exc import SQLAlchemyError
import os, requests

from application.src.models.SupplierList import SupplierList
from application.src.repositories.SupplierListRepository import (
  SupplierListRepository,
  STATE_PENDING, STATE_APPROVED, STATE_REJECTED
)

# Cafe24 OAuth 토큰 서비스 사용(※ 토큰은 여기서 동적으로 발급/갱신)
from application.src.service.cafe24_oauth_service import get_access_token

supplier = Blueprint("supplier", __name__, url_prefix="/supplier")

# ====== 환경 ======
CAFE24_BASE_URL     = os.getenv("CAFE24_BASE_URL")            # 예: https://onedayboxb2b.cafe24api.com

def _cafe24_headers():
  """
  Cafe24 Admin API 헤더 구성
  - Authorization 은 oauth_service.get_access_token() 으로 매 호출시 최신 토큰 확보
  """
  access_token = get_access_token()  # DB refresh_token 기반으로 access_token 재발급/캐시
  return {
    "Authorization": f"Bearer {access_token}",
    "Content-Type": "application/json"
  }


# -----------------------------------
# View: 공급사 관리 페이지
# -----------------------------------
@supplier.route("/", methods=["GET"])
def index():
  items = SupplierListRepository.findApproved()

  def to_Dict(x: SupplierList) -> dict:
    return {
      "seq": x.seq,
      "companyName": x.companyName or "",
      "supplierCode": x.supplierCode or "",
      "stateCode": getattr(x, "stateCode", "") or "",
      "channelId": x.channelId or "",
      "contractStatus": x.contractStatus or "",
      "supplierID": x.supplierID or "",
      "supplierPW": x.supplierPW or "",
      "supplierURL": x.supplierURL or "",
      "manager": x.manager or "",
      "managerRank": x.managerRank or "",
      "number": x.number or "",
      "email": x.email or ""
    }

  return render_template("supplier.html", pageName="supplier", supplierList=[to_Dict(s) for s in items])

# -----------------------------------
# Ajax: 등록
# -----------------------------------
@supplier.route("/ajax/addSupplier", methods=["POST"])
def addSupplier():
  try:
    data = request.get_json(silent=True) or request.form

    def g(k):
      v = data.get(k)
      return v.strip() if isinstance(v, str) else v

    company_name = g("supplierCompanyName") or ""
    supplier_code = g("supplierCode") or ""
    supplier_id  = g("supplierID") or ""   # 자유형식 + 최소 6자
    supplier_pw  = g("supplierPW") or None
    supplier_url = g("supplierURL") or None
    manager      = g("supplierManager") or None
    manager_rank = g("supplierManagerRank") or None
    number       = g("supplierNumber") or None
    email        = g("supplierEmail") or None

    # 검증 (프런트와 일치)
    errors = {}
    if not company_name:
      errors["supplierCompanyName"] = "회사명은 필수입니다."
    if not supplier_id or len(supplier_id) < 6:
      errors["supplierID"] = "ID는 6자 이상 입력해 주세요."
    if errors:
      return jsonify({"code": 40001, "errors": errors}), 400

    s = SupplierList(
      companyName=company_name,
      supplierCode=supplier_code,
      supplierID=supplier_id,
      supplierPW=supplier_pw,
      supplierURL=supplier_url,
      manager=manager,
      managerRank=manager_rank,
      number=number,
      email=email,
      stateCode=STATE_PENDING
    )
    SupplierListRepository.save(s)
    return jsonify({"code": 20000, "seq": s.seq})

  except SQLAlchemyError as e:
    SupplierListRepository.rollback_if_needed()
    return jsonify({"code": 50001, "message": "DB 오류", "detail": str(e.__Dict__.get('orig') or e)}), 500
  except Exception as e:
    SupplierListRepository.rollback_if_needed()
    return jsonify({"code": 50000, "message": "예외 발생", "detail": str(e)}), 500

# -----------------------------------
# Ajax: 단건 조회(수정 프리필)
# -----------------------------------
@supplier.route("/ajax/<int:seq>", methods=["GET"])
def getSupplier(seq: int):
  s = SupplierListRepository.findBySeq(seq)
  if not s:
    return jsonify({"code": 40400, "message": "존재하지 않는 공급사입니다."}), 404

  def to_Dict(x: SupplierList) -> dict:
    return {
      "seq": x.seq,
      "companyName": x.companyName or "",
      "supplierCode": x.supplierCode or "",
      "supplierID": x.supplierID or "",
      "supplierPW": "",  # 보안상 미노출
      "supplierURL": x.supplierURL or "",
      "manager": x.manager or "",
      "managerRank": x.managerRank or "",
      "number": x.number or "",
      "email": x.email or "",
      "stateCode": getattr(x, "stateCode", "") or "",
      "updatedAt": x.updatedAt.isoformat() if getattr(x, "updatedAt", None) else None
    }

  return jsonify({"code": 20000, "item": to_Dict(s)})

# -----------------------------------
# Ajax: 수정(낙관적 잠금)
# -----------------------------------
@supplier.route("/ajax/update", methods=["POST"])
def updateSupplier():
  try:
    data = request.get_json(silent=True) or request.form

    def g(k):
      v = data.get(k)
      return v.strip() if isinstance(v, str) else v

    seq = int(g("seq") or 0)
    expected_updated_at = g("updatedAt")

    s = SupplierListRepository.findBySeq(seq)
    if not s:
      return jsonify({"code": 40400, "message": "존재하지 않는 공급사입니다."}), 404

    # 낙관적 잠금(선택): 클라이언트가 보낸 updatedAt과 현재 DB값 비교
    if expected_updated_at and s.updatedAt and s.updatedAt.isoformat() != expected_updated_at:
      return jsonify({"code": 40900, "message": "다른 사용자가 먼저 수정했습니다. 새로고침 후 다시 시도해 주세요."}), 409

    company_name = g("companyName") or ""
    supplier_code = g("supplierCode") or ""
    supplier_id  = g("supplierID") or ""      # 자유형식 + 최소 6자
    supplier_pw  = g("supplierPW")            # 공란이면 변경하지 않음
    supplier_url = g("supplierURL") or None
    manager      = g("manager") or None
    manager_rank = g("managerRank") or None
    number       = g("number") or None
    email        = g("email") or None

    # 서버 검증
    errors = {}
    if not company_name:
      errors["companyName"] = "회사명은 필수입니다."
    if not supplier_id or len(supplier_id) < 6:
      errors["supplierID"] = "ID는 6자 이상 입력해 주세요."
    if errors:
      return jsonify({"code": 40001, "errors": errors}), 400

    # 반영 (PW는 공란이면 유지)
    s.companyName = company_name
    s.supplierCode = supplier_code
    s.supplierID  = supplier_id
    if supplier_pw is not None and supplier_pw != "":
      s.supplierPW = supplier_pw
    s.supplierURL = supplier_url
    s.manager = manager
    s.managerRank = manager_rank
    s.number = number
    s.email = email

    SupplierListRepository.save(s)
    return jsonify({"code": 20000, "seq": s.seq,
                    "updatedAt": s.updatedAt.isoformat() if getattr(s, "updatedAt", None) else None})

  except SQLAlchemyError as e:
    SupplierListRepository.rollback_if_needed()
    return jsonify({"code": 50001, "message": "DB 오류", "detail": str(e.__Dict__.get('orig') or e)}), 500
  except Exception as e:
    SupplierListRepository.rollback_if_needed()
    return jsonify({"code": 50000, "message": "예외 발생", "detail": str(e)}), 500

# -----------------------------------
# Ajax: 리스트(JSON) - 프런트가 POST 호출중
# -----------------------------------
@supplier.route("/ajax/getSupplierList", methods=["POST"])
def listSuppliers():
  items = SupplierListRepository.findAll()

  def to_Dict(x: SupplierList) -> dict:
    return {
      "seq": x.seq,
      "companyName": x.companyName or "",
      "supplierCode": x.supplierCode or "",
      "stateCode": getattr(x, "stateCode", "") or "",
      "channelId": x.channelId or "",
      "contractStatus": x.contractStatus or "",
      "supplierID": x.supplierID or "",
      "supplierPW": x.supplierPW or "",
      "supplierURL": x.supplierURL or "",
      "manager": x.manager or "",
      "managerRank": x.managerRank or "",
      "number": x.number or "",
      "email": x.email or ""
    }

  return jsonify({"code": 20000, "supplierList": [to_Dict(s) for s in items]})

# -----------------------------------
# View: 공급사 승인 페이지
# -----------------------------------
@supplier.route("/approval", methods=["GET"])
def approval_view():
  # 초기 화면에는 '대기'만 내려줌
  pending = SupplierListRepository.find_pending(limit=100)

  def to_dict(x: SupplierList) -> dict:
    return {
      "seq": x.seq,
      "companyName": x.companyName or "",
      "supplierCode": x.supplierCode or "",
      "stateCode": getattr(x, "stateCode", "") or "",
      "manager": x.manager or "",
      "number": x.number or "",
      "email": x.email or "",
      "updatedAt": x.updatedAt.isoformat() if getattr(x, "updatedAt", None) else None
    }

  return render_template("supplier_approval.html",
                         pageName="supplier_approval",
                         supplierList=[to_dict(s) for s in pending])

# -----------------------------------
# Ajax: 승인/반려 목록 조회(필터+페이지네이션)
# body: { states?: ["P","A","R"], limit?: 50, offset?: 0 }
# -----------------------------------
@supplier.route("/ajax/approval/list", methods=["POST"])
def approval_list():
  data = request.get_json(silent=True) or {}
  states = data.get("states") or [STATE_PENDING]
  limit = int(data.get("limit") or 50)
  offset = int(data.get("offset") or 0)

  items = SupplierListRepository.find_by_states(states, limit=limit, offset=offset)

  def to_dict(x: SupplierList) -> dict:
    return {
      "seq": x.seq,
      "companyName": x.companyName or "",
      "supplierCode": x.supplierCode or "",
      "stateCode": getattr(x, "stateCode", "") or "",
      "manager": x.manager or "",
      "number": x.number or "",
      "email": x.email or "",
      "updatedAt": x.updatedAt.isoformat() if getattr(x, "updatedAt", None) else None
    }

  return jsonify({"code": 20000, "items": [to_dict(s) for s in items]})

# -----------------------------------
# Ajax: 단건 승인/반려
# body: { seq: number, action: "approve"|"reject" }
# -----------------------------------
@supplier.route("/ajax/approval/set", methods=["POST"])
def approval_set():
  data = request.get_json(silent=True) or {}
  seq = int(data.get("seq") or 0)
  action = (data.get("action") or "").strip().lower()

  s = SupplierListRepository.findBySeq(seq)
  if not s:
    return jsonify({"code": 40400, "message": "존재하지 않는 공급사입니다."}), 404

  target = STATE_APPROVED if action == "approve" else STATE_REJECTED
  SupplierListRepository.update_state(seq, target)
  return jsonify({"code": 20000, "seq": seq, "stateCode": target})

# -----------------------------------
# Ajax: 일괄 승인/반려
# body: { seqs: number[], action: "approve"|"reject" }
# -----------------------------------
@supplier.route("/ajax/approval/bulkSet", methods=["POST"])
def approval_bulk_set():
  data = request.get_json(silent=True) or {}
  seqs = list({int(x) for x in (data.get("seqs") or []) if str(x).isdigit()})
  action = (data.get("action") or "").strip().lower()

  if not seqs:
    return jsonify({"code": 40001, "message": "선택된 대상이 없습니다."}), 400

  target = STATE_APPROVED if action == "approve" else STATE_REJECTED
  n = SupplierListRepository.bulk_update_state(seqs, target)
  return jsonify({"code": 20000, "updated": n, "stateCode": target})


# -----------------------------------
# Ajax: Cafe24 공급사 생성 프록시
# body: { seq: number }
# -----------------------------------
@supplier.route("/ajax/cafe24/createSupplier", methods=["POST"])
def cafe24_create_supplier():
  data = request.get_json(silent=True) or {}
  seq = int(data.get("seq") or 0)
  if not seq:
    return jsonify({"code": 40001, "message": "seq가 필요합니다."}), 400

  s = SupplierListRepository.findBySeq(seq)

  if not s:
    return jsonify({"code": 40400, "message": "존재하지 않는 공급사입니다."}), 404

  if not CAFE24_BASE_URL:
    return jsonify({"code": 50010, "message": "CAFE24_BASE_URL 환경변수가 없습니다."}), 500

  # 안전 문자열
  def _safe(v): return (v or "").strip() if isinstance(v, str) else (v if v is not None else "")

  # ====== 매핑 규칙 ======
  # supplier_name -> companyName
  # manager_information[0].no -> 1 (고정)
  # manager_information[0].name -> manager
  # manager_information[0].phone -> number
  # manager_information[0].email -> email
  # trading_type -> "D"
  # payment_period -> "A"
  # phone -> number
  # company_name -> companyName
  payload = {
    "shop_no": 1,
    "request": {
      "supplier_name": _safe(s.companyName),
      "manager_information": [{
        "no": 1,
        "name": _safe(s.manager),
        "phone": _safe(s.number),
        "email": _safe(s.email)
      }],
      "trading_type": "D",
      # "payment_period": "A", # 월 정산
      "phone": _safe(s.number),
      "company_name": _safe(s.companyName)
    }
  }

  url = f"{CAFE24_BASE_URL.rstrip('/')}/api/v2/admin/suppliers"

  try:
    resp = requests.post(url, headers=_cafe24_headers(), json=payload, timeout=20)
    try:
      body = resp.json()
      print(body)
    except Exception:
      body = {"raw": resp.text}

    if resp.status_code not in (200, 201):
      # Cafe24는 오류 상세를 본문에 넣어주는 경우가 많음 → 그대로 전달
      return jsonify({
        "code": 50011,
        "message": f"Cafe24 API 오류(status={resp.status_code})",
        "detail": body
      }), 200  # 프런트에서 code로 판정

    # 성공(필요 시 body 의 식별자를 DB에 저장하는 확장 가능)
    return jsonify({"code": 20000, "result": body})
  except requests.RequestException as e:
    return jsonify({"code": 50012, "message": "Cafe24 호출 실패", "detail": str(e)}), 200