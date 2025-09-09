from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Mapped, mapped_column
from application.src.models import db

@dataclass
class OAuthToken(db.Model):
  __tablename__ = "OAUTH_TOKEN"

  provider: Mapped[str] = mapped_column("PROVIDER", db.String(20), primary_key=True)
  mallId: Mapped[Optional[str]] = mapped_column("MALL_ID", db.String(50), nullable=True)
  refreshToken: Mapped[str] = mapped_column("REFRESH_TOKEN", db.Text, nullable=False)
  accessToken: Mapped[Optional[str]] = mapped_column("ACCESS_TOKEN", db.Text, nullable=True)
  expiresAt: Mapped[Optional[datetime]] = mapped_column("EXPIRES_AT", db.DateTime, nullable=True)
  scope: Mapped[Optional[str]] = mapped_column("SCOPE", db.String(255), nullable=True)
  updatedAt: Mapped[datetime] = mapped_column("UPDATED_AT", db.DateTime, server_default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())
  createdAt: Mapped[datetime] = mapped_column("CREATED_AT", db.DateTime, server_default=db.func.current_timestamp())
