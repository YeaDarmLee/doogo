# application/src/service/cafe24_boards_service.py
# -*- coding: utf-8 -*-
import logging, os
from typing import Dict, Any
from flask import current_app
from application.src.service.slackService import client  # 이미 준비된 WebClient

logger = logging.getLogger("cafe24.boards")

class Cafe24BoardsService:
  def __init__(self, broadcast_env: str = "SLACK_BROADCAST_CHANNEL_ID"):
    self.broadcast = os.getenv(broadcast_env, "").strip()

  def _post_to_channel(self, channel_id: str, text: str):
    if not channel_id:
      raise ValueError("Broadcast channel is not configured (SLACK_BROADCAST_CHANNEL_ID).")
    # slackService.client 직접 사용
    client.chat_postMessage(channel=channel_id, text=text)

  def notify_board_created(self, payload: Dict[str, Any], topic: str):
    try:
      resource = payload.get("resource") or {}
      mall_id = resource.get("mall_id")
      board_no = resource.get("board_no")
      post_no = resource.get("no")
      has_parent = resource.get("has_parent")
      member_id = resource.get("member_id")
      writer = resource.get("writer")

      text = (
        ":memo: *게시물 등록 알림*\n"
        f"- Mall: {mall_id}\n"
        f"- Board No: {board_no}\n"
        f"- Post No: {post_no}\n"
        f"- 부모글 여부: {has_parent}\n"
        f"- 작성자: {writer} ({member_id})"
      )

      self._post_to_channel(self.broadcast, text)
      logger.info(f"[board-ok] board={board_no} post={post_no} writer={writer}")
      return True

    except Exception as e:
      logger.exception(f"[board-fail] {e}")
      try:
        if current_app:
          current_app.logger.exception(e)
      except Exception:
        pass
      return False
