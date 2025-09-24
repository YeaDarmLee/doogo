from flask import Flask, render_template
from flask_jwt_extended import JWTManager

from application.src.models import db
from application.src.config.ErrorHandlers import registerErrorHandlers
from application.src.config.Config import Config  # 환경 설정 불러오기

# Flask 앱 초기화
app = Flask(__name__)

# Flask 환경 설정 적용
app.config.from_object(Config)

# `db`를 Flask 앱에 바인딩
db.init_app(app)

# JWT 초기화
jwt = JWTManager(app)

# 에러 핸들러 등록
registerErrorHandlers(app, jwt)

# 백그라운드 스케줄러 가동
from application.jobs.scheduler import start_scheduler
start_scheduler(app)
  
# 블루프린트 및 라우트 관련 모듈 import (순환 참조 방지)
from application.controllers.main import main
from application.controllers.login import login
from application.controllers.supplier import supplier
from application.controllers.payments import payments
from application.controllers.eformsign_webhook import eformsign_webhook
from application.controllers.cafe24_webhooks import cafe24_webhooks_bp
from application.controllers.slack_commands import slack_commands
from application.controllers.slack_interactions import slack_actions
from application.controllers.cafe24_oauth_controller import cafe24_oauth_controller

# 블루프린트 등록
app.register_blueprint(main)
app.register_blueprint(login)
app.register_blueprint(supplier)
app.register_blueprint(payments)

app.register_blueprint(eformsign_webhook)
app.register_blueprint(cafe24_webhooks_bp)
app.register_blueprint(slack_commands)
app.register_blueprint(slack_actions)
app.register_blueprint(cafe24_oauth_controller)

# 상태 매핑 딕셔너리
STATE_CODE_MAP = {
  "": "대기",
  "P": "대기",
  "I": "초대",
  "A": "성공",
  "E": "에러"
}
STATE_CONTRACT_CODE_MAP = {
  "": "슬랙대기",
  "P": "발송대기",
  "A": "발송완료",
  "SS": "계약완료",
  "S": "외부계약",
  "E": "에러"
}

def state_text(code):
  return STATE_CODE_MAP.get(code, "")
def contractState_text(code):
  return STATE_CONTRACT_CODE_MAP.get(code, "")

# Jinja 필터 등록
app.jinja_env.filters["state_text"] = state_text
app.jinja_env.filters["contractState_text"] = contractState_text