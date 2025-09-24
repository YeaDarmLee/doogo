from dataclasses import dataclass
from datetime import datetime, date
from typing import Optional
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey
from application.src.models import db

# STATUS 고정값: READY, SENT, CONFIRMED, PAID, RECONCILED, CANCELED

@dataclass
class Settlement(db.Model):
  """
  정산 헤더 (SETTLEMENT)
  공급사 × 기간(시작~종료) × 주기(M/W/D) 단위 1건
  """
  __tablename__ = "SETTLEMENT"
  __table_args__ = (
    db.UniqueConstraint("SUPPLIER_CODE", "PERIOD_TYPE", "PERIOD_START", "PERIOD_END", name="uq_supplier_period"),
    db.Index("IDX_SETTLEMENT_SUPPLIER", "SUPPLIER_CODE", "PERIOD_TYPE", "PERIOD_START", "PERIOD_END"),
    db.Index("IDX_SETTLEMENT_MONTH", "MONTH"),
  )

  id: Mapped[int] = mapped_column("ID", db.BigInteger, primary_key=True, autoincrement=True, comment="정산 헤더 ID")

  # 식별/연결
  supplierSeq: Mapped[int] = mapped_column("SUPPLIER_SEQ", db.Integer, nullable=False, comment="FK → SUPPLIER_LIST.SEQ")
  supplierCode: Mapped[str] = mapped_column("SUPPLIER_CODE", db.String(50), nullable=False, comment="공급사 코드")

  # 기간/주기
  periodType: Mapped[str] = mapped_column("PERIOD_TYPE", db.Enum("M", "W", "D"), nullable=False, comment="월/주/일")
  periodStart: Mapped[date] = mapped_column("PERIOD_START", db.Date, nullable=False, comment="정산 시작일")
  periodEnd: Mapped[date] = mapped_column("PERIOD_END", db.Date, nullable=False, comment="정산 종료일")
  month: Mapped[Optional[str]] = mapped_column("MONTH", db.String(7), nullable=True, comment="YYYY-MM")

  # 금액 지표
  grossAmount: Mapped[int] = mapped_column("GROSS_AMOUNT", db.BigInteger, nullable=False, default=0, server_default=db.text("0"), comment="총 결제 금액")
  shippingAmount: Mapped[int] = mapped_column("SHIPPING_AMOUNT", db.BigInteger, nullable=False, default=0, server_default=db.text("0"), comment="배송비 합계")
  commissionRate: Mapped[float] = mapped_column("COMMISSION_RATE", db.Numeric(5, 2), nullable=False, default=0.00, server_default=db.text("0.00"), comment="수수료율 %")
  commissionAmount: Mapped[int] = mapped_column("COMMISSION_AMOUNT", db.BigInteger, nullable=False, default=0, server_default=db.text("0"), comment="수수료")
  finalAmount: Mapped[int] = mapped_column("FINAL_AMOUNT", db.BigInteger, nullable=False, default=0, server_default=db.text("0"), comment="최종 정산금")

  # 상태/전송/정산 흐름
  status: Mapped[str] = mapped_column(
    "STATUS",
    db.Enum("READY", "SENT", "CONFIRMED", "PAID", "RECONCILED", "CANCELED"),
    nullable=False,
    default="READY",
    server_default="READY",
    comment="정산 상태"
  )
  depositDueDt: Mapped[Optional[date]] = mapped_column("DEPOSIT_DUE_DT", db.Date, nullable=True, comment="입금 예정일")
  sentAt: Mapped[Optional[datetime]] = mapped_column("SENT_AT", db.DateTime, nullable=True, comment="슬랙 전송 시각")
  slackChannelId: Mapped[Optional[str]] = mapped_column("SLACK_CHANNEL_ID", db.String(30), nullable=True, comment="슬랙 채널 ID")
  slackFileTs: Mapped[Optional[str]] = mapped_column("SLACK_FILE_TS", db.String(50), nullable=True, comment="슬랙 파일 ts")
  excelFilePath: Mapped[Optional[str]] = mapped_column("EXCEL_FILE_PATH", db.String(255), nullable=True, comment="엑셀 파일 경로")

  createdAt: Mapped[datetime] = mapped_column(
    "CREATED_AT", db.DateTime, server_default=db.func.current_timestamp(), comment="생성일시"
  )
  updatedAt: Mapped[datetime] = mapped_column(
    "UPDATED_AT", db.DateTime, server_default=db.func.current_timestamp(), onupdate=db.func.current_timestamp(), comment="수정일시"
  )
