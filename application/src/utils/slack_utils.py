# application/src/utils/slack_utils.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import os
from typing import Optional
from slack_sdk import WebClient as _SlackClient
from application.src.service import slackService as _slack_svc

# slackService.client 가 이미 있으면 재사용 (프로젝트 규칙 준수)
_client = getattr(_slack_svc, "client", None)

def ensure_client() -> _SlackClient:
  """
  Slack WebClient 반환.
  - slackService.client 가 있으면 재사용
  - 없으면 SLACK_BOT_TOKEN 으로 새로 생성
  """
  global _client
  if _client:
    return _client
  token = os.getenv("SLACK_BOT_TOKEN", "").strip()
  if not token:
    raise RuntimeError("SLACK_BOT_TOKEN is not set.")
  _client = _SlackClient(token=token)
  return _client

def post_text(channel_id: str, text: str) -> None:
  """
  채널 ID(또는 #name)에 텍스트 메시지 전송.
  """
  if not channel_id:
    return
  cli = ensure_client()
  # '#name' 이면 이름을 ID로 해석 시도
  if channel_id.startswith("#"):
    ch = resolve_channel_id_by_name(channel_id.lstrip("#"))
    channel_id = ch or channel_id
  cli.chat_postMessage(channel=channel_id, text=text)

def resolve_channel_id_by_name(name: str) -> Optional[str]:
  """
  공개 채널 또는 봇이 멤버인 비공개 채널 목록에서 name에 해당하는 채널 ID 검색.
  """
  try:
    cli = ensure_client()
    cursor = None
    types = "public_channel,private_channel"
    for _ in range(20):
      resp = cli.conversations_list(limit=1000, cursor=cursor, types=types)
      for ch in resp.get("channels", []):
        if ch.get("name") == name:
          return ch.get("id")
      cursor = resp.get("response_metadata", {}).get("next_cursor")
      if not cursor:
        break
  except Exception:
    return None
  return None
