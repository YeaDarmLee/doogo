# application/src/service/cafe24_oauth_service.py
# -*- coding: utf-8 -*-

import os, time, requests, base64
from datetime import datetime, timedelta
from typing import Optional
from application.src.repositories.OAuthTokenRepository import OAuthTokenRepository

PROVIDER = "cafe24"

CAFE24_BASE_URL = os.getenv("CAFE24_BASE_URL")
CLIENT_ID       = os.getenv("CAFE24_CLIENT_ID")
CLIENT_SECRET   = os.getenv("CAFE24_CLIENT_SECRET")

# 메모리 캐시 (access_token)
_token_cache = {"access_token": None, "expires_at": 0.0}

def save_refresh_token(refresh_token: str, mall_id: Optional[str] = None, scope: Optional[str] = None):
  OAuthTokenRepository.upsert_refresh(PROVIDER, refresh_token, mall_id=mall_id, scope=scope)

def load_refresh_token() -> Optional[str]:
  return OAuthTokenRepository.load_refresh(PROVIDER)

def save_access_token(access_token: str, expires_in: Optional[int] = 7200, scope: Optional[str] = None):
  expires_at = None
  if expires_in:
    expires_at = datetime.utcnow() + timedelta(seconds=int(expires_in))
  OAuthTokenRepository.update_access(PROVIDER, access_token, expires_at=expires_at, scope=scope)

def _refresh_access_token_with(rt: str) -> str:
  """
  refresh_token으로 access_token 재발급 (HTTP Basic Authorization 사용)
  """
  url = f"{CAFE24_BASE_URL}/api/v2/oauth/token"
  basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode("utf-8")).decode("utf-8")

  resp = requests.post(
    url,
    headers={
      "Authorization": f"Basic {basic}",
      "Content-Type": "application/x-www-form-urlencoded",
    },
    data={
      "grant_type": "refresh_token",
      "refresh_token": rt,
    },
    timeout=10
  )
  resp.raise_for_status()
  data = resp.json()

  access      = data["access_token"]
  new_refresh = data.get("refresh_token")  # 로테이션 가능
  raw_scope = data.get("scope") or data.get("scopes")
  if isinstance(raw_scope, list):
    scope = ",".join(raw_scope)
  else:
    scope = str(raw_scope) if raw_scope is not None else None
  expires_in  = int(data.get("expires_in", 7200))

  # DB 저장/갱신
  save_access_token(access, expires_in=expires_in, scope=scope)
  if new_refresh:
    save_refresh_token(new_refresh)

  # 캐시 (60초 여유)
  _token_cache["access_token"] = access
  _token_cache["expires_at"]   = time.time() + expires_in - 60
  return access

def get_access_token() -> str:
  """
  호출 시점에 유효한 access_token 반환.
  - 캐시에 유효한 토큰이 있으면 그대로 사용
  - 없으면 DB의 refresh_token으로 갱신
  """
  now = time.time()
  if _token_cache["access_token"] and _token_cache["expires_at"] > now:
    return _token_cache["access_token"]

  rt = load_refresh_token()
  if not rt:
    raise RuntimeError("Cafe24 refresh_token not found in DB. Run OAuth install/authorize first.")

  return _refresh_access_token_with(rt)
