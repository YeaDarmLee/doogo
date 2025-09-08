# application/src/service/cafe24_boards_service.py
# -*- coding: utf-8 -*-
import logging, os
from typing import Dict, Any
from flask import current_app
from application.src.repositories.SupplierListRepository import SupplierListRepository
from application.src.service.slackService import client

logger = logging.getLogger("cafe24.boards")
SLACK_BROADCAST_CHANNEL_ID = os.getenv("SLACK_BROADCAST_CHANNEL_ID", "").strip()

class Cafe24BoardsService:
  def __init__(self):
    pass

  def _post_to_channel(self, channel_id: str, text: str):
    cli = self._ensure_slack_client()
    cli.chat_postMessage(channel=channel_id, text=text)
    
  def notify_board_created(self, payload: Dict[str, Any], topic: str):
    try:
      resource = payload.get("resource", {})
      mall_id = resource.get("mall_id")
      board_no = resource.get("board_no")
      post_no = resource.get("no")
      member_id = resource.get("member_id")
      writer = resource.get("writer")

      text = (
        f":memo: *게시물 등록 알림*\n"
        f"- Mall: {mall_id}\n"
        f"- Board No: {board_no}\n"
        f"- Post No: {post_no}\n"
        f"- 작성자: {writer} ({member_id})"
      )
      self._post_to_channel(SLACK_BROADCAST_CHANNEL_ID, text)

    except Exception as e:
      logger.exception(f"[board-fail] {e}")
      if current_app:
        current_app.logger.exception(e)
      return False
