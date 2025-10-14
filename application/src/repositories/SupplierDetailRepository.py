# application/src/repositories/SupplierDetailRepository.py
from typing import Optional, Dict, Any
from sqlalchemy import select
from application.src.models import db
from application.src.models.SupplierDetail import SupplierDetail
import re

class SupplierDetailRepository:
  @staticmethod
  def save(entity: SupplierDetail) -> SupplierDetail:
    """
    SupplierDetail 저장 (seq 없으면 insert, 있으면 update)
    """
    if not getattr(entity, "id", None):  # PK 없으면 신규
      db.session.add(entity)
    db.session.commit()
    return entity
  
  @staticmethod
  def findBySupplierSeq(supplier_seq: int) -> Optional[SupplierDetail]:
    """
    SupplierDetail 조회 (공급사 seq 기준)
    """
    stmt = select(SupplierDetail).where(SupplierDetail.supplierSeq == supplier_seq)
    return db.session.execute(stmt).scalar_one_or_none()

  @staticmethod
  def upsert_from_seller_body(supplier_seq: int, seller_body: Dict[str, Any]) -> SupplierDetail:
    """
    seller_body의 필요한 필드만 SUPPLIER_DETAIL에 upsert
    """
    d = SupplierDetailRepository.find_by_supplier_seq(supplier_seq)
    if not d:
      d = SupplierDetail(supplierSeq=supplier_seq)
      db.session.add(d)

    d.businessType = seller_body.get("businessType")

    company = seller_body.get("company", {}) or {}
    d.companyName = company.get("name")
    d.representativeName = company.get("representativeName")
    d.businessRegistrationNumber = company.get("businessRegistrationNumber")
    d.companyEmail = company.get("email")
    d.companyPhone = company.get("phone")

    account = seller_body.get("account", {}) or {}
    d.bankCode = account.get("bankCode")
    d.accountNumber = account.get("accountNumber")
    d.holderName = account.get("holderName")

    db.session.commit()
    return d

  @staticmethod
  def upsert_manual(
    supplier_seq: int,
    company_name: Optional[str] = None,
    business_registration_number: Optional[str] = None,
    bank_code: Optional[str] = None,
    account_number: Optional[str] = None,
    holder_name: Optional[str] = None,
    company_email: Optional[str] = None,
    company_phone: Optional[str] = None,
    business_type: Optional[str] = None,
  ) -> SupplierDetail:
    """
    관리자/등록 폼 등에서 직접 입력받은 상세 정보를 upsert.
    - 값이 None 이면 해당 필드는 변경하지 않음 (기존값 유지)
    - 사업자번호는 숫자만 남겨 저장(10자리 기대)
    """
    d = SupplierDetailRepository.findBySupplierSeq(supplier_seq)
    is_new = False
    if not d:
      d = SupplierDetail(supplierSeq=supplier_seq)
      db.session.add(d)
      is_new = True

    # 정규화/보정
    def _nz(v: Optional[str]) -> Optional[str]:
      if v is None:
        return None
      v = str(v).strip()
      return v if v != "" else None

    biz_digits = None
    if business_registration_number is not None:
      digits = re.sub(r"\D", "", str(business_registration_number))
      biz_digits = digits or None  # 빈문자열이면 None

    # 필드 반영 (None이 아닌 값만)
    if company_name is not None:
      d.companyName = _nz(company_name)
    if biz_digits is not None:
      d.businessRegistrationNumber = biz_digits
    if bank_code is not None:
      d.bankCode = _nz(bank_code)
    if account_number is not None:
      d.accountNumber = _nz(account_number)
    if holder_name is not None:
      d.holderName = _nz(holder_name)
    if company_email is not None:
      d.companyEmail = _nz(company_email)
    if company_phone is not None:
      d.companyPhone = _nz(company_phone)
    if business_type is not None:
      d.businessType = _nz(business_type)

    db.session.commit()
    return d
  
  @staticmethod
  def find_by_supplier_seq(supplier_seq: int) -> Optional[SupplierDetail]:
    # alias for naming consistency
    return SupplierDetailRepository.findBySupplierSeq(supplier_seq)