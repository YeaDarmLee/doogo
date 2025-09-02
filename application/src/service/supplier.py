from flask import Blueprint, render_template, request, jsonify
from sqlalchemy.exc import SQLAlchemyError

from application.src.models.SupplierList import SupplierList
from application.src.repositories.SupplierListRepository import SupplierListRepository

supplier = Blueprint("supplier", __name__, url_prefix="/supplier")


# ------------------------------------------------------------------------------
# View: 공급사 관리 페이지
#  - 서버 렌더링으로 최초 진입 시 목록을 내려준다.
#  - 테이블 재조회는 /ajax/list 를 통해 비동기로 가져오는 흐름 권장.
# ------------------------------------------------------------------------------
@supplier.route("/", methods=["GET"])
def index():
  supplier_list = SupplierListRepository.findAll()
  return render_template(
    "supplier.html",
    pageName="supplier",
    supplierList=supplier_list
  )


# ------------------------------------------------------------------------------
# Ajax: 공급사 신규 등록
#  - 수신: FormData 또는 JSON 모두 허용
#  - 검증: 회사명 필수, supplierID 최소 6자(자유형식)
#  - 저장: 정상 저장 시 20000 + 생성된 seq 반환
# ------------------------------------------------------------------------------
@supplier.route("/ajax/addSupplier", methods=["POST"])
def addSupplier():
  try:
    # 1) 입력 수신 (JSON 우선, 없으면 form)
    data = request.get_json(silent=True) or request.form

    # 2) 필드 파싱 + 공백 정리
    def g(key):  # 작은 헬퍼
      v = data.get(key)
      return v.strip() if isinstance(v, str) else v

    company_name = g("supplierCompanyName") or ""
    supplier_id = g("supplierID") or ""         # 자유형식 (최소 6자)
    supplier_pw = g("supplierPW") or None
    supplier_url = g("supplierURL") or None
    manager = g("supplierManager") or None
    manager_rank = g("supplierManagerRank") or None
    number = g("supplierNumber") or None
    email = g("supplierEmail") or None

    # 3) 간단 검증 (프런트 규칙과 일치)
    errors = {}
    if not company_name:
      errors["supplierCompanyName"] = "회사명은 필수입니다."
    if not supplier_id or len(supplier_id) < 6:
      errors["supplierID"] = "ID는 6자 이상 입력해 주세요."

    if errors:
      return jsonify({"code": 40001, "errors": errors}), 400

    # 4) 엔티티 구성
    supplier_obj = SupplierList(
      companyName=company_name,
      supplierID=supplier_id,      # 자유형식 가정
      supplierPW=supplier_pw,
      supplierURL=supplier_url,
      manager=manager,
      managerRank=manager_rank,
      number=number,
      email=email
    )

    # 5) 저장
    SupplierListRepository.save(supplier_obj)

    return jsonify({"code": 20000, "seq": supplier_obj.seq})

  except SQLAlchemyError as e:
    # DB 제약/세션 오류 처리
    SupplierListRepository.rollback_if_needed()
    return jsonify({"code": 50001, "message": "DB 처리 중 오류", "detail": str(e.__dict__.get('orig') or e)}), 500
  except Exception as e:
    SupplierListRepository.rollback_if_needed()
    return jsonify({"code": 50000, "message": "처리 중 예외", "detail": str(e)}), 500


# ------------------------------------------------------------------------------
# Ajax: 공급사 리스트 조회
#  - 응답을 프런트에서 바로 렌더링할 수 있도록 dict 배열로 직렬화
#  - GET 사용을 권장 (캐싱/디버깅 용이)
# ------------------------------------------------------------------------------
@supplier.route("/ajax/list", methods=["GET"])
def listSuppliers():
  items = SupplierListRepository.findAll()

  # SQLAlchemy 모델 → dict 로 변환
  def to_dict(s: SupplierList) -> dict:
    return {
      "seq": s.seq,
      "companyName": s.companyName or "",
      "stateCode": getattr(s, "stateCode", None) or "",
      "supplierID": s.supplierID or "",
      "supplierPW": s.supplierPW or "",
      "supplierURL": s.supplierURL or "",
      "manager": s.manager or "",
      "managerRank": s.managerRank or "",
      "number": s.number or "",
      "email": s.email or ""
    }

  rows = [to_dict(x) for x in items]
  return jsonify({"code": 20000, "list": rows})
