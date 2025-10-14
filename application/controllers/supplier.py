# application/src/service/supplier.py
# -*- coding: utf-8 -*-
from flask import Blueprint, render_template, request, jsonify
from flask_jwt_extended import jwt_required
from sqlalchemy.exc import SQLAlchemyError
import os, requests, base64, json, re
from typing import Any, Dict, Optional

from application.src.models.SupplierList import SupplierList
from application.src.models.SupplierDetail import SupplierDetail
from application.src.repositories.SupplierListRepository import (
  SupplierListRepository,
  STATE_PENDING, STATE_APPROVED, STATE_REJECTED
)
from application.src.repositories.SupplierDetailRepository import SupplierDetailRepository
from application.src.service.toss_service import create_seller_encrypted, list_sellers
from application.src.service.eformsign_service import after_slack_success

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

# --- 공통: 상세 머지 유틸 ---
def _merge_supplier_with_detail(s: SupplierList, d: Optional[SupplierDetail]) -> dict:
  def _f(v):
    try:
      return float(v) if v is not None else None
    except Exception:
      return None

  base = {
    "seq": s.seq,
    "companyName": s.companyName or "",
    "supplierCode": getattr(s, "supplierCode", "") or "",
    "stateCode": getattr(s, "stateCode", "") or "",
    "channelId": getattr(s, "channelId", "") or "",
    "contractStatus": getattr(s, "contractStatus", "") or "",
    "supplierID": s.supplierID or "",
    "supplierPW": getattr(s, "supplierPW", "") or "",
    "supplierURL": s.supplierURL or "",
    "manager": s.manager or "",
    "managerRank": s.managerRank or "",
    "number": s.number or "",
    "email": s.email or "",
    "updatedAt": s.updatedAt.isoformat() if getattr(s, "updatedAt", None) else None,
    "contractTemplate": (getattr(s, "contractTemplate", None) or "").upper(),
    "contractPercent": _f(getattr(s, "contractPercent", None)),
    "contractThreshold": getattr(s, "contractThreshold", None),
    "contractPercentUnder": _f(getattr(s, "contractPercentUnder", None)),
    "contractPercentOver": _f(getattr(s, "contractPercentOver", None)),
    "contractSkip": 1 if str(getattr(s, "contractSkip", 0)).lower() in ("1","true") else 0,
  }

  if not d:
    base["detail"] = {
      "businessType": None,
      "companyName": None,
      "representativeName": None,
      "businessRegistrationNumber": None,
      "companyEmail": None,
      "companyPhone": None,
      "bankCode": None,
      "accountNumber": None,
      "holderName": None,
    }
    return base

  base["detail"] = {
    "businessType": d.businessType,
    "companyName": d.companyName,
    "representativeName": d.representativeName,
    "businessRegistrationNumber": d.businessRegistrationNumber,
    "companyEmail": d.companyEmail,
    "companyPhone": d.companyPhone,
    "bankCode": d.bankCode,
    "accountNumber": d.accountNumber,
    "holderName": d.holderName,
    "createdAt": d.createdAt.isoformat() if getattr(d, "createdAt", None) else None,
    "updatedAt": d.updatedAt.isoformat() if getattr(d, "updatedAt", None) else None,
  }
  return base


# -----------------------------------
# View: 공급사 관리 페이지
# -----------------------------------
@supplier.route("/", methods=["GET"])
@jwt_required()
def index():
  items = SupplierListRepository.findApproved()  # 승인된 공급사
  merged = []
  for s in items:
    d = SupplierDetailRepository.findBySupplierSeq(s.seq)
    merged.append(_merge_supplier_with_detail(s, d))
  return render_template("supplier.html", pageName="supplier", supplierList=merged)

