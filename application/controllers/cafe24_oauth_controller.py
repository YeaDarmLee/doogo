# application/src/controllers/cafe24_oauth_controller.py
# -*- coding: utf-8 -*-

from flask import Blueprint, request, jsonify, redirect
import os, requests, base64
from urllib.parse import urlparse, quote_plus
from typing import Optional
from application.src.service.cafe24_oauth_service import (
  save_refresh_token, save_access_token
)

cafe24_oauth_controller = Blueprint("cafe24_oauth_controller", __name__, url_prefix="/oauth")

# ===== ENV =====
CAFE24_BASE_URL = os.getenv("CAFE24_BASE_URL")          # e.g. https://<mall_id>.cafe24api.com
CLIENT_ID       = os.getenv("CAFE24_CLIENT_ID")
CLIENT_SECRET   = os.getenv("CAFE24_CLIENT_SECRET")
REDIRECT_URI    = os.getenv("CAFE24_REDIRECT_URI")      # e.g. https://<your-domain>/oauth/callback
SCOPE           = os.getenv("CAFE24_SCOPE", "").strip() # e.g. mall.read_order,mall.read_product

def _extract_mall_id_from_base(url: str) -> Optional[str]:
  """
  https://abc123.cafe24api.com  ->  abc123
  """
  try:
    host = urlparse(url).hostname or ""
    return host.split(".")[0] if host else None
  except Exception:
    return None

@cafe24_oauth_controller.route("/install")
def install():
  """
  카페24 OAuth '동의' 화면으로 리다이렉트.
  서버에서 정확한 authorize URL을 만들어 인코딩/줄바꿈 실수를 방지.
  """
  missing = [k for k, v in {
    "CAFE24_BASE_URL": CAFE24_BASE_URL,
    "CAFE24_CLIENT_ID": CLIENT_ID,
    "CAFE24_REDIRECT_URI": REDIRECT_URI
  }.items() if not v]
  if missing:
    return jsonify({"ok": False, "error": f"missing env: {', '.join(missing)}"}), 400

  auth_url = (
    f"{CAFE24_BASE_URL}/api/v2/oauth/authorize"
    f"?response_type=code"
    f"&client_id={CLIENT_ID}"
    f"&redirect_uri={quote_plus(REDIRECT_URI)}"
    f"&state=doogo-setup"
  )
  if SCOPE:
    auth_url += f"&scope={quote_plus(SCOPE)}"

  return redirect(auth_url, code=302)

@cafe24_oauth_controller.route("/callback")
def callback():
  """
  카페24가 'code'를 붙여 호출하는 리다이렉트 엔드포인트.
  code는 1분 내 access/refresh 토큰으로 교환해야 한다.
  """
  # authorize 단계 에러
  if "error" in request.args:
    return jsonify({
      "ok": False,
      "stage": "authorize",
      "error": request.args.get("error"),
      "error_description": request.args.get("error_description"),
    }), 400

  code = request.args.get("code")
  state = request.args.get("state")

  if not code:
    return jsonify({"ok": False, "error": "missing code"}), 400

  if state and state != "doogo-setup":
    print(f"[Cafe24 OAuth] WARN: unexpected state received: {state}")

  # ---- 토큰 교환 (HTTP Basic Authorization 필요) ----
  token_url = f"{CAFE24_BASE_URL}/api/v2/oauth/token"
  basic = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode("utf-8")).decode("utf-8")

  try:
    resp = requests.post(
      token_url,
      headers={
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/x-www-form-urlencoded",
      },
      data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
      },
      timeout=10
    )
    resp.raise_for_status()
  except requests.HTTPError:
    return jsonify({
      "ok": False,
      "stage": "token",
      "status": resp.status_code if 'resp' in locals() else None,
      "body": resp.text if 'resp' in locals() else None
    }), 400
  except requests.RequestException as e:
    return jsonify({"ok": False, "stage": "token", "error": str(e)}), 500

  tok = resp.json()
  access     = tok.get("access_token")
  refresh    = tok.get("refresh_token")
  raw_scope = tok.get("scope") or tok.get("scopes")
  if isinstance(raw_scope, list):
    scope_resp = ",".join(raw_scope)
  else:
    scope_resp = str(raw_scope) if raw_scope is not None else None
  expires_in = tok.get("expires_in", 7200)

  mall_id = _extract_mall_id_from_base(CAFE24_BASE_URL)

  # DB 저장
  if refresh:
    save_refresh_token(refresh, mall_id=mall_id, scope=scope_resp)
  if access:
    save_access_token(access, expires_in=expires_in, scope=scope_resp)

  return jsonify({
    "ok": True,
    "mall_id": mall_id,
    "has_refresh_token": bool(refresh),
    "scope": scope_resp
  })
