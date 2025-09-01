from flask import Blueprint, render_template, request
from oauth2client.service_account import ServiceAccountCredentials
import os, gspread

supplier = Blueprint("supplier", __name__, url_prefix="/supplier")

# supplier 페이지 이동
@supplier.route("/")
def index():
  return render_template(
    'supplier.html',
    pageName='supplier'
  )