# application/src/service/eformsign_service.py
# -*- coding: utf-8 -*-

import os
import time
import base64
import json
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

import requests

log = logging.getLogger(__name__)

class EformsignError(Exception):
  def __init__(self, message: str, status: Optional[int] = None, payload: Optional[Dict[str, Any]] = None):
    super().__init__(message)
    self.status = status
    self.payload = payload or {}

@dataclass
class TokenResponse:
  access_token: str
  refresh_token: Optional[str]
  api_url: Optional[str]
  issued_at_ms: int
  expires_in: Optional[int]  # seconds
  raw: Dict[str, Any]

  @property
  def expires_at_ms(self) -> Optional[int]:
    if self.expires_in is None:
      return None
    return self.issued_at_ms + (self.expires_in * 1000)

def _get_in(obj: Any, path: List[str], default=None):
  cur = obj
  for k in path:
    if not isinstance(cur, dict):
      return default
    cur = cur.get(k)
    if cur is None:
      return default
  return cur

class EformsignService:
  """
  - 토큰 발급: POST https://service.eformsign.com/v2.0/api_auth/access_token
      Headers:
        Authorization: Bearer <base64(API_KEY)>
        eformsign_signature: Bearer <SIGNATURE_TOKEN>
      Body:
        { "execution_time": <epoch_ms>, "member_id": "<eformsign_account_id>" }
  - 템플릿 문서 생성/전송: POST {api_url}/v2.0/api/documents?template_id={...}
  """
  def __init__(
    self,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    signature_bearer: Optional[str] = None,
    member_id: Optional[str] = None,
    timeout: Optional[float] = None,
  ):
    # token 발급용 base_url (고정 도메인)
    self.base_url = (base_url or os.getenv("EFORMSIGN_BASE_URL") or "https://service.eformsign.com/v2.0").rstrip("/")
    self.api_key = api_key or os.getenv("EFORMSIGN_API_KEY") or ""
    self.signature_bearer = signature_bearer or os.getenv("EFORMSIGN_SIGNATURE_BEARER") or ""
    self.member_id = member_id or os.getenv("EFORMSIGN_MEMBER_ID") or ""
    self.timeout = timeout or float(os.getenv("EFORMSIGN_TIMEOUT", "15"))

    # 문서 전송 기본값 (ENV로 커스터마이즈)
    self.default_template_id = os.getenv("EFORMSIGN_TEMPLATE_ID") or ""
    self.default_document_name = os.getenv("EFORMSIGN_DOC_NAME", "공급사 계약서")
    self.default_comment = os.getenv("EFORMSIGN_DOC_COMMENT", "계약서 확인 및 작성 부탁드립니다.")
    self.default_valid_days = int(os.getenv("EFORMSIGN_DOC_VALID_DAYS", "7"))

    if not self.api_key:
      raise EformsignError("EFORMSIGN_API_KEY is required")
    if not self.signature_bearer:
      raise EformsignError("EFORMSIGN_SIGNATURE_BEARER is required (검증유형이 Bearer token 인 경우 필수)")
    if not self.member_id:
      raise EformsignError("EFORMSIGN_MEMBER_ID is required")

  # ---- helpers
  @staticmethod
  def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("utf-8")

  @staticmethod
  def _now_ms() -> int:
    return int(time.time() * 1000)

  def _token_headers(self) -> Dict[str, str]:
    return {
      "Authorization": f"Bearer {self._b64(self.api_key)}",
      "eformsign_signature": f"Bearer {self.signature_bearer}",
      "Content-Type": "application/json; charset=UTF-8",
    }

  @staticmethod
  def _bearer_headers(access_token: str) -> Dict[str, str]:
    return {
      "Authorization": f"Bearer {access_token}",
      "Content-Type": "application/json; charset=UTF-8",
    }

  # ---- token
  def issue_access_token(self) -> TokenResponse:
    url = f"{self.base_url}/api_auth/access_token"
    issued_at_ms = self._now_ms()
    body = {
      "execution_time": issued_at_ms,
      "member_id": self.member_id,
    }

    try:
      resp = requests.post(url, headers=self._token_headers(), json=body, timeout=self.timeout)
    except requests.RequestException as e:
      raise EformsignError(f"HTTP request failed: {e}") from e

    if resp.status_code != 200:
      raise EformsignError(
        f"eformsign token request failed (HTTP {resp.status_code})",
        status=resp.status_code,
        payload={"text": resp.text},
      )

    try:
      data = resp.json()
    except ValueError:
      raise EformsignError("Invalid JSON response from eformsign", status=resp.status_code, payload={"text": resp.text})

    # 포맷 A/B 모두 대응
    oauth = data.get("oauth_token") or {}
    api_key_obj = data.get("api_key") or {}
    company = (api_key_obj.get("company") or {}) if isinstance(api_key_obj, dict) else {}

    access_token = data.get("access_token") or oauth.get("access_token")
    refresh_token = data.get("refresh_token") or oauth.get("refresh_token")
    expires_in = data.get("expires_in") or oauth.get("expires_in")
    api_url = data.get("api_url") or company.get("api_url")

    if not access_token:
      keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
      raise EformsignError(
        f"Response missing access_token (keys={keys})",
        status=resp.status_code,
        payload=data,
      )

    tr = TokenResponse(
      access_token=access_token,
      refresh_token=refresh_token,
      api_url=api_url,
      issued_at_ms=issued_at_ms,
      expires_in=expires_in,
      raw=data,
    )

    log.info(f"[eformsign] token issued api_url={tr.api_url or '(none)'} expires_in={tr.expires_in}")
    return tr

  # ---- document
  def create_document_from_template(
    self,
    token: TokenResponse,
    template_id: Optional[str] = None,
    *,
    recipient_name: str,
    recipient_email: str,
    document_name: Optional[str] = None,
    comment: Optional[str] = None,
    use_sms: bool = False,
    password: Optional[str] = None,
    valid_days: Optional[int] = None,
    fields: Optional[List[Dict[str, Any]]] = None,
  ) -> Dict[str, Any]:
    if not token or not token.access_token:
      raise EformsignError("create_document_from_template: token(access_token) is required")
    api_base = (token.api_url or "").rstrip("/")
    if not api_base:
      raise EformsignError("create_document_from_template: token.api_url is missing (use api_url from access token response)")
    tid = (template_id or self.default_template_id).strip()
    if not tid:
      raise EformsignError("EFORMSIGN_TEMPLATE_ID is required")

    url = f"{api_base}/v2.0/api/documents"
    params = {"template_id": tid}

    body = {
      "document": {
        "document_name": document_name or self.default_document_name,
        "comment": comment or self.default_comment,
        "recipients": [
          {
            "step_type": "05",           # Quickstart 기준 수신자 단계 예시
            "use_mail": True,
            "use_sms": bool(use_sms),
            "member": {
              "name": recipient_name,
              "id": recipient_email,
              "sms": {
                "country_code": "+82",
                "phone_number": ""
              }
            },
            "auth": {
              **({"password": password} if password else {}),
              "valid": {
                "day": int(valid_days or self.default_valid_days),
                "hour": 0
              }
            }
          }
        ],
        "fields": fields or [],
        "select_group_name": "",
        "notification": []
      }
    }

    try:
      resp = requests.post(
        url,
        headers=self._bearer_headers(token.access_token),
        params=params,
        json=body,  # ← JSON 인코딩 안전
        timeout=self.timeout,
      )
    except requests.RequestException as e:
      raise EformsignError(f"HTTP request failed: {e}") from e

    if resp.status_code != 200:
      raise EformsignError(
        f"eformsign create_document failed (HTTP {resp.status_code})",
        status=resp.status_code,
        payload={"text": resp.text},
      )

    try:
      data = resp.json()
    except ValueError:
      raise EformsignError("Invalid JSON response from eformsign", status=resp.status_code, payload={"text": resp.text})

    # 다양한 응답 포맷에서 문서 ID 추출
    possible_paths = [
      ["document", "id"],
      ["document", "document_id"],
      ["documentId"],
      ["id"],
      ["document_id"],
      ["result", "document_id"],
      ["result", "id"],
    ]
    doc_id = None
    for p in possible_paths:
      v = _get_in(data, p)
      if v:
        doc_id = v
        break

    # 호출부 편의: 정규화 키(document_id)를 보정
    if isinstance(data, dict):
      data.setdefault("document_id", doc_id)

    log.info(f"[eformsign] document created template_id={tid} recipient={recipient_email} document_id={doc_id or '(unknown)'}")
    return data

# ---- optional CLI test
if __name__ == "__main__":
  logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
  svc = EformsignService()
  token = svc.issue_access_token()
  print(json.dumps({
    "api_url": token.api_url,
    "access_token_len": len(token.access_token),
    "refresh_token_len": len(token.refresh_token or ""),
    "expires_in": token.expires_in,
    "issued_at_ms": token.issued_at_ms,
  }, ensure_ascii=False, indent=2))
