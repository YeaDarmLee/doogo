# -*- coding: utf-8 -*-
from application.src.models import db

class WebhookEvent(db.Model):
  __tablename__ = 'webhook_events'

  id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
  dedupe_key = db.Column(db.String(128), nullable=False, unique=True)
  webhook_id = db.Column(db.String(64))
  topic = db.Column(db.String(128))
  sig_verified = db.Column(db.Boolean, default=False, nullable=False)
  received_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False)
  body_json = db.Column(db.Text)
