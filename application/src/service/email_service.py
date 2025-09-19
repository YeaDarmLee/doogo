# -*- coding: utf-8 -*-
from __future__ import annotations
import os, ssl, smtplib
from typing import Optional
from email.message import EmailMessage

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
EMAIL_FROM = os.getenv("INVITE_EMAIL_FROM", "noreply@example.com")

def send_email(to: str, subject: str, text: str, html: Optional[str] = None) -> bool:
  if not (SMTP_HOST and SMTP_PORT and EMAIL_FROM and to):
    print(f"[email_service] missing SMTP config or recipient. to={to}")
    return False

  msg = EmailMessage()
  msg["Subject"] = subject
  msg["From"] = EMAIL_FROM
  msg["To"] = to
  msg.set_content(text)
  if html:
    msg.add_alternative(html, subtype="html")

  try:
    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
      s.starttls(context=ctx)
      if SMTP_USER and SMTP_PASS:
        s.login(SMTP_USER, SMTP_PASS)
      s.send_message(msg)
    print(f"[email_service] sent ok to={to} subject={subject}")
    return True
  except Exception as e:
    print(f"[email_service] send fail to={to} err={e}")
    return False
