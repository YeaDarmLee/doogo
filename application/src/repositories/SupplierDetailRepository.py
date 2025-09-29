# application/src/repositories/SupplierDetailRepository.py
from typing import Optional, Dict, Any
from sqlalchemy import select
from application.src.models import db
from application.src.models.SupplierDetail import SupplierDetail

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
  def find_by_supplier_seq(supplier_seq: int) -> Optional[SupplierDetail]:
    # alias for naming consistency
    return SupplierDetailRepository.findBySupplierSeq(supplier_seq)