# -----------------------------------
# Ajax: 등록
# -----------------------------------
@supplier.route("/ajax/addSupplier", methods=["POST"])
@jwt_required()
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
    
    # 신규: 상세(정산) 필드
    bizno_raw   = g("businessRegistrationNumber") or None
    bank_code   = g("bankCode") or None
    account_no  = g("accountNumber") or None

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

    # 상세 검증(값이 있으면 형식 체크)
    if bizno_raw:
      biz_digits = re.sub(r"\D", "", str(bizno_raw))
      if len(biz_digits) != 10:
        errors["businessRegistrationNumber"] = "사업자등록번호는 숫자 10자리여야 합니다."
    if account_no and len(account_no) > 30:
      errors["accountNumber"] = "계좌번호는 최대 30자까지 입력 가능합니다."
      
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
      # 계약 필드
      contractTemplate=contract_template or None,
      contractPercent=contract_percent,
      contractThreshold=contract_threshold,
      contractPercentUnder=contract_percent_under,
      contractPercentOver=contract_percent_over,
      contractSkip=contract_skip,
      contractStatus=contract_status,
      stateCode='R'
    )
    s = SupplierListRepository.save(s)  # s.seq 확정
    
    sd = SupplierDetail(
      supplierSeq=s.seq,
      businessType='CORPORATE',
      companyName=company_name,
      businessRegistrationNumber = bizno_raw,
      bankCode = bank_code,
      accountNumber = account_no
    )
    
    SupplierDetailRepository.save(sd)
    
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
@jwt_required()
def getSupplier(seq: int):
  s = SupplierListRepository.findBySeq(seq)
  if not s:
    return jsonify({"code": 40400, "message": "존재하지 않는 공급사입니다."}), 404

  d = SupplierDetailRepository.findBySupplierSeq(seq)
  item = _merge_supplier_with_detail(s, d)
  # 보안상 PW는 숨김
  item["supplierPW"] = ""
  return jsonify({"code": 20000, "item": item})

# -----------------------------------
# Ajax: 수정(낙관적 잠금)
# -----------------------------------
@supplier.route("/ajax/update", methods=["POST"])
@jwt_required()
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
    
    # 상세(정산) 필드
    sd = SupplierDetailRepository.findBySupplierSeq(s.seq)
    
    bizno_raw  = g("businessRegistrationNumber") or None
    bank_code  = g("bankCode") or None
    account_no = g("accountNumber") or None
    
    sd.businessRegistrationNumber = bizno_raw
    sd.bankCode = bank_code
    sd.accountNumber = account_no
    
    SupplierDetailRepository.save(sd)
    
    
    
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
@jwt_required()
def listSuppliers():
  items = SupplierListRepository.findAll()
  merged = []
  for s in items:
    d = SupplierDetailRepository.findBySupplierSeq(s.seq)
    merged.append(_merge_supplier_with_detail(s, d))
  return jsonify({"code": 20000, "supplierList": merged})

