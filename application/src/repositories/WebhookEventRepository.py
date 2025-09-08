# -*- coding: utf-8 -*-
from typing import Optional
from application.src.models import db
from application.src.models.WebhookEvent import WebhookEvent

class WebhookEventRepository:
  @staticmethod
  def get_by_dedupe(dedupe_key: str) -> Optional[WebhookEvent]:
    return WebhookEvent.query.filter_by(dedupe_key=dedupe_key).first()

  @staticmethod
  def insert(dedupe_key: str, webhook_id: str, topic: str, sig_verified: bool, body_json: str) -> WebhookEvent:
    e = WebhookEvent(
      dedupe_key=dedupe_key,
      webhook_id=webhook_id,
      topic=topic,
      sig_verified=sig_verified,
      body_json=body_json
    )
    db.session.add(e)
    db.session.commit()
    return e
