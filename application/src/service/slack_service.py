# -*- coding: utf-8 -*-
"""
Slack 공통 유틸 모듈 (WebClient 싱글톤 보유)

핵심 설계
- 이 모듈이 Slack WebClient 를 '생성/보유'한다. (모든 시작은 여기서)
- 서비스 레이어(slackService.py)는 이 모듈의 get_client()/ensure_client()를 호출해
  이미 생성된 클라이언트를 받아서만 사용한다 (의존성 단일화).
- API 단건 호출(메시지/채널/사용자/파일 업로드)은 여기에서 안전하게 캡슐화한다.
  오케스트레이션(온보딩/방송/웰컴)은 slackService에서 조립한다.

정책
- 429(레이트리밋) 발생 시 Retry-After 기준 1회 자동 재시도.
- '#채널명' 식별자 허용 → 내부에서 name→ID 해석 후 호출.
- 실패 시 False/None 반환하여 호출부가 로직을 이어가거나 결정할 수 있게 한다.
"""

from __future__ import annotations

import os
import time
import logging
from typing import Optional, Iterable, Union

from slack_sdk import WebClient as _SlackClient
from slack_sdk.errors import SlackApiError

_logger = logging.getLogger("slack.utils")

# 이 모듈이 '주도적으로' 보유하는 WebClient 싱글톤
_CLIENT: Optional[_SlackClient] = None

# ========= 환경변수 =========
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

# =============================================================================
# 클라이언트 생성/반환
# =============================================================================
def _build_client_from_env() -> _SlackClient:
  """
  환경변수에서 토큰을 읽어 Slack WebClient 를 생성한다.
  - SLACK_BOT_TOKEN 미설정 시 RuntimeError.
  """
  token = SLACK_BOT_TOKEN
  if not token:
    raise RuntimeError("SLACK_BOT_TOKEN is not set.")
  return _SlackClient(token=token)


def ensure_client() -> _SlackClient:
  """
  모듈 내 싱글톤 WebClient 를 보장(없으면 생성, 있으면 재사용).
  서비스 레이어는 반드시 이 함수를 통해 클라이언트를 획득해야 한다.
  """
  global _CLIENT
  if _CLIENT is None:
    _CLIENT = _build_client_from_env()
  return _CLIENT


def get_client() -> _SlackClient:
  """
  이미 생성된 WebClient 를 반환. (없으면 ensure_client()로 생성)
  - 명시적으로 '의존성 주입' 느낌으로 쓰고 싶을 때 사용 가능
  """
  return ensure_client()


def reset_client() -> None:
  """
  (테스트용) 보유한 WebClient 싱글톤을 초기화.
  - 환경 변수 변경 후 재생성하고 싶을 때 사용.
  """
  global _CLIENT
  _CLIENT = None


# =============================================================================
# 내부 유틸: 레이트리밋 대기
# =============================================================================
def _sleep_if_rate_limited(e: SlackApiError) -> bool:
  """
  Slack API 가 429(레이트리밋)일 때 Retry-After 초만큼 대기 후 True.
  그 외에는 False.
  """
  try:
    resp = getattr(e, "response", None)
    if getattr(resp, "status_code", None) == 429:
      ra = int(resp.headers.get("Retry-After", "1"))
      _logger.warning(f"[rate-limit] Retry after {ra}s")
      time.sleep(max(1, ra))
      return True
  except Exception:
    pass
  return False


# =============================================================================
# 채널 ID/이름 해석 / 메시지 전송
# =============================================================================
def resolve_channel_id_by_name(name: str) -> Optional[str]:
  """
  채널 '이름'으로 채널 ID 조회.
  - 공개 채널은 모두 조회 가능
  - 비공개 채널은 '봇이 멤버'일 때만 목록에 노출
  """
  if not name:
    return None

  cli = ensure_client()
  cursor = None
  types = "public_channel,private_channel"

  for _ in range(20):  # 방어적 페이지 한도
    try:
      resp = cli.conversations_list(limit=1000, cursor=cursor, types=types)
    except SlackApiError as e:
      if _sleep_if_rate_limited(e):
        continue
      _logger.error(f"[list-fail] name={name} err={getattr(e, 'response', {}).get('data', {})}")
      return None

    for ch in resp.get("channels", []):
      if ch.get("name") == name:
        return ch.get("id")

    cursor = resp.get("response_metadata", {}).get("next_cursor")
    if not cursor:
      break

  return None


