# -*- coding: utf-8 -*-
import os, json, base64
from typing import Any, Dict, Optional
import requests
from dotenv import load_dotenv

class TossPayoutsError(RuntimeError):
  pass

class TossPayoutsClient:
  """
  Toss Payments Payouts(지급대행) 전용 클라이언트
  - ENCRYPTION 모드(JWE) 필수 엔드포인트용 래퍼
  - 셀러 등록/수정/조회 등 공용 함수 제공
  """
  def __init__(
    self,
    secret_key: Optional[str] = None,
    api_base: str = "https://api.tosspayments.com",
    timeout: int = 30,
    session: Optional[requests.Session] = None,
  ):
    load_dotenv()
    self.api_base = (api_base or "https://api.tosspayments.com").rstrip("/")
    self.secret_key = secret_key or os.getenv("TOSSPAY_SECRET_KEY", "") + ":"
    self.timeout = timeout
    self.session = session or requests.Session()

    if not self.secret_key:
      raise ValueError("Missing secret_key (env TOSSPAY_SECRET_KEY)")

  # ---------- Low-level helpers ----------
  def _post_encrypted(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    api_key_bytes = self.secret_key.encode("utf-8")
    encoded_api_key = base64.b64encode(api_key_bytes).decode("utf-8")

    url = f"{self.api_base}{path}"
    
    headers = {
      "Authorization": f"Basic {encoded_api_key}",
      "Content-Type": "text/plain",
      "TossPayments-api-security-mode": "ENCRYPTION"
    }

    resp = self.session.post(
      url,
      headers=headers,
      data=json.dumps(body),
      timeout=self.timeout,
    )

    try:
      return resp.json()
    except ValueError:
      return {"raw": resp.text}

  # ---------- Sellers ----------
  def create_seller(self, seller: Dict[str, Any]) -> Dict[str, Any]:
    """
    셀러 등록 (POST /v2/sellers) — ENCRYPTION
    seller: 평문 JSON (문서 스펙 준수)
    """
    return self._post_encrypted("/v2/sellers", seller)

  # ---------- Payouts ----------
  def request_payouts(self, payouts):
    """
    지급대행 요청 (POST /v2/payouts) — ENCRYPTION/JWE
    - payouts: dict(단건) 또는 list[dict](최대 100건)
      각 아이템 스펙:
        {
          "refPayoutId": "고유ID",                         # 필수
          "destination": "seller_id",                     # 필수: /v2/sellers 응답의 id
          "scheduleType": "EXPRESS" or "SCHEDULED",      # 필수
          "payoutDate": "yyyy-MM-dd",                    # SCHEDULED일 때만 필수 (영업일, 1년 이내)
          "amount": {"currency": "KRW", "value": 12345}, # 필수
          "transactionDescription": "적요<=7자",          # 필수
          "metadata": {"key": "value"}                   # 선택(최대 5쌍)
        }
    - 반환: 토스가 돌려주는 암호화(JWE) 문자열(raw). (ENCRYPTION 응답은 복호화 필요)
    """
    # 목록/단건 정규화
    if isinstance(payouts, dict):
      items = [payouts]
    elif isinstance(payouts, (list, tuple)):
      items = list(payouts)
    else:
      raise TypeError("payouts는 dict 또는 list여야 합니다.")

    if not items:
      raise ValueError("요청할 지급 건이 없습니다.")
    if len(items) > 100:
      raise ValueError("한 번에 최대 100건까지만 요청 가능합니다.")

    # 문서상 ENCRYPTION 모드 POST /v2/payouts
    return self._post_encrypted("/v2/payouts", items)

  # ---------- Utility ----------
  def close(self) -> None:
    try:
      self.session.close()
    except Exception:
      pass