# -----------------------------------
# View: 공급사 승인 페이지
# -----------------------------------
@supplier.route("/approval", methods=["GET"])
@jwt_required()
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
@jwt_required()
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
@jwt_required()
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
@jwt_required()
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
@jwt_required()
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
    # 3-bulk) 기본상품 등록 (HARDCODED PAYLOAD → Cafe24 제품 생성)
    
    ## Main Code (메인진열코드) ##
    # 2:product_listmain_1: 반가운 신제품 소식-오직 원데이박스 B2B
    # 3:product_listmain_2: 국내 배송
    # 4:product_listmain_3: 해외에서 출고 되는 상품입니다
    # 5:product_listmain_4: 채움앤비움
    # 6:product_listmain_5: 두고푸드
    # 7:product_listmain_6: 뉴질랜드배송
    # 8:product_listmain_7: 원데이박스 사업자 특혜
    # 9:product_listmain_8: 신제품 NEW
    # 10:product_listmain_9: SHORTS
    # 11:product_listmain_10: 대현
    # 12:product_listmain_11: 탭5
    # 13:product_listmain_12: 메인진열1
    # 14:product_listmain_13: 메인진열2
    # 15:product_listmain_14: 메인진열3
    # 16:product_listmain_15: 메인진열4
    # 17:product_listmain_16: 메인진열5
    # 18:product_listmain_17: 메인진열6
    # 19:product_listmain_18: 메인진열7
    # 20:product_listmain_19: 메인진열8
    # 21:product_listmain_20: 메인진열9
    
    ## User Group Code ##
    # 1: 일반 회원
    # 4: 멤버쉽 회원
    # 5: 관리자
    # 6: 총판
    # 7: OEM
    
    ## Icon Code ##
    # custom_9:단종
    # custom_11:품절
    # custom_7:해외배송
    # custom_8:국내배송
    # custom_10:재입고
    # custom_14:배송지연
    # custom_16:모든채널
    # custom_17:무료배송
    # custom_18:위탁배송
    # custom_19:특가할인
    # custom_20:폐쇄몰
    # custom_21:약국전용
    # custom_22:특가할인
    try:
      products_url = f"{CAFE24_BASE_URL.rstrip('/')}/api/v2/admin/products"
      
      ## product_1 ##
      product_1_detail_image_list = cafe24_upload_images(["/web/application/static/img/thumb/product_1_detail_img.jpg"])
      product_1_description = build_description_html(product_1_detail_image_list)
      
      product_1_image_list = cafe24_upload_images(["/web/application/static/img/thumb/product_1_img.png"])
      product_1_image = "/web/upload/" + product_1_image_list[0].split("/web/upload/")[-1],
      
      product_1_add_image_paths = [
        "/web/application/static/img/thumb/product_1_add_1_img.jpg",
        "/web/application/static/img/thumb/product_1_add_2_img.jpg",
        "/web/application/static/img/thumb/product_1_add_3_img.jpg",
        "/web/application/static/img/thumb/product_1_add_4_img.jpg",
        "/web/application/static/img/thumb/product_1_add_5_img.jpg",
      ]
      product_1_add_image_list = cafe24_upload_images(product_1_add_image_paths)

      req1 = {
        "shop_no": 1,
        "request": {
          ## 표시 설정 ##
          "display": "F",                           # 진열상태
          "selling": "F",                           # 판매상태
          "add_category_no": [                      # 추가 분류 번호
            {"category_no": 113, "recommend": "F", "new": "F"},
            {"category_no": 132, "recommend": "F", "new": "F"},
            {"category_no": 145, "recommend": "F", "new": "F"},
            {"category_no": 340, "recommend": "F", "new": "F"}
          ],
          "main":                                   # 메인진열
            [16],
          "exposure_limit_type": "A",               # 표시제한 범위
          
          ## 기본 정보 ##
          "product_name":                           # 상품명
            "[테스트상품_가전제품] DS 무선 핸디 청소기 휴대식 청소기 차량 가정 겸용 X",
          # "eng_product_name": "",                 # 영문 상품명
          "internal_product_name":                  # 상품명(관리용)
            "테스트상품_가전제품",
          "supply_product_name":                    # 공급사 상품명
            "[테스트상품_가전제품] DS 무선 핸디 청소기 휴대식 청소기 차량 가정 겸용",
          "model_name":                             # 모델명
            "[테스트상품_가전제품] DS 무선 핸디 청소기 휴대식 청소기 차량 가정 겸용",
          # "custom_product_code": "",              # 자체상품 코드
          "product_condition": "N",                 # 상품 상태
          "summary_description":                    # 상품요약설명
            "일반 소비자 가격이 있는 가전제품의 상품 등록 방법입니다.  [가전제품 공급사 확인]",
          # "simple_description": "",               # 상품 간략 설명
          "description":                            # 상품 상세설명
            product_1_description,
          # "mobile_description": "",               # 모바일 상품 상세설명
          # "product_tag": "",                      # 검색어
          "additional_information": [               # 추가항목
            {"key": "custom_option1", "value": "국내배송"},     # 국내·해외배송
            # {"key": "custom_option2", "value": ""},     # 유튜브 영상 ID
            {"key": "custom_option5", "value": "온라인 l 오프라인"},     # 판매가능플랫폼
            # {"key": "custom_option8", "value": ""},     # 유튜브 영상 삽입/링크
            {"key": "custom_option9", "value": "1일"},     # 평균 배송 완료일
            {"key": "custom_option10", "value": "https://1drv.ms/f/c/87241ec44506bab2/EqEh6mN2CZhOg5yABsJ3qeoB_yTfDIvJImj9RZJ6Qyxnmw?e=WUHkqE"},    # (new)상세 이미지 다운로드
            # {"key": "custom_option12", "value": ""},    # (new)유튜브 영상 바로가기
            # {"key": "custom_option13", "value": ""},    # (new)유튜브 영상 다운로드
            # {"key": "custom_option14", "value": ""},    # (new)알집 다운로드
            # {"key": "custom_option15", "value": ""},    # 10개 이상 구매 시
            {"key": "custom_option16", "value": "제주 및 도서산간 배송 불가"},    # 배송비 추가문구
            {"key": "custom_option17", "value": "공급사 배송"},    # 배송형태
            {"key": "custom_option18", "value": "과세"},    # 과세구분
            {"key": "custom_option19", "value": "오후 01시 00분"},    # 발주마감
          ],
          
          ## 판매 정보 ##
          "retail_price": "5000",             # 상품 소비자가
          "supply_price": "500",              # 상품 공급가
          "tax_type": "B",                    # 과세구분
          "margin_rate": "20.00",             # 마진률
          "price": "2000",                    # 상품 판매가
          # "price_content": "",              # 판매가 대체문구
          "buy_limit_by_product": "T",        # 구매제한 개별 설정여부
          "buy_limit_type": "M",              # 구매제한
          "buy_group_list":                   # 구매가능 회원 등급
            [4, 5, 6, 7],
          "single_purchase_restriction": "F", # 단독구매 제한
          "single_purchase": "F",             # 단독구매 설정
          "buy_unit_type": "O",               # 구매단위 타입
          "buy_unit": 1,                      # 구매단위
          "order_quantity_limit_type": "O",   # 주문수량 제한 기준
          "minimum_quantity": 1,              # 최소 주문수량
          "maximum_quantity": 0,              # 최대 주문수량
          "points_by_product": "F",           # 적립금 개별설정 사용여부
          # "points_setting_by_payment": "C",   # 결제방식별 적립금 설정 여부
          # "points_amount": [                  # 적립금 설정 정보
          #   {
          #     "payment_method": "cash",
          #     "points_rate": "100.00",
          #     "points_unit_by_payment": "W"
          #   },
          #   {
          #     "payment_method": "mileage",
          #     "points_rate": "10.00",
          #     "points_unit_by_payment": "P"
          #   }
          # ],
                                              # 개별 결제수단 설정
                                              # 할인혜택 설정
          "except_member_points": "F",        # 회원등급 추가 적립 제외
                                              # 공통이벤트 정보
          "adult_certification": "F",         # 성인인증
                                              # 다음 쇼핑하우 추가 홍보문구
          
          ## 옵션/재고 설정 ##
          "has_option": "F",                  # 옵션 사용여부
          
          ## 이미지정보 ##
          "image_upload_type": "A",           # 이미지 업로드 타입
          "detail_image":                     # 상세이미지
            product_1_image[0],
          # "list_image": "",                 # 목록이미지
          # "tiny_image": "",                 # 작은목록이미지
          # "small_image": "",                # 축소이미지
          "additional_image":                 # 추가이미지
            product_1_add_image_list,
          
          ## 제작 정보 ##
          "manufacturer_code": "M0000000",    # 제조사
          "supplier_code": supplier_code,     # 공급사
          "brand_code": "B0000000",           # 브랜드
          "trend_code": "T0000000",           # 트렌드
          "classification_code": "C000000A",  # 자체분류
          # "made_date": "",                  # 제조일자
          # "release_date": "",               # 출시일자
          # "expiration_date": "",            # 유효기간
          
          "origin_classification": "F",       # 원산지
          # "origin_place_no": "",            # 원산지 번호
          # "origin_place_value": "",         # 원산지기타정보
          "made_in_code": "KR",               # 원산지 국가코드
          
          # "size_guide": {                   # 사이즈 가이드
          #     "use": "T",
          #     "type": "default",
          #     "default": "Male"
          # },
          # "product_volume": {               # 상품 부피 정보
          #   "use_product_volume": "T",
          #   "product_width": 3,
          #   "product_height": 5.5,
          #   "product_length": 7
          # },
          
          "image_upload_type": "A",
          
          ## 상세 이용안내 ##
          # "payment_info": "",               # 상품결제안내
          # "shipping_info": "",              # 상품배송안내
          # "exchange_info": "",              # 교환/반품안내
          # "service_info": "",               # 서비스문의/안내
          
          ## 아이콘 설정 ##
          "icon": [                           # 아이콘
              "custom_16",
              "custom_17",
              "custom_18"
          ],
          
          ## 배송 정보 ##
          "shipping_scope": "A",              # 배송정보
          # "shipping_fee_by_product": "F",     # 개별배송여부
          # "shipping_method": "01",            # 배송방법
          "product_weight": "1.00",           # 상품 전체중량
          # "hscode": "4303101990",           # HS코드
          # "country_hscode": {               # 국가별 HS 코드
          #   "JPN": "430310011",
          #   "CHN": "43031020"
          # },
          "product_shipping_type": "C",       # 상품 배송유형
          
          ## 추가구성상품 ##
          
          ## 관련상품 ##
          
          ## 검색엔진 최적화(SEO) ##
          
          ## 메모 ##
        }
      }
      
      respP1 = requests.post(
        products_url,
        headers=_cafe24_headers(),
        json=_to_jsonable(req1),  # NaN/None/Decimal 안전 변환
        timeout=30
      )
      try:
        bodyP1 = respP1.json()
      except Exception:
        bodyP1 = {"raw": respP1.text}
      
      ## product_2 ##
      product_2_detail_image_list = cafe24_upload_images(["/web/application/static/img/thumb/product_2_detail_img.jpg"])
      product_2_description = build_description_html(product_2_detail_image_list)
      
      product_2_image_list = cafe24_upload_images(["/web/application/static/img/thumb/product_2_img.png"])
      product_2_image = "/web/upload/" + product_2_image_list[0].split("/web/upload/")[-1],
      
      product_2_add_image_paths = [
        "/web/application/static/img/thumb/product_2_add_1_img.jpg",
        "/web/application/static/img/thumb/product_2_add_2_img.jpg",
        "/web/application/static/img/thumb/product_2_add_3_img.jpg",
        "/web/application/static/img/thumb/product_2_add_4_img.jpg",
      ]
      product_2_add_image_list = cafe24_upload_images(product_2_add_image_paths)

      req2 = {
        "shop_no": 1,
        "request": {
          ## 표시 설정 ##
          "display": "F",                           # 진열상태
          "selling": "F",                           # 판매상태
          "add_category_no": [                      # 추가 분류 번호
            {"category_no": 113, "recommend": "F", "new": "F"},
            {"category_no": 132, "recommend": "F", "new": "F"},
            {"category_no": 145, "recommend": "F", "new": "F"},
            {"category_no": 340, "recommend": "F", "new": "F"}
          ],
          "main":                                   # 메인진열
            [16],
          "exposure_limit_type": "A",               # 표시제한 범위
          
          ## 기본 정보 ##
          "product_name":                           # 상품명
            "[테스트상품_여성의류] 페이퍼먼츠 셔츠형 허리 스모크 주름 베이직 롱 원피스 01924",
          # "eng_product_name": "",                 # 영문 상품명
          "internal_product_name":                  # 상품명(관리용)
            "테스트상품_여성의류",
          "supply_product_name":                    # 공급사 상품명
            "[테스트상품_여성의류] 페이퍼먼츠 셔츠형 허리 스모크 주름 베이직 롱 원피스 01924",
          "model_name":                             # 모델명
            "[테스트상품_여성의류] 페이퍼먼츠 셔츠형 허리 스모크 주름 베이직 롱 원피스 01924",
          # "custom_product_code": "",              # 자체상품 코드
          "product_condition": "N",                 # 상품 상태
          "summary_description":                    # 상품요약설명
            "색상 옵션 2가지 있는 경우 상품 등록 방법입니다  [의류 판매 공급사 확인]",
          # "simple_description": "",               # 상품 간략 설명
          "description":                            # 상품 상세설명
            product_2_description,
          # "mobile_description": "",               # 모바일 상품 상세설명
          # "product_tag": "",                      # 검색어
          "additional_information": [               # 추가항목
            {"key": "custom_option1", "value": "국내배송"},     # 국내·해외배송
            # {"key": "custom_option2", "value": ""},     # 유튜브 영상 ID
            {"key": "custom_option5", "value": "온라인 l 오프라인"},     # 판매가능플랫폼
            # {"key": "custom_option8", "value": ""},     # 유튜브 영상 삽입/링크
            {"key": "custom_option9", "value": "1일"},     # 평균 배송 완료일
            {"key": "custom_option10", "value": "https://1drv.ms/f/c/87241ec44506bab2/EqEh6mN2CZhOg5yABsJ3qeoB_yTfDIvJImj9RZJ6Qyxnmw?e=WUHkqE"},    # (new)상세 이미지 다운로드
            # {"key": "custom_option12", "value": ""},    # (new)유튜브 영상 바로가기
            # {"key": "custom_option13", "value": ""},    # (new)유튜브 영상 다운로드
            # {"key": "custom_option14", "value": ""},    # (new)알집 다운로드
            # {"key": "custom_option15", "value": ""},    # 10개 이상 구매 시
            {"key": "custom_option16", "value": "제주 및 도서산간 배송 불가"},    # 배송비 추가문구
            {"key": "custom_option17", "value": "공급사 배송"},    # 배송형태
            {"key": "custom_option18", "value": "과세"},    # 과세구분
            {"key": "custom_option19", "value": "오후 01시 00분"},    # 발주마감
          ],
          
          ## 판매 정보 ##
          "retail_price": "5000",             # 상품 소비자가
          "supply_price": "500",              # 상품 공급가
          "tax_type": "B",                    # 과세구분
          "margin_rate": "20.00",             # 마진률
          "price": "2000",                    # 상품 판매가
          # "price_content": "",              # 판매가 대체문구
          "buy_limit_by_product": "T",        # 구매제한 개별 설정여부
          "buy_limit_type": "M",              # 구매제한
          "buy_group_list":                   # 구매가능 회원 등급
            [4, 5, 6, 7],
          "single_purchase_restriction": "F", # 단독구매 제한
          "single_purchase": "F",             # 단독구매 설정
          "buy_unit_type": "O",               # 구매단위 타입
          "buy_unit": 1,                      # 구매단위
          "order_quantity_limit_type": "O",   # 주문수량 제한 기준
          "minimum_quantity": 1,              # 최소 주문수량
          "maximum_quantity": 0,              # 최대 주문수량
          "points_by_product": "F",           # 적립금 개별설정 사용여부
          # "points_setting_by_payment": "C",   # 결제방식별 적립금 설정 여부
          # "points_amount": [                  # 적립금 설정 정보
          #   {
          #     "payment_method": "cash",
          #     "points_rate": "100.00",
          #     "points_unit_by_payment": "W"
          #   },
          #   {
          #     "payment_method": "mileage",
          #     "points_rate": "10.00",
          #     "points_unit_by_payment": "P"
          #   }
          # ],
                                              # 개별 결제수단 설정
                                              # 할인혜택 설정
          "except_member_points": "F",        # 회원등급 추가 적립 제외
                                              # 공통이벤트 정보
          "adult_certification": "F",         # 성인인증
                                              # 다음 쇼핑하우 추가 홍보문구
          
          ## 옵션/재고 설정 ##
          "has_option": "T",                  # 옵션 사용여부
          "option_type": "S",                 # 옵션 구성방식
          "options": [
              {
                  "name": "Color",
                  "value": [
                      "네이비",
                      "카라멜"
                  ]
              },
              {
                  "name": "Size",
                  "value": [
                      "S",
                      "M",
                      "L",
                      "XL"
                  ]
              }
          ],
          
          ## 이미지정보 ##
          "image_upload_type": "A",           # 이미지 업로드 타입
          "detail_image":                     # 상세이미지
            product_2_image[0],
          # "list_image": "",                 # 목록이미지
          # "tiny_image": "",                 # 작은목록이미지
          # "small_image": "",                # 축소이미지
          "additional_image":                 # 추가이미지
            product_2_add_image_list,
          
          ## 제작 정보 ##
          "manufacturer_code": "M0000000",    # 제조사
          "supplier_code": supplier_code,     # 공급사
          "brand_code": "B0000000",           # 브랜드
          "trend_code": "T0000000",           # 트렌드
          "classification_code": "C000000A",  # 자체분류
          # "made_date": "",                  # 제조일자
          # "release_date": "",               # 출시일자
          # "expiration_date": "",            # 유효기간
          
          "origin_classification": "F",       # 원산지
          # "origin_place_no": "",            # 원산지 번호
          # "origin_place_value": "",         # 원산지기타정보
          "made_in_code": "KR",               # 원산지 국가코드
          
          # "size_guide": {                   # 사이즈 가이드
          #     "use": "T",
          #     "type": "default",
          #     "default": "Male"
          # },
          # "product_volume": {               # 상품 부피 정보
          #   "use_product_volume": "T",
          #   "product_width": 3,
          #   "product_height": 5.5,
          #   "product_length": 7
          # },
          
          "image_upload_type": "A",
          
          ## 상세 이용안내 ##
          # "payment_info": "",               # 상품결제안내
          # "shipping_info": "",              # 상품배송안내
          # "exchange_info": "",              # 교환/반품안내
          # "service_info": "",               # 서비스문의/안내
          
          ## 아이콘 설정 ##
          "icon": [                           # 아이콘
              "custom_16",
              "custom_17",
              "custom_18"
          ],
          
          ## 배송 정보 ##
          "shipping_scope": "A",              # 배송정보
          # "shipping_fee_by_product": "F",     # 개별배송여부
          # "shipping_method": "01",            # 배송방법
          "product_weight": "1.00",           # 상품 전체중량
          # "hscode": "4303101990",           # HS코드
          # "country_hscode": {               # 국가별 HS 코드
          #   "JPN": "430310011",
          #   "CHN": "43031020"
          # },
          "product_shipping_type": "C",       # 상품 배송유형
          
          ## 추가구성상품 ##
          
          ## 관련상품 ##
          
          ## 검색엔진 최적화(SEO) ##
          
          ## 메모 ##
        }
      }
      
      respP2 = requests.post(
        products_url,
        headers=_cafe24_headers(),
        json=_to_jsonable(req2),  # NaN/None/Decimal 안전 변환
        timeout=30
      )
      try:
        bodyP2 = respP2.json()
      except Exception:
        bodyP2 = {"raw": respP2.text}

      # Toss 등록 시도
      try:
        supplierDetail = SupplierDetailRepository.findBySupplierSeq(s.seq)
        seller_body = {
          "refSellerId": s.supplierCode,
          "businessType": supplierDetail.businessType,
          "company": {
            "name": supplierDetail.companyName,
            "representativeName": supplierDetail.representativeName,
            "businessRegistrationNumber": supplierDetail.businessRegistrationNumber,
            "email": supplierDetail.companyEmail,
            "phone": supplierDetail.companyPhone,
          },
          "account": {
            "bankCode": supplierDetail.bankCode,
            "accountNumber": supplierDetail.accountNumber,
            "holderName": supplierDetail.holderName,
          },
        }
        status, body = create_seller_encrypted(seller_body)
        print(status, json.dumps(body, ensure_ascii=False, indent=2))
      except Exception as e:
        print(e)
        # 필요시 seller_body 최소 필드만 로그(개인정보 과다 로그 방지)
        return jsonify({
          "code": 50210,
          "message": "토스 셀러 생성 실패",
          "error": str(e),
        }), 502
    except Exception as e:
      print(e)

    return jsonify({
      "code": 20000,
      "result": {
        "supplier_create": body,
        "user_create": body2,
        "product_create_1": bodyP1,
        "product_create_2": bodyP2
      }
    })

  except requests.RequestException as e:
    print(e)
    return jsonify({"code": 50012, "message": "Cafe24 호출 실패", "detail": str(e)}), 200