def post_text(channel: str, text: str, thread_ts: Optional[str] = None) -> bool:
  """
  텍스트 메시지 전송(단일 진입점).
  - channel: 채널 ID 또는 '#채널명'
  - thread_ts 지정 시 스레드로 전송
  """
  if not channel or not text:
    return False

  cli = ensure_client()
  if channel.startswith("#"):
    ch_id = resolve_channel_id_by_name(channel.lstrip("#"))
    channel = ch_id or channel.lstrip("#")

  for _ in range(2):
    try:
      payload = {"channel": channel, "text": text}
      if thread_ts:
        payload["thread_ts"] = thread_ts
      cli.chat_postMessage(**payload)
      return True
    except SlackApiError as e:
      if _sleep_if_rate_limited(e):
        continue
      _logger.error(f"[post-fail] ch={channel} err={getattr(e, 'response', {}).get('data', {})}")
      return False

  return False


# =============================================================================
# 채널 관리 (생성/아카이브/언아카이브/이름 변경)
# =============================================================================
def create_channel(name: str, private: bool = True, ensure_join: bool = True) -> Optional[str]:
  """
  채널 생성 유틸 (기본 비공개).
  - 공개 채널일 경우 ensure_join=True 시 조인 시도.
  - 'name_taken' 등의 케이스는 호출부에서 이름 변형 후 재호출 권장.
  """
  if not name:
    return None

  cli = ensure_client()
  for _ in range(2):
    try:
      resp = cli.conversations_create(name=name, is_private=private)
      ch = resp.get("channel", {})
      ch_id = ch.get("id")
      if ensure_join and ch_id:
        try:
          cli.conversations_join(channel=ch_id)
        except SlackApiError as je:
          _logger.info(f"[create.join-skip] id={ch_id} err={getattr(je, 'response', {}).get('data', {})}")
      return ch_id
    except SlackApiError as e:
      if _sleep_if_rate_limited(e):
        continue
      _logger.error(f"[create.fail] name={name} err={getattr(e, 'response', {}).get('data', {})}")
      return None
  return None


def archive_channel(channel: str) -> bool:
  """
  채널 아카이브(=사실상 삭제). 일반 플랜은 물리 삭제가 없음.
  """
  if not channel:
    return False

  cli = ensure_client()
  if channel.startswith("#"):
    ch_id = resolve_channel_id_by_name(channel.lstrip("#"))
    channel = ch_id or channel.lstrip("#")

  for _ in range(2):
    try:
      cli.conversations_archive(channel=channel)
      return True
    except SlackApiError as e:
      if _sleep_if_rate_limited(e):
        continue
      _logger.error(f"[archive.fail] ch={channel} err={getattr(e, 'response', {}).get('data', {})}")
      return False
  return False


def unarchive_channel(channel: str) -> bool:
  """
  아카이브된 채널 활성화.
  """
  if not channel:
    return False

  cli = ensure_client()
  if channel.startswith("#"):
    ch_id = resolve_channel_id_by_name(channel.lstrip("#"))
    channel = ch_id or channel.lstrip("#")

  for _ in range(2):
    try:
      cli.conversations_unarchive(channel=channel)
      return True
    except SlackApiError as e:
      if _sleep_if_rate_limited(e):
        continue
      _logger.error(f"[unarchive.fail] ch={channel} err={getattr(e, 'response', {}).get('data', {})}")
      return False
  return False


def rename_channel(channel: str, new_name: str) -> bool:
  """
  채널 이름 변경 (슬러그 검증은 호출부 권장).
  """
  if not channel or not new_name:
    return False

  cli = ensure_client()
  if channel.startswith("#"):
    ch_id = resolve_channel_id_by_name(channel.lstrip("#"))
    channel = ch_id or channel.lstrip("#")

  for _ in range(2):
    try:
      cli.conversations_rename(channel=channel, name=new_name)
      return True
    except SlackApiError as e:
      if _sleep_if_rate_limited(e):
        continue
      _logger.error(f"[rename.fail] ch={channel} name={new_name} err={getattr(e, 'response', {}).get('data', {})}")
      return False
  return False


