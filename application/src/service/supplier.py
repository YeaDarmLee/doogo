from flask import Blueprint, render_template, request, jsonify
from sqlalchemy.exc import SQLAlchemyError
from application.src.models.SupplierList import SupplierList
from application.src.repositories.SupplierListRepository import SupplierListRepository

supplier = Blueprint("supplier", __name__, url_prefix="/supplier")

# -----------------------------------
# View: 공급사 관리 페이지
# -----------------------------------
@supplier.route("/", methods=["GET"])
def index():
  items = SupplierListRepository.findAll()

  def to_dict(x: SupplierList) -> dict:
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

  return render_template("supplier.html", pageName="supplier", supplierList=[to_dict(s) for s in items])

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
      email=email
    )
    SupplierListRepository.save(s)
    return jsonify({"code": 20000, "seq": s.seq})

  except SQLAlchemyError as e:
    SupplierListRepository.rollback_if_needed()
    return jsonify({"code": 50001, "message": "DB 오류", "detail": str(e.__dict__.get('orig') or e)}), 500
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

  def to_dict(x: SupplierList) -> dict:
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

  return jsonify({"code": 20000, "item": to_dict(s)})

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
    return jsonify({"code": 50001, "message": "DB 오류", "detail": str(e.__dict__.get('orig') or e)}), 500
  except Exception as e:
    SupplierListRepository.rollback_if_needed()
    return jsonify({"code": 50000, "message": "예외 발생", "detail": str(e)}), 500

# -----------------------------------
# Ajax: 리스트(JSON) - 프런트가 POST 호출중
# -----------------------------------
@supplier.route("/ajax/getSupplierList", methods=["POST"])
def listSuppliers():
  items = SupplierListRepository.findAll()

  def to_dict(x: SupplierList) -> dict:
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

  return jsonify({"code": 20000, "supplierList": [to_dict(s) for s in items]})
