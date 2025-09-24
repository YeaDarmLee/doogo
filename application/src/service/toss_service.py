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
