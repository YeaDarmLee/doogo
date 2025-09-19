from flask import Blueprint, render_template, request
from oauth2client.service_account import ServiceAccountCredentials
import os, gspread

main = Blueprint("main", __name__, url_prefix="/")

# main 페이지 이동
# @main.route("/")
# def index():
#   return render_template(
#     'index.html',
#     pageName='main'
#   )