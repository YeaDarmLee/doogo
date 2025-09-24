from flask import Blueprint, render_template, request, redirect, url_for, make_response
from flask_jwt_extended import create_access_token, set_access_cookies, unset_jwt_cookies, jwt_required
from datetime import timedelta
from werkzeug.security import check_password_hash
from application.src.repositories.UserListRepository import UserListRepository
from application.src.config.Config import Config

login = Blueprint("login", __name__, url_prefix="/")

# GET: 로그인 페이지
@login.route("/login", methods=["GET"])
def index():
  error = request.args.get("error")
  return render_template("login.html", error=error)

def verify_password(stored_hash: str, plain: str) -> bool:
  """
  hashlib.scrypt 미지원 빌드에서 scrypt 해시를 만났을 때 AttributeError가 터지는 문제를 흡수.
  scrypt 해시는 False로 처리하여 비밀번호 재설정 유도.
  """
  try:
    return check_password_hash(stored_hash, plain)
  except AttributeError as e:
    # e.g. module 'hashlib' has no attribute 'scrypt'
    if stored_hash and stored_hash.startswith("scrypt:"):
      return False
    raise

# POST: 인증 처리
@login.route("/login", methods=["POST"])
def do_login():
  user_id = (request.form.get("userId") or "").strip()
  password = (request.form.get("userPassword") or "").strip()
  # 템플릿에 따라 rememberMe 또는 customCheck가 올 수 있으므로 둘 다 체크
  remember = (request.form.get("rememberMe") == "on") or (request.form.get("customCheck") == "on")

  user = UserListRepository.findByUserId(user_id)
  if not user:
    return redirect(url_for("login.index", error="존재하지 않는 계정입니다."))

  # 비밀번호 해시 검증
  if not verify_password(user.password, password):
    # scrypt → False 반환된 경우도 포함 (환경 문제로 검증 불가)
    return redirect(url_for("login.index", error="비밀번호가 올바르지 않거나 검증할 수 없습니다. 비밀번호 재설정이 필요할 수 있습니다."))

  # 상태 체크(선택)
  if getattr(user, "statusCode", None) and user.statusCode != "A":
    return redirect(url_for("login.index", error="비활성화된 계정입니다."))

  # JWT 생성 및 쿠키 설정
  expires = timedelta(days=30) if remember else timedelta(hours=8)
  access_token = create_access_token(identity=user.userId, expires_delta=expires)

  resp = make_response(redirect(url_for("main.index")))
  set_access_cookies(resp, access_token)
  return resp

# 로그아웃: JWT 쿠키 삭제
@login.route("/logout", methods=["POST", "GET"])
def logout():
  resp = make_response(redirect(url_for("login.index")))
  unset_jwt_cookies(resp)
  return resp
