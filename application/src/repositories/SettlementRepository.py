# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Optional, Iterable, Tuple, List
from datetime import date, datetime
from sqlalchemy import select, update, and_
from application.src.models import db
from application.src.models.Settlement import Settlement

class SettlementRepository:
  """정산 헤더(SETTLEMENT) 저장소"""

  # 고유키(공급사×주기×기간)로 단건 조회
  @staticmethod
  def get_by_unique(supplier_code: str, period_type: str, period_start: date, period_end: date) -> Optional[Settlement]:
    stmt = (
      select(Settlement)
      .where(
        and_(
          Settlement.supplierCode == supplier_code,
          Settlement.periodType == period_type,
          Settlement.periodStart == period_start,
          Settlement.periodEnd == period_end,
        )
      )
      .limit(1)
    )
    return db.session.execute(stmt).scalar_one_or_none()

  # ID 단건 조회
  @staticmethod
  def get_by_id(header_id: int) -> Optional[Settlement]:
    stmt = select(Settlement).where(Settlement.id == header_id).limit(1)
    return db.session.execute(stmt).scalar_one_or_none()

  # 목록 조회(필터: month/supplier_code)
  @staticmethod
  def list_headers(month: Optional[str] = None, supplier_code: Optional[str] = None, limit: int = 100) -> List[Settlement]:
    conds = []
    if month:
      conds.append(Settlement.month == month)
    if supplier_code:
      conds.append(Settlement.supplierCode == supplier_code)

    stmt = select(Settlement)
    if conds:
      stmt = stmt.where(and_(*conds))
    stmt = stmt.order_by(Settlement.periodStart.desc()).limit(limit)

    return db.session.execute(stmt).scalars().all()

  # upsert: 있으면 업데이트, 없으면 생성
  @staticmethod
  def upsert_header(
    *,
    supplier_seq: int,
    supplier_code: str,
    period_type: str,
    period_start: date,
    period_end: date,
    month: Optional[str],
    gross_amount: int,
    shipping_amount: int,
    commission_rate: float,
    commission_amount: int,
    final_amount: int,
    status: str = "READY",
    deposit_due_dt: Optional[date] = None,
    sent_at: Optional[datetime] = None,
    slack_channel_id: Optional[str] = None,
    slack_file_ts: Optional[str] = None,
    excel_file_path: Optional[str] = None,
  ) -> Settlement:
    row = SettlementRepository.get_by_unique(supplier_code, period_type, period_start, period_end)
    if row:
      row.supplierSeq = supplier_seq
      row.month = month
      row.grossAmount = gross_amount
      row.shippingAmount = shipping_amount
      row.commissionRate = commission_rate
      row.commissionAmount = commission_amount
      row.finalAmount = final_amount
      row.status = status
      row.depositDueDt = deposit_due_dt
      row.sentAt = sent_at
      row.slackChannelId = slack_channel_id
      row.slackFileTs = slack_file_ts
      row.excelFilePath = excel_file_path
      db.session.commit()
      return row

    row = Settlement(
      supplierSeq=supplier_seq,
      supplierCode=supplier_code,
      periodType=period_type,
      periodStart=period_start,
      periodEnd=period_end,
      month=month,
      grossAmount=gross_amount,
      shippingAmount=shipping_amount,
      commissionRate=commission_rate,
      commissionAmount=commission_amount,
      finalAmount=final_amount,
      status=status,
      depositDueDt=deposit_due_dt,
      sentAt=sent_at,
      slackChannelId=slack_channel_id,
      slackFileTs=slack_file_ts,
      excelFilePath=excel_file_path,
    )
    db.session.add(row)
    db.session.commit()
    return row

  # 상태/전송 정보 업데이트 (부분 업데이트 허용)
  @staticmethod
  def update_status(
    header_id: int,
    *,
    status: Optional[str] = None,
    deposit_due_dt: Optional[date] = None,
    sent_at: Optional[datetime] = None,
    slack_channel_id: Optional[str] = None,
    slack_file_ts: Optional[str] = None,
    excel_file_path: Optional[str] = None,
  ) -> None:
    values = {}
    if status is not None:
      values["status"] = status
    if deposit_due_dt is not None:
      values["depositDueDt"] = deposit_due_dt
    if sent_at is not None:
      values["sentAt"] = sent_at
    if slack_channel_id is not None:
      values["slackChannelId"] = slack_channel_id
    if slack_file_ts is not None:
      values["slackFileTs"] = slack_file_ts
    if excel_file_path is not None:
      values["excelFilePath"] = excel_file_path

    if not values:
      return

    db.session.execute(
      update(Settlement)
      .where(Settlement.id == header_id)
      .values(**values)
    )
    db.session.commit()

  # 삭제(자식 상세는 FK ON DELETE CASCADE로 자동 삭제)
  @staticmethod
  def delete(header_id: int) -> None:
    row = SettlementRepository.get_by_id(header_id)
    if not row:
      return
    db.session.delete(row)
    db.session.commit()
