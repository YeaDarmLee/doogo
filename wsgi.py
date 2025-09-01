import os
from dotenv import load_dotenv

# 실행 환경 설정 (dev 또는 prod)
FLASK_ENV = os.getenv("FLASK_ENV", "prod")

# 올바른 .env 파일 로드
env_file = f".env.{FLASK_ENV}"
if os.path.exists(env_file):
  load_dotenv(env_file)

# Flask 앱 실행
from application import app

if __name__ == "__main__":
  app.run(
    host="0.0.0.0",
    port=int(os.getenv("PORT")),  # 기본값 8080
    debug=os.getenv("DEBUG").lower() == "true"
  )
