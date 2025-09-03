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
# registerErrorHandlers(app, jwt)

# 백그라운드 스케줄러 가동
from application.src.scheduler import start_scheduler
start_scheduler(app)
  
# 블루프린트 및 라우트 관련 모듈 import (순환 참조 방지)
from .src.service.main import main
from .src.service.login import login
from .src.service.supplier import supplier

# 블루프린트 등록
app.register_blueprint(main)
app.register_blueprint(login)
app.register_blueprint(supplier)

from .controllers.cafe24_webhooks import cafe24
app.register_blueprint(cafe24)