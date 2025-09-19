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

    # 기존 필드
    company_name = g("supplierCompanyName") or ""
    supplier_code = g("supplierCode") or ""
    supplier_id  = g("supplierID") or ""
    supplier_pw  = g("supplierPW") or None
    supplier_url = g("supplierURL") or None
    manager      = g("supplierManager") or None
    manager_rank = g("supplierManagerRank") or None
    number       = g("supplierNumber") or None
    email        = g("supplierEmail") or None

    # 신규: 계약 필드
    contract_template = (g("contractTemplate") or "").upper()  # '', 'A', 'B'
    contract_skip     = 1 if str(g("contractSkip") or "0") in ("1","true","True") else 0

    # 숫자 파싱 유틸
    def to_decimal(val):
      try:
        return None if val in (None, "") else round(float(val), 2)
      except Exception:
        return None

    def to_int(val):
      try:
        return None if val in (None, "") else int(val)
      except Exception:
        return None

    contract_percent       = to_decimal(g("contractPercent"))
    contract_threshold     = to_int(g("contractThreshold"))
    contract_percent_under = to_decimal(g("contractPercentUnder"))
    contract_percent_over  = to_decimal(g("contractPercentOver"))

    # 기본 검증 (기존과 동일)
    errors = {}
    if not company_name:
      errors["supplierCompanyName"] = "회사명은 필수입니다."
    if not supplier_id or len(supplier_id) < 6:
      errors["supplierID"] = "ID는 6자 이상 입력해 주세요."

    # 계약 검증 (스킵이면 생략)
    if not contract_skip and contract_template in ("A", "B"):
      if contract_template == "A":
        if contract_percent is None or contract_percent < 0 or contract_percent > 100:
          errors["contractPercent"] = "0~100 사이 수수료(%)를 입력해 주세요."
      elif contract_template == "B":
        if contract_threshold is None or contract_threshold < 0:
          errors["contractThreshold"] = "0 이상의 특정 금액을 입력해 주세요."
        if contract_percent_under is None or contract_percent_under < 0 or contract_percent_under > 100:
          errors["contractPercentUnder"] = "0~100 사이 수수료(%)를 입력해 주세요."
        if contract_percent_over is None or contract_percent_over < 0 or contract_percent_over > 100:
          errors["contractPercentOver"] = "0~100 사이 수수료(%)를 입력해 주세요."

    if errors:
      return jsonify({"code": 40001, "errors": errors}), 400

    # contract_status 결정
    if contract_skip:
      contract_status = "S"    # 이미 체결: 발송 스킵
    elif contract_template in ("A","B"):
      contract_status = "P"    # 템플릿/입력값 확보 → 발송 큐 대상
    else:
      contract_status = ""            # 계약 미선택

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

      # 신규 계약 필드 저장
      contractTemplate=contract_template or None,
      contractPercent=contract_percent,
      contractThreshold=contract_threshold,
      contractPercentUnder=contract_percent_under,
      contractPercentOver=contract_percent_over,
      contractSkip=contract_skip,
      contractStatus=contract_status
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
  import re, decimal
  from decimal import Decimal
  data = request.get_json(silent=True) or {}
  seq = int(data.get("seq") or 0)
  
  if not seq:
    return jsonify({"code": 40001, "message": "seq가 필요합니다."}), 400

  s = SupplierListRepository.findBySeq(seq)
  s.contractTemplate = (data.get("contractTemplate") or "").strip()
  s.contractPercent = (data.get("contractPercent") or "").strip()
  s.contractThreshold = (data.get("contractThreshold") or "").strip()
  s.contractPercentUnder = (data.get("contractPercentUnder") or "").strip()
  s.contractPercentOver = (data.get("contractPercentOver") or "").strip()
  s.settlementPeriod = (data.get("settlementPeriod") or "").strip()
  
  if not s:
    return jsonify({"code": 40400, "message": "존재하지 않는 공급사입니다."}), 404

  if not CAFE24_BASE_URL:
    return jsonify({"code": 50010, "message": "CAFE24_BASE_URL 환경변수가 없습니다."}), 500

  # 안전 문자열
  def _safe(v):
    # 문자열은 strip, 그 외(None 제외)는 그대로 반환
    return (v or "").strip() if isinstance(v, str) else (v if v is not None else "")

  # JSON 직렬화 안전 변환기 (dict/list 깊은 변환)
  def _to_jsonable(obj):
    if isinstance(obj, Decimal):
      # 외부 API에는 문자열로 보내는 편이 안전 (정밀도 유지)
      return str(obj)
    if isinstance(obj, (list, tuple)):
      return [ _to_jsonable(x) for x in obj ]
    if isinstance(obj, dict):
      return { k: _to_jsonable(v) for k, v in obj.items() }
    return obj

  # email 로컬파트로 user_id 만들기
  def _user_id_from_email(email: str) -> str:
    local = (email or "").split("@")[0].lower()
    # 영문/숫자/언더스코어만 허용
    local = re.sub(r"[^a-z0-9_]", "", local)
    # 길이 제한(카페24는 4~16자 권장)
    if len(local) < 4:
      # 회사명으로 보강
      fallback = re.sub(r"[^a-z0-9_]", "", (_safe(s.companyName) or "").lower())
      local = (local + fallback)[:16]
    if len(local) < 4:
      local = f"vendor{seq}"[:16]
    return local[:16]

  # 커미션 템플릿 매핑 (Decimal 가능성 있음)
  template_map = {
    "A": s.contractPercent,
    "B": s.contractPercentOver,
  }
  commission = template_map.get(s.contractTemplate, None)
  if commission is not None and isinstance(commission, (int, float, Decimal)):
    # 문자열로 변환해 전달(예: "15" 또는 "15.0")
    commission = str(commission)

  # 1) 공급사 생성
  create_supplier_payload = {
    "shop_no": 1,
    "request": {
      "supplier_name": _safe(s.companyName),
      "manager_information": [{
        "no": 1,
        "name": _safe(s.manager),
        "email": _safe(s.email)
      }],
      "trading_type": "D",            # 도매(예시)
      "company_name": _safe(s.companyName),
      # commission 은 스펙에 따라 문자열/숫자 허용; 여기서는 문자열로 전달
      **({"commission": commission} if commission is not None else {})
    }
  }
  create_supplier_payload = _to_jsonable(create_supplier_payload)

  suppliers_url = f"{CAFE24_BASE_URL.rstrip('/')}/api/v2/admin/suppliers"

  try:
    resp = requests.post(suppliers_url, headers=_cafe24_headers(), json=create_supplier_payload, timeout=20)
    try:
      body = resp.json()
    except Exception:
      body = {"raw": resp.text}

    if resp.status_code not in (200, 201):
      return jsonify({
        "code": 50011,
        "message": f"Cafe24 API 오류(status={resp.status_code})",
        "detail": body
      }), 200  # 프런트는 code로 판정

    # supplier_code 안전 추출
    supplier_code = None
    if isinstance(body, dict):
      if "suppliers" in body and isinstance(body["suppliers"], list) and body["suppliers"]:
        supplier_code = body["suppliers"][0].get("supplier_code")
      if not supplier_code:
        for k in ("supplier", "data", "result"):
          v = body.get(k)
          if isinstance(v, dict) and v.get("supplier_code"):
            supplier_code = v["supplier_code"]
            break

    if not supplier_code:
      return jsonify({
        "code": 50013,
        "message": "공급사 생성은 성공했지만 supplier_code를 찾지 못했습니다.",
        "detail": body
      }), 200

    # 2) 공급사 운영자(유저) 생성
    # user_id 우선순위: 명시된 supplierID → email 로컬파트 파생
    src_user_id = (_safe(s.supplierID) or "").lower()
    if not src_user_id:
      src_user_id = _user_id_from_email(_safe(s.email))

    # 허용 문자/길이 보정
    src_user_id = re.sub(r"[^a-z0-9_]", "", src_user_id)[:16]
    if len(src_user_id) < 4:
      src_user_id = _user_id_from_email(_safe(s.email))

    create_user_payload = {
      "request": {
        "user_id": src_user_id,
        "supplier_code": supplier_code,
        "password": _safe(s.supplierPW),
        "permission_shop_no": 1,
        "user_name": [
          {
            "shop_no": 1,
            "user_name": _safe(s.manager) or _safe(s.companyName)
          }
        ],
      }
    }

    # 선택 필드 보강
    if _safe(s.email):
      create_user_payload["request"]["email"] = _safe(s.email)
    if _safe(getattr(s, "number", "")):
      # 형식 검증(숫자/+, - 제거 등) 필요시 여기서 정규화
      phone = re.sub(r"[^\d+]", "", _safe(s.number))
      create_user_payload["request"]["phone"] = phone

    create_user_payload = _to_jsonable(create_user_payload)

    users_url = f"{CAFE24_BASE_URL.rstrip('/')}/api/v2/admin/suppliers/users"
    resp2 = requests.post(users_url, headers=_cafe24_headers(), json=create_user_payload, timeout=20)
    try:
      body2 = resp2.json()
    except Exception:
      body2 = {"raw": resp2.text}

    if resp2.status_code not in (200, 201):
      return jsonify({
        "code": 50021,
        "message": f"공급사 생성 성공, 운영자 생성 실패(status={resp2.status_code})",
        "detail": {"supplier_create": body, "user_create": body2}
      }), 200

    s.supplierCode = supplier_code
    SupplierListRepository.save(s)

    return jsonify({
      "code": 20000,
      "result": {
        "supplier_create": body,
        "user_create": body2
      }
    })

  except requests.RequestException as e:
    return jsonify({"code": 50012, "message": "Cafe24 호출 실패", "detail": str(e)}), 200
