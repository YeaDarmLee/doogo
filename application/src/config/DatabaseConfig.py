import os
from dotenv import load_dotenv

# .env 파일 로드 (환경 변수 파일 로드)
load_dotenv()

class DatabaseConfig:
  """
  데이터베이스 설정을 관리하는 클래스
  """

  # 환경 변수에서 데이터베이스 설정 값 로드
  HOST = os.getenv("DB_HOST")  # 데이터베이스 호스트 주소
  USER = os.getenv("DB_USER")  # 데이터베이스 사용자명
  PASSWORD = os.getenv("DB_PASSWORD")  # 데이터베이스 비밀번호
  DATABASE = os.getenv("DB_NAME")  # 사용할 데이터베이스 이름
  CHARSET = "utf8mb4"  # 문자 인코딩 설정 (이모지 등 지원)

  @classmethod
  def validateConfig(cls):
    """
    환경 변수가 올바르게 설정되었는지 검증
    :raises ValueError: 필수 환경 변수가 누락된 경우 예외 발생
    """
    missingVars = []
    for var in ["HOST", "USER", "PASSWORD", "DATABASE"]:
      if not getattr(cls, var):  # 해당 속성이 None 또는 빈 문자열인지 확인
        missingVars.append(var)

    if missingVars:
      raise ValueError(f"Missing required environment variables: {', '.join(missingVars)}")

  @classmethod
  def getUri(cls):
    """
    SQLAlchemy 연결 URI 반환
    :return: 데이터베이스 연결 URI 문자열
    """
    cls.validateConfig()  # 환경 변수 검증
    return f"mysql+pymysql://{cls.USER}:{cls.PASSWORD}@{cls.HOST}/{cls.DATABASE}?charset={cls.CHARSET}"