# =============================================================================
# 파일 업로드
# =============================================================================
def upload_file(
  channels: Union[str, Iterable[str]],
  filepath: Optional[str] = None,
  content: Optional[bytes] = None,
  *,
  filename: Optional[str] = None,
  title: Optional[str] = None,
  initial_comment: Optional[str] = None
) -> bool:
  """
  파일 업로드(files.uploadV2). 단일 채널 기준으로 호출되므로 채널 수만큼 반복.
  - filepath 또는 content 중 하나는 필수.
  - content 사용 시 filename 지정 권장.
  """
  cli = ensure_client()

  # 채널 목록 정규화 및 '#name' → ID
  if isinstance(channels, str):
    channels = [channels]
  resolved = []
  for ch in channels:
    if not ch:
      continue
    if ch.startswith("#"):
      cid = resolve_channel_id_by_name(ch.lstrip("#"))
      resolved.append(cid or ch.lstrip("#"))
    else:
      resolved.append(ch)
  resolved = [c for c in resolved if c]

  if not resolved:
    _logger.error("[upload] no channels resolved")
    return False
  if not filepath and content is None:
    _logger.error("[upload] no file content")
    return False

  for ch in resolved:
    ok = False
    for _ in range(2):
      try:
        if filepath:
          cli.files_upload_v2(
            channel=ch,
            file=filepath,
            filename=filename or os.path.basename(filepath),
            title=title,
            initial_comment=initial_comment
          )
        else:
          if not filename:
            _logger.error("[upload] filename required for raw content")
            return False
          cli.files_upload_v2(
            channel=ch,
            content=content,
            filename=filename,
            title=title,
            initial_comment=initial_comment
          )
        ok = True
        break
      except SlackApiError as e:
        if _sleep_if_rate_limited(e):
          continue
        _logger.error(f"[upload.fail] ch={ch} err={getattr(e, 'response', {}).get('data', {})}")
        return False
    if not ok:
      return False

  return True


# =============================================================================
# 사용자 조회/초대
# =============================================================================
def lookup_user_id_by_email(email: str) -> Optional[str]:
  """
  이메일로 사용자 ID 조회. 가입 전 이메일이면 404 → None.
  """
  if not email:
    return None

  cli = ensure_client()
  for _ in range(2):
    try:
      resp = cli.users_lookupByEmail(email=email)
      return resp.get("user", {}).get("id")
    except SlackApiError as e:
      if _sleep_if_rate_limited(e):
        continue
      _logger.warning(f"[lookup-fail] email={email} err={getattr(e, 'response', {}).get('data', {})}")
      return None
  return None


def invite_user(channel: str, user: str) -> bool:
  """
  채널로 사용자 초대(간단 버전).
  - user 는 'U***' 또는 이메일.
  - already_in_channel / cant_invite_self / not_in_channel 은 업무상 무시 가능 → True.
  """
  if not channel or not user:
    return False

  cli = ensure_client()
  if channel.startswith("#"):
    ch_id = resolve_channel_id_by_name(channel.lstrip("#"))
    channel = ch_id or channel.lstrip("#")

  user_id = user
  if not user_id.startswith("U"):
    user_id = lookup_user_id_by_email(user)
    if not user_id:
      _logger.warning(f"[invite.skip] user lookup failed user={user}")
      return False

  for _ in range(2):
    try:
      cli.conversations_invite(channel=channel, users=user_id)
      return True
    except SlackApiError as e:
      if _sleep_if_rate_limited(e):
        continue
      data = getattr(getattr(e, "response", None), "data", None)
      err = (data.get("error") if isinstance(data, dict) else str(e))
      if err in ("already_in_channel", "cant_invite_self", "not_in_channel"):
        _logger.info(f"[invite-skip] ch={channel} user={user_id} err={err}")
        return True
      _logger.error(f"[invite-fail] ch={channel} user={user_id} err={err}")
      return False
  return False
