import os
from datetime import timedelta
from application.src.config.DatabaseConfig import DatabaseConfig

class Config:
  """ Flask 환경별 설정 관리 """
  
  # 환경 변수 로드
  FLASK_ENV = os.getenv("FLASK_ENV")
  DEBUG = os.getenv("DEBUG").lower() == "true"
  SECRET_KEY = os.getenv("SECRET_KEY")
  JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")

  # 세션 설정
  SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES"))
  PERMANENT_SESSION_LIFETIME = timedelta(minutes=SESSION_TIMEOUT_MINUTES)

  # SQLAlchemy DB URI (DatabaseConfig에서 가져오기)
  SQLALCHEMY_DATABASE_URI = DatabaseConfig.getUri()
  SQLALCHEMY_TRACK_MODIFICATIONS = False

  # 기타 설정
  SCHEDULER_API_ENABLED = True  # Flask-APScheduler 사용 옵션
  JWT_TOKEN_LOCATION = ["cookies"]
  JWT_ACCESS_COOKIE_NAME = "access_token"
  JWT_COOKIE_CSRF_PROTECT = False
