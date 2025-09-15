# application/src/repositories/SupplierListRepository.py
from typing import Optional, List
from sqlalchemy import select, update
from application.src.models import db
from application.src.models.SupplierList import SupplierList

# ✅ 상태 코드 상수
STATE_PENDING  = "R"  # 승인 대기
STATE_APPROVED = "RA"  # 승인 완료
STATE_REJECTED = "RR"  # 반려

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

  @staticmethod
  def update_state(seq: int, state_code: str) -> None:
    db.session.execute(
      update(SupplierList)
      .where(SupplierList.seq == seq)
      .values(stateCode=state_code)
    )
    db.session.commit()

  # ⬇️ 여기서부터 신규/개선 쿼리

  # ▶ 승인된 공급사만
  @staticmethod
  def find_approved(limit: int = 100):
    stmt = select(SupplierList).where(SupplierList.stateCode == STATE_APPROVED).limit(limit)
    return db.session.execute(stmt).scalars().all()

  # ▶ 비승인(= 대기/반려/미지정) 공급사
  @staticmethod
  def find_unapproved(limit: int = 100):
    stmt = select(SupplierList).where(SupplierList.stateCode == STATE_PENDING).limit(limit)
    return db.session.execute(stmt).scalars().all()

  # ▶ 상태코드 in 조회(페이지네이션용)
  @staticmethod
  def find_by_states(states: List[str], limit: int = 100, offset: int = 0):
    if not states:
      states = [STATE_PENDING]
    stmt = select(SupplierList).where(SupplierList.stateCode.in_(states)).offset(offset).limit(limit)
    return db.session.execute(stmt).scalars().all()

  # ▶ 일괄 상태 변경
  @staticmethod
  def bulk_update_state(seqs: List[int], state_code: str) -> int:
    if not seqs:
      return 0
    res = db.session.execute(
      update(SupplierList)
      .where(SupplierList.seq.in_(seqs))
      .values(stateCode=state_code)
    )
    db.session.commit()
    return res.rowcount

  # ▶ 기존: 대기/미생성 조회(보완) — None도 대기로 간주하려면 사용
  @staticmethod
  def find_pending(limit: int = 100):
    stmt = select(SupplierList).where(SupplierList.stateCode == STATE_PENDING).limit(limit)
    return db.session.execute(stmt).scalars().all()

  @staticmethod
  def findBySupplierCode(supplier_code: str) -> Optional[SupplierList]:
    stmt = select(SupplierList).where(SupplierList.supplierCode == supplier_code)
    return db.session.execute(stmt).scalar_one_or_none()

  @staticmethod
  def find_by_channel_id(channel_id: str) -> Optional[SupplierList]:
    stmt = select(SupplierList).where(SupplierList.channelId == channel_id)
    return db.session.execute(stmt).scalar_one_or_none()

  @staticmethod
  def findApproved(limit: int = 100):
    stmt = select(SupplierList).where((SupplierList.stateCode != STATE_PENDING) & (SupplierList.stateCode != STATE_REJECTED)).limit(limit)
    return db.session.execute(stmt).scalars().all()