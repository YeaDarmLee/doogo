# application/src/service/toss_service.py
import os, base64, json, uuid, datetime, requests
from typing import Tuple, Dict, Any
from jwcrypto import jwk, jwe
from jwcrypto.common import json_encode

def _basic_auth() -> str:
  secret = os.getenv("TOSS_SECRET_KEY")  # live_sk_**** (시크릿 키)
  if not secret:
    raise RuntimeError("TOSS_SECRET_KEY 환경변수가 없습니다.")
  return "Basic " + base64.b64encode(f"{secret}:".encode()).decode()

def _now_iso():
  # KST 기준으로 생성(형식 문제 방지)
  return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec="seconds")

def _jwk_from_hex() -> jwk.JWK:
  # 보안 키: 64자리 hex → 32바이트
  hexkey = (os.getenv("TOSS_SECURITY_KEY") or "").strip()
  if not hexkey:
    raise RuntimeError("TOSS_SECURITY_KEY(64-hex) 환경변수가 없습니다.")
  raw = bytes.fromhex(hexkey)  # 여기서 ValueError 나면 hex 형식 아님
  return jwk.JWK(kty="oct", k=base64.urlsafe_b64encode(raw).rstrip(b"=").decode())

def _encrypt_jwe(payload: Dict[str, Any]) -> str:
  k = _jwk_from_hex()
  header = {"alg":"dir","enc":"A256GCM","iat":_now_iso(),"nonce":str(uuid.uuid4())}
  obj = jwe.JWE(plaintext=json.dumps(payload, ensure_ascii=False).encode(), protected=header)
  obj.add_recipient(k)
  return obj.serialize(compact=True)

def _decrypt_jwe(compact: str) -> Dict[str, Any]:
  k = _jwk_from_hex()
  obj = jwe.JWE()
  obj.deserialize(compact)
  obj.decrypt(k)
  try:
    return json.loads(obj.payload.decode())
  except Exception:
    return {"raw": obj.payload.decode(errors="ignore")}

