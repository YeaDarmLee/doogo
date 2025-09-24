from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey
from application.src.models import db

# STATUS_LABEL: 배송완료 / 취소처리

@dataclass
class SettlementDetail(db.Model):
  """
  정산 상세 (SETTLEMENT_DETAIL)
  정산서 1건(SETTLEMENT.ID)에 대한 주문/품목 라인
  """
  __tablename__ = "SETTLEMENT_DETAIL"
  __table_args__ = (
    db.Index("IDX_DETAIL_SETTLEMENT", "SETTLEMENT_ID"),
    db.Index("IDX_DETAIL_SUPPLIER", "SUPPLIER_CODE", "STATUS_LABEL"),
    db.Index("IDX_DETAIL_ORDER", "ORDER_ID"),
  )

  id: Mapped[int] = mapped_column("ID", db.BigInteger, primary_key=True, autoincrement=True, comment="정산 상세 ID")

  settlementId: Mapped[int] = mapped_column(
    "SETTLEMENT_ID",
    db.BigInteger,
    ForeignKey("SETTLEMENT.ID", ondelete="CASCADE"),
    nullable=False,
    comment="FK → SETTLEMENT.ID"
  )

  # 그룹/식별
  supplierCode: Mapped[str] = mapped_column("SUPPLIER_CODE", db.String(50), nullable=False, comment="공급사 코드")
  orderId: Mapped[Optional[str]] = mapped_column("ORDER_ID", db.String(100), nullable=True, comment="주문 ID")
  statusLabel: Mapped[str] = mapped_column(
    "STATUS_LABEL",
    db.Enum("배송완료", "취소처리"),
    nullable=False,
    comment="주문 상태"
  )

  # 엑셀 컬럼(핵심)
  orderDate: Mapped[Optional[datetime]] = mapped_column("ORDER_DATE", db.DateTime, nullable=True, comment="주문일시")
  storeName: Mapped[Optional[str]] = mapped_column("STORE_NAME", db.String(100), nullable=True, comment="쇼핑몰명")
  buyerName: Mapped[Optional[str]] = mapped_column("BUYER_NAME", db.String(100), nullable=True, comment="주문자명")
  receiverName: Mapped[Optional[str]] = mapped_column("RECEIVER_NAME", db.String(100), nullable=True, comment="수령인")
  receiverAddrFull: Mapped[Optional[str]] = mapped_column("RECEIVER_ADDR_FULL", db.String(255), nullable=True, comment="수령인 주소")
  receiverPhone: Mapped[Optional[str]] = mapped_column("RECEIVER_PHONE", db.String(30), nullable=True, comment="수령인 전화")
  productName: Mapped[Optional[str]] = mapped_column("PRODUCT_NAME", db.String(200), nullable=True, comment="상품명")
  qty: Mapped[int] = mapped_column("QTY", db.Integer, nullable=False, default=0, server_default=db.text("0"), comment="수량")
  itemAmount: Mapped[int] = mapped_column("ITEM_AMOUNT", db.BigInteger, nullable=False, default=0, server_default=db.text("0"), comment="상품 금액")
  carrierName: Mapped[Optional[str]] = mapped_column("CARRIER_NAME", db.String(80), nullable=True, comment="배송업체")
  trackingNo: Mapped[Optional[str]] = mapped_column("TRACKING_NO", db.String(80), nullable=True, comment="운송장번호")
  orderShippingFee: Mapped[int] = mapped_column("ORDER_SHIPPING_FEE", db.BigInteger, nullable=False, default=0, server_default=db.text("0"), comment="배송비")

  createdAt: Mapped[datetime] = mapped_column(
    "CREATED_AT", db.DateTime, server_default=db.func.current_timestamp(), comment="생성일시"
  )
