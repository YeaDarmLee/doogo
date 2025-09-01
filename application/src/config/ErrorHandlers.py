from flask import render_template, redirect, url_for, jsonify, make_response, request
from flask_jwt_extended import jwt_required, get_jwt_identity

def registerErrorHandlers(app, jwt):
  """
  에러 핸들러를 등록하는 함수
  :param app: Flask 앱 인스턴스
  :param jwt: JWTManager 인스턴스
  """
  # 404 ERROR
  @app.errorhandler(404)
  def error404(error):
    return render_template('common/404.html'), 404
    
  # 500 ERROR
  @app.errorhandler(500)
  def error500(error):
    return render_template('common/500.html'), 500

  # JWT가 없거나 유효하지 않을 때
  @jwt.unauthorized_loader
  def unauthorizedLoader(msg):
    return redirect('/login')

  # JWT가 손상되었거나 잘못된 경우
  @jwt.invalid_token_loader
  def invalidTokenLoader(msg):
    return redirect('/login')

  # JWT 만료 시
  @jwt.expired_token_loader
  def expiredTokenLoader(jwtHeader, jwtPayload):
    return redirect('/login')