def create_seller_encrypted(seller_body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
  """
  ENCRYPTION 강제 상점용: 바디(JWE)로 전송, 응답(JWE) 복호화
  """
  url = "https://api.tosspayments.com/v2/sellers"
  jwe_body = _encrypt_jwe(seller_body)
  headers = {
    "Authorization": _basic_auth(),
    "Content-Type": "text/plain",
    "TossPayments-api-security-mode": "ENCRYPTION",
  }
  r = requests.post(url, headers=headers, data=jwe_body)
  # 응답이 JSON이면 그대로, JWE면 복호화
  if r.headers.get("Content-Type","").startswith("application/json"):
    # 오류일 때 간혹 JSON으로 떨어질 수도 있으니 그대로 반환
    try:
      return r.status_code, r.json()
    except Exception:
      return r.status_code, {"raw": r.text}
  else:
    try:
      return r.status_code, _decrypt_jwe(r.text)
    except Exception:
      return r.status_code, {"raw": r.text, "decryptError": True}
# --- payout: 지급대행 요청 (ENCRYPTION) --------------------------------------
from typing import List, Union

def _validate_payout_item(item: Dict[str, Any]) -> None:
  missing = [k for k in ["refPayoutId", "destination", "scheduleType", "amount", "transactionDescription"] if k not in item]
  if missing:
    raise ValueError(f"payout item 필수 필드 누락: {missing}")
  st = item["scheduleType"]
  if st not in ("EXPRESS", "SCHEDULED"):
    raise ValueError("scheduleType은 'EXPRESS' 또는 'SCHEDULED' 이어야 합니다.")
  if st == "SCHEDULED" and not item.get("payoutDate"):
    raise ValueError("scheduleType=SCHEDULED 인 경우 payoutDate(yyyy-MM-dd)가 필수입니다.")
  desc = item.get("transactionDescription", "")
  if isinstance(desc, str) and len(desc) > 7:
    raise ValueError("transactionDescription(적요)은 최대 7자입니다.")
  amt = item["amount"]
  if not isinstance(amt, dict) or "currency" not in amt or "value" not in amt:
    raise ValueError("amount는 {'currency':'KRW','value':정수} 형태여야 합니다.")
  if amt.get("currency") != "KRW":
    raise ValueError("현재 통화는 KRW만 지원됩니다.")

def create_payouts_encrypted(payouts: Union[Dict[str, Any], List[Dict[str, Any]]]) -> Tuple[int, Dict[str, Any]]:
  """
  지급대행 요청 (배치 최대 100건)
  - Endpoint: POST https://api.tosspayments.com/v2/payouts
  - 보안: ENCRYPTION (요청/응답 모두 JWE)
  - 입력: 단일 dict 또는 dict 리스트
  - 출력: (status, 복호화된 응답 dict) — entityBody.items에 Payout 목록
  """
  url = "https://api.tosspayments.com/v2/payouts"

  # 1) 리스트 화 + 유효성 체크
  items: List[Dict[str, Any]] = payouts if isinstance(payouts, list) else [payouts]
  if not items:
    raise ValueError("payouts가 비어 있습니다.")
  if len(items) > 100:
    raise ValueError("한 번에 최대 100건까지 요청할 수 있습니다.")
  for it in items:
    _validate_payout_item(it)

  # 3) JWE 암호화
  jwe_body = _encrypt_jwe(items)

  # 4) 전송 (ENCRYPTION 헤더 + text/plain)
  headers = {
    "Authorization": _basic_auth(),
    "Content-Type": "text/plain",
    "TossPayments-api-security-mode": "ENCRYPTION",
  }
  r = requests.post(url, headers=headers, data=jwe_body)

  # 5) 응답 처리 (성공/실패 모두 JWE일 수 있음)
  ctype = r.headers.get("Content-Type", "")
  if ctype.startswith("application/json"):
    try:
      return r.status_code, r.json()
    except Exception:
      return r.status_code, {"raw": r.text}
  else:
    try:
      return r.status_code, _decrypt_jwe(r.text)
    except Exception:
      return r.status_code, {"raw": r.text, "decryptError": True}
    
def get_balance() -> Tuple[int, Dict[str, Any]]:
  """
  현재 상점의 정산 가능 잔액 조회
  - Endpoint: GET https://api.tosspayments.com/v2/balances
  - 보안: Basic Auth
  - 출력: (status, 응답 dict)
  """
  url = "https://api.tosspayments.com/v2/balances"
  headers = {
    "Authorization": _basic_auth(),
  }
  r = requests.get(url, headers=headers)

  try:
    return r.status_code, r.json()
  except Exception:
    return r.status_code, {"raw": r.text}

from typing import Optional

def list_payouts(
    limit: int = 10,
    startingAfter: Optional[str] = None,
    payoutDateGte: Optional[str] = None,
    payoutDateLte: Optional[str] = None
) -> Tuple[int, Dict[str, Any]]:
  """
  지급대행 요청 목록 조회
  - Endpoint: GET https://api.tosspayments.com/v2/payouts
  - 보안: Basic Auth
  - Query 파라미터:
      limit          (int, 기본 10, 최대 10000)
      startingAfter  (str, 선택)
      payoutDateGte  (str, yyyy-MM-dd, 선택)
      payoutDateLte  (str, yyyy-MM-dd, 선택)
  - 출력: (status, 응답 dict)
  """
  url = "https://api.tosspayments.com/v2/payouts"
  headers = {
    "Authorization": _basic_auth(),
  }
  params: Dict[str, Any] = {"limit": limit}
  if startingAfter:
    params["startingAfter"] = startingAfter
  if payoutDateGte:
    params["payoutDateGte"] = payoutDateGte
  if payoutDateLte:
    params["payoutDateLte"] = payoutDateLte

  r = requests.get(url, headers=headers, params=params)

  try:
    return r.status_code, r.json()
  except Exception:
    return r.status_code, {"raw": r.text}
  
def get_seller(seller_id: str) -> Tuple[int, Dict[str, Any]]:
  """
  셀러 단건 조회
  - Endpoint: GET https://api.tosspayments.com/v2/sellers/{id}
  - 보안: Basic Auth
  - 입력: seller_id (문자열, 예: "seller_a01k5x4nrmrterzprdmrbt0q8x9")
  - 출력: (status, 응답 dict)
  """
  url = f"https://api.tosspayments.com/v2/sellers/{seller_id}"
  headers = {
    "Authorization": _basic_auth(),
  }
  r = requests.get(url, headers=headers)

  try:
    return r.status_code, r.json()
  except Exception:
    return r.status_code, {"raw": r.text}

def list_sellers(
    limit: int = 10,
    startingAfter: Optional[str] = None
) -> Tuple[int, Dict[str, Any]]:
  """
  셀러 목록 조회
  - Endpoint: GET https://api.tosspayments.com/v2/sellers
  - 보안: Basic Auth
  - Query 파라미터:
      limit         (int, 기본 10, 최대 100)
      startingAfter (str, 선택, 이전 결과의 마지막 sellerId)
  - 출력: (status, 응답 dict)
  """
  url = "https://api.tosspayments.com/v2/sellers"
  headers = {
    "Authorization": _basic_auth(),
  }
  params: Dict[str, Any] = {"limit": limit}
  if startingAfter:
    params["startingAfter"] = startingAfter

  r = requests.get(url, headers=headers, params=params)

  try:
    return r.status_code, r.json()
  except Exception:
    return r.status_code, {"raw": r.text}
  
def list_settlements(start_date: str, end_date: str, limit: int = 1000, startingAfter: Optional[str] = None) -> Tuple[int, Dict[str, Any]]:
  """
  정산 내역 조회
  - Endpoint: GET https://api.tosspayments.com/v1/settlements
  - 보안: Basic Auth
  - Query 파라미터:
      startDate     (yyyy-MM-dd, 필수)
      endDate       (yyyy-MM-dd, 필수)
      limit         (int, 선택, 기본 100, 최대 5000)
      startingAfter (str, 선택, 페이징 커서)
  - 출력: (status, 응답 dict)
  """
  url = "https://api.tosspayments.com/v1/settlements"
  headers = {
    "Authorization": _basic_auth(),
  }
  params: Dict[str, Any] = {
    "startDate": start_date,
    "endDate": end_date,
    "size": limit,
  }
  if startingAfter:
    params["startingAfter"] = startingAfter

  r = requests.get(url, headers=headers, params=params)

  try:
    return r.status_code, r.json()
  except Exception:
    return r.status_code, {"raw": r.text}

def cancel_payout(payout_id: str):
  """
  지급대행 요청 취소 (예약(SCHEDULED)만 가능, 지급일 이전에만 가능)
  - Endpoint: POST /v1/payouts/sub-malls/settlements/{payoutKey}/cancel
  - 보안: Basic Auth (ENCRYPTION 아님)
  """
  url = f"https://api.tosspayments.com/v1/payouts/sub-malls/settlements/{payout_id}/cancel"
  headers = {
    "Authorization": _basic_auth(),
    "Content-Type": "application/json",
  }
  r = requests.post(url, headers=headers)  # 바디 없음
  try:
    return r.status_code, r.json()
  except Exception:
    return r.status_code, {"raw": r.text}