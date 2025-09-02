from application.src.models import db
from application.src.models.SupplierList import SupplierList
from sqlalchemy import select

class SupplierListRepository:
  """
  공급사 정보 테이블 (SUPPLIER_LIST)의 CRUD 처리를 위한 Repository 클래스
  """

  @staticmethod
  def findAll():
    stmt = select(SupplierList)
    result = db.session.execute(stmt).scalars().all()
    return result

  @staticmethod
  def findBySeq(seq: int):
    stmt = select(SupplierList).where(SupplierList.seq == seq)
    result = db.session.execute(stmt).scalar_one_or_none()
    return result

  @staticmethod
  def findByCompanyName(company_name: str):
    stmt = select(SupplierList).where(SupplierList.companyName == company_name)
    result = db.session.execute(stmt).scalar_one_or_none()
    return result

  @staticmethod
  def save(supplier: SupplierList):
    if not supplier.seq:
      db.session.add(supplier)
    db.session.commit()
    return supplier

  @staticmethod
  def delete(supplier: SupplierList):
    db.session.delete(supplier)
    db.session.commit()
