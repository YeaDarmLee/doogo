from flask import Blueprint, render_template, request
from oauth2client.service_account import ServiceAccountCredentials
import os, gspread

login = Blueprint("login", __name__, url_prefix="/")

# login 페이지 이동
@login.route("/login")
def index():
  return render_template(
    'login.html'
  )