from flask import Blueprint, render_template, request, redirect, url_for
from flask_jwt_extended import jwt_required
import os, gspread

main = Blueprint("main", __name__, url_prefix="/")

# main 페이지 이동
@main.route("/")
@jwt_required()
def index():
#   return render_template(
#     'index.html',
#     pageName='main'
#   )
  return redirect(url_for("supplier.index"))  # main 블루프린트 안에 home 함수로 이동