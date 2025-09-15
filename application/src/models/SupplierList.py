from application.src.models import db
from dataclasses import dataclass
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional

@dataclass
class SupplierList(db.Model):
  """
  공급사 정보 테이블 (SUPPLIER_LIST) 모델
  """
  __tablename__ = "SUPPLIER_LIST"

  # 기본 키 (AUTO_INCREMENT)
  seq: Mapped[int] = mapped_column(db.Integer, primary_key=True, autoincrement=True, comment="SEQ")

  # 공급사 이름 (NULL 불가능)
  companyName: Mapped[Optional[str]] = mapped_column("COMPANY_NAME", db.String(100), nullable=False, comment="공급사 이름")
  
  # CAFE24 공급사 코드
  supplierCode: Mapped[Optional[str]] = mapped_column("SUPPLIER_CODE", db.String(50), nullable=True, unique=True, comment="CAFE24 공급사 코드")

  # 공급사 ID/PW/URL
  supplierID: Mapped[Optional[str]]  = mapped_column("SUPPLIER_ID", db.String(100), nullable=True, comment="공급사 ID")
  supplierPW: Mapped[Optional[str]]  = mapped_column("SUPPLIER_PW", db.String(100), nullable=True, comment="공급사 PW")
  supplierURL: Mapped[Optional[str]] = mapped_column("SUPPLIER_URL", db.String(255), nullable=True, comment="공급사 URL")

  # 담당자
  manager: Mapped[Optional[str]]      = mapped_column("MANAGER", db.String(100), nullable=True, comment="담당자 이름")
  managerRank: Mapped[Optional[str]]  = mapped_column("MANAGER_RANK", db.String(50), nullable=True, comment="담당자 직책")
  number: Mapped[Optional[str]]       = mapped_column("NUMBER", db.String(50), nullable=True, comment="담당자 연락처")
  email: Mapped[Optional[str]]        = mapped_column("EMAIL", db.String(255), nullable=True, comment="이메일")
  
  # 상태/슬랙
  stateCode: Mapped[Optional[str]] = mapped_column("STATE_CODE", db.String(4), nullable=True, comment="상태 코드")
  channelId: Mapped[Optional[str]] = mapped_column("CHANNEL_ID", db.String(30), nullable=True, comment="채널 ID")

  # 계약서(상태/식별자)
  contractStatus: Mapped[Optional[str]] = mapped_column("CONTRACT_STATUS", db.String(20), nullable=True, comment="계약서 상태")
  contractId: Mapped[Optional[str]]     = mapped_column("CONTRACT_ID", db.String(100), nullable=True, comment="계약서 ID")

  # ✅ 신규: 계약서 템플릿 & 파라미터
  contractTemplate: Mapped[Optional[str]]      = mapped_column("CONTRACT_TEMPLATE", db.String(20), nullable=True, comment="A(단일%)|B(구간%)")
  contractPercent: Mapped[Optional[float]]     = mapped_column("CONTRACT_PERCENT", db.Numeric(5, 2), nullable=True, comment="A용: 단일 수수료(%)")
  contractThreshold: Mapped[Optional[int]]     = mapped_column("CONTRACT_THRESHOLD", db.BigInteger, nullable=True, comment="B용: 특정 금액(원)")
  contractPercentUnder: Mapped[Optional[float]] = mapped_column("CONTRACT_PERCENT_UNDER", db.Numeric(5, 2), nullable=True, comment="B용: 이하 시 %")
  contractPercentOver: Mapped[Optional[float]]  = mapped_column("CONTRACT_PERCENT_OVER", db.Numeric(5, 2), nullable=True, comment="B용: 초과 시 %")
  contractSkip: Mapped[bool]                    = mapped_column("CONTRACT_SKIP", db.Boolean, nullable=False, server_default=db.text("0"), comment="이미 체결되어 발송 스킵")

  # 등록/수정일
  createdAt: Mapped[datetime] = mapped_column(
    "CREAT_DATE", db.DateTime, server_default=db.func.current_timestamp(), comment="등록일"
  )
  updatedAt: Mapped[datetime] = mapped_column(
    "UPDT_DATE", db.DateTime, server_default=db.func.current_timestamp(), onupdate=db.func.current_timestamp(), comment="수정일"
  )
