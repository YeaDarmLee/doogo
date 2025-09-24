# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Iterable, List, Dict, Optional
from sqlalchemy import select, delete
from application.src.models import db
from application.src.models.SettlementDetail import SettlementDetail

class SettlementDetailRepository:
  """정산 상세(SETTLEMENT_DETAIL) 저장소"""

  # 헤더 기준 삭제
  @staticmethod
  def delete_by_header(header_id: int) -> None:
    db.session.execute(
      delete(SettlementDetail).where(SettlementDetail.settlementId == header_id)
    )
    db.session.commit()

  # 배치 insert (dict 목록을 그대로 컬럼 맵핑)
  @staticmethod
  def insert_many(header_id: int, supplier_code: str, rows: Iterable[Dict]) -> None:
    objs: List[SettlementDetail] = []
    for r in rows:
      obj = SettlementDetail(
        settlementId=header_id,
        supplierCode=supplier_code,
        orderId=r.get("orderId"),
        statusLabel=r.get("statusLabel"),           # '배송완료' | '취소처리'
        orderDate=r.get("orderDate"),               # datetime or None
        storeName=r.get("storeName"),
        buyerName=r.get("buyerName"),
        receiverName=r.get("receiverName"),
        receiverAddrFull=r.get("receiverAddrFull"),
        receiverPhone=r.get("receiverPhone"),
        productName=r.get("productName"),
        qty=int(r.get("qty") or 0),
        itemAmount=int(r.get("itemAmount") or 0),
        carrierName=r.get("carrierName"),
        trackingNo=r.get("trackingNo"),
        orderShippingFee=int(r.get("orderShippingFee") or 0),
      )
      objs.append(obj)

    if objs:
      db.session.add_all(objs)
      db.session.commit()

  # 헤더 기준 상세 목록
  @staticmethod
  def list_by_header(header_id: int) -> List[SettlementDetail]:
    stmt = select(SettlementDetail).where(SettlementDetail.settlementId == header_id).order_by(SettlementDetail.id.asc())
    return db.session.execute(stmt).scalars().all()
