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
  companyName: Mapped[Optional[str]] = mapped_column("COMPANY_NAME", db.String(20), nullable=False, comment="공급사 이름")
  
  # CAFE24 공급사 코드
  supplierCode: Mapped[Optional[str]] = mapped_column("SUPPLIER_CODE", db.String(50), nullable=True, unique=True, comment="CAFE24 공급사 코드")

  # 공급사 ID (NULL 불가능)
  supplierID: Mapped[Optional[str]] = mapped_column("SUPPLIER_ID", db.Integer, nullable=False, comment="공급사 ID")

  # 공급사 PW (NULL 불가능)
  supplierPW: Mapped[Optional[str]] = mapped_column("SUPPLIER_PW", db.String(100), nullable=False, comment="공급사 PW")

  # 공급사 URL (NULL 가능)
  supplierURL: Mapped[Optional[str]] = mapped_column("SUPPLIER_URL", db.String(100), nullable=True, comment="공급사 URL")

  # 담당자 이름 (NULL 가능)
  manager: Mapped[Optional[str]] = mapped_column("MANAGER", db.String(10), nullable=True, comment="담당자 이름")

  # 담당자 직책 (NULL 가능)
  managerRank: Mapped[Optional[str]] = mapped_column("MANAGER_RANK", db.String(500), nullable=True, comment="담당자 직책")

  # 담당자 연락처 (NULL 가능)
  number: Mapped[Optional[str]] = mapped_column("NUMBER", db.Text, nullable=True, comment="담당자 연락처")

  # 이메일 (NULL 가능)
  email: Mapped[Optional[str]] = mapped_column("EMAIL", db.String(10), nullable=True, comment="이메일")
  
  # 상태 코드 (NULL 가능)
  stateCode: Mapped[Optional[str]] = mapped_column("STATE_CODE", db.String(4), nullable=True, comment="상태 코드")
  
  # 채널 ID (NULL 가능)
  channelId: Mapped[Optional[str]] = mapped_column("CHANNEL_ID", db.String(30), nullable=True, comment="채널 ID")
  
  # 계약서 상태 (NULL 가능)
  contractStatus: Mapped[Optional[str]] = mapped_column("CONTRACT_STATUS", db.String(4), nullable=True, comment="계약서 상태")
  
  # 계약서 ID (NULL 가능)
  contractId: Mapped[Optional[str]] = mapped_column("CONTRACT_ID", db.String(100), nullable=True, comment="계약서 ID")

  # 등록일 (기본값: 현재 시간)
  createdAt: Mapped[datetime] = mapped_column(
    "CREAT_DATE", db.DateTime, server_default=db.func.current_timestamp(), comment="등록일"
  )

  # 수정일 (기본값: 현재 시간, 업데이트 시 자동 변경)
  updatedAt: Mapped[datetime] = mapped_column(
    "UPDT_DATE", db.DateTime, server_default=db.func.current_timestamp(), onupdate=db.func.current_timestamp(), comment="수정일"
  )
