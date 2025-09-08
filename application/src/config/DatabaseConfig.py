# application/src/config/DatabaseConfig.py
import os
from urllib.parse import quote_plus
from dotenv import load_dotenv
load_dotenv()

class DatabaseConfig:
  HOST = os.getenv("DB_HOST")
  USER = os.getenv("DB_USER")
  PASSWORD = os.getenv("DB_PASSWORD")
  DATABASE = os.getenv("DB_NAME")
  PORT = os.getenv("DB_PORT", "3306")
  CHARSET = "utf8mb4"

  @classmethod
  def validateConfig(cls):
    missing = []
    for var in ["HOST", "USER", "PASSWORD", "DATABASE"]:
      if not getattr(cls, var):
        missing.append(var)
    if missing:
      raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

  @classmethod
  def _sanitize_host(cls, host: str) -> str:
    h = (host or "").strip()
    if h.startswith("@"):
      h = h[1:]
    for bad in ("mysql://", "http://", "https://"):
      if h.startswith(bad):
        h = h[len(bad):]
    return h.lstrip("/").strip()

  @classmethod
  def getUri(cls) -> str:
    cls.validateConfig()
    host = cls._sanitize_host(cls.HOST)
    user = quote_plus(cls.USER)
    pw = quote_plus(cls.PASSWORD)
    return f"mysql+pymysql://{user}:{pw}@{host}:{cls.PORT}/{cls.DATABASE}?charset={cls.CHARSET}"
