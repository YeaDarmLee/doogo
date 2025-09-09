# application/src/service/slack_verify.py
import os, hmac, hashlib, time
from flask import Request

SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET", "")

def verify_slack_request(req: Request, tolerance_sec: int = 60 * 5) -> bool:
  # Slack-Signature style: v0=hexdigest
  if not SLACK_SIGNING_SECRET:
    return True  # 개발 단계에서만 패스 (운영은 반드시 검증)
  ts = req.headers.get("X-Slack-Request-Timestamp")
  sig = req.headers.get("X-Slack-Signature")
  if not ts or not sig:
    return False
  try:
    ts = int(ts)
  except Exception:
    return False
  # 리플레이 방지
  if abs(time.time() - ts) > tolerance_sec:
    return False
  body = req.get_data(as_text=True) or ""
  basestring = f"v0:{ts}:{body}".encode("utf-8")
  my_sig = "v0=" + hmac.new(
    SLACK_SIGNING_SECRET.encode("utf-8"),
    basestring,
    hashlib.sha256
  ).hexdigest()
  # 안전 비교
  return hmac.compare_digest(my_sig, sig)
