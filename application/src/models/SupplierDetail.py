# application/src/models/SupplierDetail.py
from application.src.models import db
from dataclasses import dataclass
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey, UniqueConstraint
from typing import Optional

@dataclass
class SupplierDetail(db.Model):
  """
  공급사 상세 정보 (SUPPLIER_DETAIL)
  SUPPLIER_LIST.SEQ 와 1:1 연결
  """
  __tablename__ = "SUPPLIER_DETAIL"
  __table_args__ = (
    UniqueConstraint("SUPPLIER_SEQ", name="UX_SUPPLIER_DETAIL_SUPPLIER_SEQ"),
  )

  id: Mapped[int] = mapped_column(db.Integer, primary_key=True, autoincrement=True, comment="PK")
  supplierSeq: Mapped[int] = mapped_column(
    "SUPPLIER_SEQ",
    db.Integer,
    ForeignKey("SUPPLIER_LIST.seq", ondelete="CASCADE"),
    nullable=False,
    comment="SUPPLIER_LIST.seq"
  )

  # businessType
  businessType: Mapped[Optional[str]] = mapped_column("BUSINESS_TYPE", db.String(30), nullable=True, comment="INDIVIDUAL_BUSINESS|CORPORATE")

  # company
  companyName: Mapped[Optional[str]] = mapped_column("COMPANY_NAME", db.String(100), nullable=True, comment="상호")
  representativeName: Mapped[Optional[str]] = mapped_column("REPRESENTATIVE_NAME", db.String(100), nullable=True, comment="대표자")
  businessRegistrationNumber: Mapped[Optional[str]] = mapped_column("BIZ_REG_NO", db.String(20), nullable=True, comment="사업자등록번호")
  companyEmail: Mapped[Optional[str]] = mapped_column("COMPANY_EMAIL", db.String(255), nullable=True, comment="회사 이메일")
  companyPhone: Mapped[Optional[str]] = mapped_column("COMPANY_PHONE", db.String(50), nullable=True, comment="회사 연락처")

  # account
  bankCode: Mapped[Optional[str]] = mapped_column("BANK_CODE", db.String(3), nullable=True, comment="금융결제원 3자리 코드")
  accountNumber: Mapped[Optional[str]] = mapped_column("ACCOUNT_NUMBER", db.String(64), nullable=True, comment="계좌번호")
  holderName: Mapped[Optional[str]] = mapped_column("HOLDER_NAME", db.String(100), nullable=True, comment="예금주")

  createdAt: Mapped[datetime] = mapped_column(
    "CREAT_DATE", db.DateTime, server_default=db.func.current_timestamp(), comment="등록일"
  )
  updatedAt: Mapped[datetime] = mapped_column(
    "UPDT_DATE", db.DateTime, server_default=db.func.current_timestamp(), onupdate=db.func.current_timestamp(), comment="수정일"
  )

  bizAddr: Mapped[Optional[str]] = mapped_column("BIZ_ADDR", db.String(200), nullable=True, comment="사업자 주소")
  bizType: Mapped[Optional[str]] = mapped_column("BIZ_TYPE", db.String(40), nullable=True, comment="회사정보 - 업태")
  bizClass: Mapped[Optional[str]] = mapped_column("BIZ_CLASS", db.String(40), nullable=True, comment="회사정보 - 업종")