# application/src/repositories/SupplierListRepository.py
from typing import Optional
from sqlalchemy import select, update
from application.src.models import db
from application.src.models.SupplierList import SupplierList

class SupplierListRepository:
  @staticmethod
  def findAll():
    stmt = select(SupplierList)
    return db.session.execute(stmt).scalars().all()

  @staticmethod
  def findBySeq(seq: int) -> Optional[SupplierList]:
    stmt = select(SupplierList).where(SupplierList.seq == seq)
    return db.session.execute(stmt).scalar_one_or_none()

  @staticmethod
  def save(entity: SupplierList) -> SupplierList:
    if not entity.seq:
      db.session.add(entity)
    db.session.commit()
    return entity

  # ▶ 채널ID + 상태코드 동시 업데이트
  @staticmethod
  def update_channel_and_state(seq: int, channel_id: str, state_code: Optional[str] = None) -> None:
    values = {"channelId": channel_id}
    if state_code is not None:
      values["stateCode"] = state_code
    db.session.execute(
      update(SupplierList)
      .where(SupplierList.seq == seq)
      .values(**values)
    )
    db.session.commit()

  # ▶ 상태코드만 업데이트 (실패표시 등)
  @staticmethod
  def update_state(seq: int, state_code: str) -> None:
    db.session.execute(
      update(SupplierList)
      .where(SupplierList.seq == seq)
      .values(stateCode=state_code)
    )
    db.session.commit()

  # ▶ 대기/미생성 건 조회(예: NULL 또는 특정 코드)
  @staticmethod
  def find_pending(limit: int = 100):
    stmt = select(SupplierList).where(SupplierList.stateCode.is_(None)).limit(limit)
    return db.session.execute(stmt).scalars().all()
