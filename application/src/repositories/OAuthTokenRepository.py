from typing import Optional
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from application.src.models import db
from application.src.models.OAuthToken import OAuthToken

class OAuthTokenRepository:
  @staticmethod
  def get(provider: str) -> Optional[OAuthToken]:
    stmt = select(OAuthToken).where(OAuthToken.provider == provider)
    return db.session.execute(stmt).scalar_one_or_none()

  @staticmethod
  def upsert_refresh(provider: str, refresh_token: str, mall_id: Optional[str] = None, scope: Optional[str] = None) -> OAuthToken:
    tok = OAuthTokenRepository.get(provider)
    if tok is None:
      tok = OAuthToken(
        provider=provider,
        mallId=mall_id,
        refreshToken=refresh_token,
        scope=scope
      )
      db.session.add(tok)
    else:
      tok.refreshToken = refresh_token
      if mall_id is not None:
        tok.mallId = mall_id
      if scope is not None:
        tok.scope = scope
    db.session.commit()
    return tok

  @staticmethod
  def update_access(provider: str, access_token: str, expires_at: Optional[datetime] = None, scope: Optional[str] = None) -> OAuthToken:
    tok = OAuthTokenRepository.get(provider)
    if tok is None:
      # access만 먼저 들어오는 예외 케이스 대비 (refresh 없이 생성)
      tok = OAuthToken(
        provider=provider,
        accessToken=access_token,
        expiresAt=expires_at,
        scope=scope
      )
      db.session.add(tok)
    else:
      tok.accessToken = access_token
      tok.expiresAt = expires_at
      if scope is not None:
        tok.scope = scope
    db.session.commit()
    return tok

  @staticmethod
  def load_refresh(provider: str) -> Optional[str]:
    tok = OAuthTokenRepository.get(provider)
    return tok.refreshToken if tok else None

  @staticmethod
  def rollback_if_needed():
    try:
      db.session.rollback()
    except Exception:
      pass
