# application/src/repositories/UserListRepository.py
from typing import Optional, List
from sqlalchemy import select, update
from application.src.models import db
from application.src.models.UserList import UserList

# 상태 코드 상수
STATUS_ACTIVE = "A"   # 활성
STATUS_INACTIVE = "I" # 비활성
STATUS_DELETED = "D"  # 삭제

class UserListRepository:
  @staticmethod
  def rollback_if_needed():
    try:
      db.session.rollback()
    except Exception:
      pass

  @staticmethod
  def findAll() -> List[UserList]:
    stmt = select(UserList)
    return db.session.execute(stmt).scalars().all()

  @staticmethod
  def findBySeq(seq: int) -> Optional[UserList]:
    stmt = select(UserList).where(UserList.seq == seq)
    return db.session.execute(stmt).scalar_one_or_none()

  @staticmethod
  def findByUserId(user_id: str) -> Optional[UserList]:
    stmt = select(UserList).where(UserList.userId == user_id)
    return db.session.execute(stmt).scalar_one_or_none()

  @staticmethod
  def save(entity: UserList) -> UserList:
    if not getattr(entity, "seq", None):
      db.session.add(entity)
    db.session.commit()
    return entity

  @staticmethod
  def update_status(seq: int, status_code: str) -> None:
    db.session.execute(
      update(UserList)
      .where(UserList.seq == seq)
      .values(statusCode=status_code)
    )
    db.session.commit()

  @staticmethod
  def findActive(limit: int = 100) -> List[UserList]:
    stmt = select(UserList).where(UserList.statusCode == STATUS_ACTIVE).limit(limit)
    return db.session.execute(stmt).scalars().all()