def cafe24_upload_images(image_paths: list[str]) -> list[str]:
  """
  여러 이미지를 Cafe24에 업로드하고 업로드된 경로 리스트를 반환
  :param image_paths: 로컬 이미지 파일 경로 리스트
  :return: 업로드된 이미지 상대경로 리스트
  """
  payload = {"request": []}
  for path in image_paths:
    with open(path, "rb") as f:
      b64 = base64.b64encode(f.read()).decode("ascii")
    payload["request"].append({"image": b64})

  url = f"{CAFE24_BASE_URL.rstrip('/')}/api/v2/admin/products/images"
  r = requests.post(url, headers=_cafe24_headers(), json=payload, timeout=30)
  r.raise_for_status()
  data = r.json()
  paths = [img["path"] for img in data.get("images", [])]

  # 업로드 성공 시 각 이미지별 path 배열 반환
  return paths

def build_description_html(img_urls: list[str]) -> str:
  """상품 상세설명 HTML 생성: 이미지 + 문단 텍스트"""
  parts = []
  # 이미지들
  for url in img_urls:
    # 절대/상대 모두 허용되지만, 되도록 절대 URL 사용 권장
    parts.append(
      '<figure style="margin:16px 0;text-align:center;">'
      f'  <img src="{url}" loading="lazy" alt="" style="max-width:100%;height:auto;display:inline-block;">'
      '</figure>'
    )
  return "\n".join(parts)

@supplier.route("/ajax/contract/resend", methods=["POST"])
@jwt_required()
def resend_contract():
  try:
    data = request.get_json(silent=True) or {}
    seq = int((data.get("seq") or 0))
    if not seq:
      return jsonify({"code": 40001, "message": "seq가 필요합니다."}), 400

    s = SupplierListRepository.findBySeq(seq)
    if not s:
      return jsonify({"code": 40400, "message": "존재하지 않는 공급사입니다."}), 404

    # E 상태만 재발송 가능
    if (getattr(s, "contractStatus", "") or "") != "E":
      return jsonify({"code": 40002, "message": "재발송은 에러(E) 상태에서만 가능합니다."}), 400

    # 재시도 큐 상태로 되돌림 (배치가 잡아가도록)
    after_slack_success(s)

    # 필요시 여기서 바로 동기 재발송 호출 로직을 넣을 수도 있음.
    return jsonify({"code": 20000, "seq": s.seq})
  except Exception as e:
    # (참고) Repository에 rollback_if_needed가 없어서 save 실패시의 롤백은 SQLAlchemy 세션 정책에 따름
    return jsonify({"code": 50000, "message": "예외 발생", "detail": str(e)}), 500
