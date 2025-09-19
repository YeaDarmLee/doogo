# application/src/repositories/SupplierListRepository.py
from typing import Optional, List, Dict, Any
from sqlalchemy import select, update, or_, and_
from application.src.models import db
from application.src.models.SupplierList import SupplierList

# ✅ 상태 코드 상수
STATE_PENDING  = "R"   # 승인 대기
STATE_APPROVED = "RA"  # 승인 완료
STATE_REJECTED = "RR"  # 반려
STATE_DELETED = "D"    # 삭제

class SupplierListRepository:
  @staticmethod
  def rollback_if_needed():
    try:
      db.session.rollback()
    except Exception:
      pass

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
    if not getattr(entity, "seq", None):
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

  # ▶ 승인된 공급사만
  @staticmethod
  def find_approved(limit: int = 100):
    stmt = select(SupplierList).where(SupplierList.stateCode == STATE_APPROVED).limit(limit)
    return db.session.execute(stmt).scalars().all()

  # ▶ 비승인(= 대기) 공급사
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

  # ▶ 기존: 대기 조회
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
    stmt = select(SupplierList).where(
      or_(
        SupplierList.stateCode.is_(None),
        (SupplierList.stateCode != STATE_PENDING) & (SupplierList.stateCode != STATE_REJECTED)
      )
    ).limit(limit)
    return db.session.execute(stmt).scalars().all()

  # ✅ 신규: 계약 필드만 부분 업데이트(발송 큐/전자서명 훅에서 사용)
  @staticmethod
  def update_contract_fields(seq: int, fields: Dict[str, Any]) -> None:
    """
    fields 예시:
      {
        "contractTemplate": "A"|"B"|None,
        "contractPercent": 10.0,
        "contractThreshold": 500000,
        "contractPercentUnder": 8.0,
        "contractPercentOver": 12.0,
        "contractSkip": True|False,
        "contractStatus": "발송대기"|"완료"|"외부제출"|...
        "contractId": "eformsign-doc-id"
      }
    """
    allowed = {
      "contractTemplate", "contractPercent", "contractThreshold",
      "contractPercentUnder", "contractPercentOver", "contractSkip",
      "contractStatus", "contractId"
    }
    payload = {k: v for k, v in (fields or {}).items() if k in allowed}
    if not payload:
      return
    db.session.execute(
      update(SupplierList)
      .where(SupplierList.seq == seq)
      .values(**payload)
    )
    db.session.commit()
    
  @staticmethod
  def find_by_email(editor_email: str) -> Optional[SupplierList]:
    """
    이메일 기준으로 최신(seq DESC) 공급사 조회
    :param editor_email: 공급사 담당자 이메일
    :return: SupplierList 또는 None
    """
    stmt = select(SupplierList).where(SupplierList.email == editor_email)
    return db.session.execute(stmt).scalar_one_or_none()
  
  @staticmethod  
  def find_by_settlement_period(period_code: str, limit: int = 100):
    """
    정산주기(D/W/M) 기준 정산 대상 조회
    - channelId / supplierCode 가 존재하는 공급사만
    - settlementPeriod == period_code 인 행만
    :param period_code: 'D' | 'W' | 'M'
    :param limit: 최대 조회 건수
    """
    code = (period_code or "").strip().upper()
    if code not in ("D", "W", "M"):
      return []

    stmt = (
      select(SupplierList)
      .where(and_(
        SupplierList.channelId.isnot(None),
        SupplierList.supplierCode.isnot(None),
        SupplierList.settlementPeriod == code
      ))
      .order_by(SupplierList.seq.asc())
      .limit(limit)
    )
    return db.session.execute(stmt).scalars().all()