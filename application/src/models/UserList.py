from application.src.models import db
from dataclasses import dataclass
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from typing import Optional

@dataclass
class UserList(db.Model):
  """
  사용자 목록 테이블 (USER_LIST) 모델
  """
  __tablename__ = "USER_LIST"

  # 기본 키 (AUTO_INCREMENT)
  seq: Mapped[int] = mapped_column(db.Integer, primary_key=True, autoincrement=True, comment="고유 식별자")

  # 사용자 ID (UNIQUE, NOT NULL)
  userId: Mapped[str] = mapped_column("USER_ID", db.String(100), nullable=False, unique=True, comment="사용자 ID")

  # 사용자 비밀번호
  password: Mapped[str] = mapped_column("PASSWORD", db.String(255), nullable=False, comment="사용자 비밀번호")

  # 사용자 이름
  name: Mapped[str] = mapped_column("NAME", db.String(100), nullable=False, comment="사용자 이름")

  # 사용자 이메일
  email: Mapped[str] = mapped_column("EMAIL", db.String(255), nullable=False, comment="사용자 이메일")

  # 연락처
  phone: Mapped[Optional[str]] = mapped_column("PHONE", db.String(50), nullable=True, comment="사용자 연락처")

  # 타입 코드
  typeCode: Mapped[Optional[str]] = mapped_column("TYPE_CODE", db.String(50), nullable=True, comment="사용자 타입 코드")

  # 상태 코드
  statusCode: Mapped[Optional[str]] = mapped_column("STATUS_CODE", db.String(50), nullable=True, comment="사용자 상태 코드")

  # RefreshToken
  refreshToken: Mapped[Optional[str]] = mapped_column("REFRESH_TOKEN", db.String(255), nullable=True, comment="사용자 RefreshToken")

  # 등록일
  createdAt: Mapped[datetime] = mapped_column(
    "CREATED_AT", db.DateTime, server_default=db.func.current_timestamp(), comment="생성 일시"
  )

  # 수정일
  updatedAt: Mapped[datetime] = mapped_column(
    "UPDATED_AT", db.DateTime, server_default=db.func.current_timestamp(), onupdate=db.func.current_timestamp(), comment="수정 일시"
  )
