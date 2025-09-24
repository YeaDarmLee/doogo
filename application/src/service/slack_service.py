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
import logging, threading, traceback
from typing import Optional, Iterable, Union, Dict, Any, Tuple
from flask import current_app

from slack_sdk import WebClient as _SlackClient
from slack_sdk.errors import SlackApiError

from application.src.service.settlement_service import make_settlement_excel, prev_month_range

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
def _fmt_currency(value):
  try:
    if value in (None, "", "None"):
      return "0원"
    return f"{int(float(str(value).replace(',', '') )):,}원"
  except Exception:
    return f"{value}원"


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

def _build_settlement_button_blocks(payload: Dict[str, Any]) -> list:
  """
  '정산 확정하기' 버튼 블록.
  - value에는 문자열만 가능하므로 compact JSON 문자열로 인코딩해서 담는다.
  """
  import json
  btn_text = payload.get("button_text") or "정산 확정하기"

  value_json = json.dumps({
    "settlement_id": payload.get("settlement_id"),
    "destination": payload.get("destination"),
    "schedule_type": payload.get("schedule_type", "EXPRESS"),
    "payout_date": payload.get("payout_date"),
    "amount_value": int(payload.get("amount_value", 0)),
    "amount_currency": payload.get("amount_currency", "KRW"),
    "transaction_description": payload.get("transaction_description", "정산")
  }, ensure_ascii=False)

  return [
    {
      "type": "actions",
      "elements": [
        {
          "type": "button",
          "style": "primary",
          "text": { "type": "plain_text", "text": btn_text },
          "action_id": "payout_confirm",
          "value": value_json
        }
      ]
    }
  ]

def upload_file(
  supply_id: Optional[str] = None,
  channel: Optional[str] = None,
  start: Optional[str] = None,
  end: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
  """
  단일 채널 파일 업로드(files.uploadV2).
  """
  fpath, summary = make_settlement_excel(start, end, supply_id=supply_id, out_dir="/tmp")
  print(f"[slash:/settlement] excel_ready path={fpath} summary={summary} supply_code={supply_id}")
  
  def _bg(app):
    with app.app_context():
      try:
        cli = ensure_client()
        
        initial_comment = (
          f"*정산서 업로드 완료*\n기간: {start} ~ {end}\n"
          f"배송완료 {summary['delivered_rows']}건 · 취소처리 {summary['canceled_rows']}건\n"
          f"- *총 상품 결제 금액: {_fmt_currency(summary.get('gross_amount',0))}*\n"
          f"- *배송비: {_fmt_currency(summary.get('shipping_amount',0))}*\n"
          f"- *수수료: {_fmt_currency(summary.get('commission_amount',0))}*\n"
          f"- *총 합계 금액: {_fmt_currency(summary.get('final_amount',0))}*"
        )
        cli.files_upload_v2(
          channel=channel,  # 단수
          file=fpath,
          filename=os.path.basename(fpath),
          title=f"{start:%Y-%m} 정산서",
          initial_comment=initial_comment
        )
      except Exception as e:
        traceback.print_exc()
          
  app_obj = current_app._get_current_object()
  t = threading.Thread(target=_bg, args=(app_obj,), daemon=True)
  t.start()
  
  return fpath, summary

# slack_service.py 내 교체
def upload_file_with_button(
  supply_id: Optional[str] = None,
  channel: Optional[str] = None,
  start: Optional[str] = None,
  end: Optional[str] = None,
) -> bool:
  """
  files.uploadV2 → (파일 메시지 등장 대기) → 버튼 메시지(스레드) 전송
  - conversations.history를 폴링해 업로드된 파일이 포함된 메시지의 ts를 찾는다.
  - 찾으면 thread_ts로 버튼 메시지를 달아 '파일 먼저 → 버튼' 순서를 보장.
  """
  import os, time, traceback
  from typing import Optional
  from slack_sdk.errors import SlackApiError

  def _wait_file_message_ts(cli, channel_id: str, file_id: str, *, timeout_sec: int = 20, interval: float = 0.8) -> Optional[str]:
    """
    채널 히스토리에서 file_id를 포함한 메시지를 찾고 ts를 반환.
    """
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
      try:
        hist = cli.conversations_history(channel=channel_id, limit=50)
        for msg in hist.get("messages", []):
          files = msg.get("files") or []
          for f in files:
            if f.get("id") == file_id:
              return msg.get("ts")
      except Exception:
        pass
      time.sleep(interval)
    return None

  def _ensure_channel_id(cli, ch: str) -> str:
    # 이미 ID면 그대로, '#name'이나 'name'이면 lookup
    if ch and ch.startswith(("C", "G")) and len(ch) >= 9:
      return ch
    name = ch.lstrip("#")
    try:
      resp = cli.conversations_list(limit=1000, types="public_channel,private_channel")
      for c in resp.get("channels", []):
        if c.get("name") == name:
          return c.get("id")
    except Exception:
      pass
    return ch  # 실패 시 원본 반환

  def _bg(app):
    with app.app_context():
      try:
        fpath, summary = make_settlement_excel(start, end, supply_id=supply_id, out_dir="/tmp")
        cli = ensure_client()

        channel_id = _ensure_channel_id(cli, channel or "")
        initial_comment = (
          f"*정산서 업로드 완료*\n기간: {start} ~ {end}\n"
          f"배송완료 {summary.get('delivered_rows',0)}건 · 취소처리 {summary.get('canceled_rows',0)}건\n"
          f"- *총 상품 결제 금액: {_fmt_currency(summary.get('gross_amount',0))}*\n"
          f"- *배송비: {_fmt_currency(summary.get('shipping_amount',0))}*\n"
          f"- *수수료: {_fmt_currency(summary.get('commission_amount',0))}*\n"
          f"- *총 합계 금액: {_fmt_currency(summary.get('final_amount',0))}*"
        )

        # 1) 파일 업로드
        up = cli.files_upload_v2(
          channel=channel_id,
          file=fpath,
          filename=os.path.basename(fpath),
          title=f"{start} ~ {end} 정산서",
          initial_comment=initial_comment
        )

        # file_id 추출(v2 응답 포맷 가변 대응)
        file_id = None
        if isinstance(up, dict):
          file_id = (up.get("file") or {}).get("id")
          if not file_id:
            files = up.get("files") or []
            if files and isinstance(files, list):
              file_id = files[0].get("id")

        # 2) 채널 타임라인에 파일 메시지가 '실제로' 올라왔는지 확인 → ts 획득
        thread_ts = None
        if file_id:
          thread_ts = _wait_file_message_ts(cli, channel_id, file_id, timeout_sec=20, interval=0.7)

        # 2-보강) 그래도 못 찾으면 살짝 대기 후 진행(가시적 순서 보장용)
        if not thread_ts:
          time.sleep(4)

        # 3) 버튼 블록 구성
        btn_payload = {
          "button_text": "정산확정하기",
          "settlement_id": f"SETTLE-{(start or '')}-{(end or '')}",
          "destination": supply_id,
          "schedule_type": "EXPRESS",
          "payout_date": "",
          "amount_value": int(summary.get("net_amount", 0)),
          "amount_currency": "KRW",
          "transaction_description": "정산"
        }
        blocks = _build_settlement_button_blocks(btn_payload)

        # 4) 파일 메시지 이후에 버튼 메시지 전송(가능하면 스레드로)
        payload = {
          "channel": channel_id,
          "text": "정산 파일 업로드 완료",
          "blocks": blocks
        }
        if thread_ts:
          payload["thread_ts"] = thread_ts

        # rate limit 대비 2회 재시도
        for _ in range(2):
          try:
            cli.chat_postMessage(**payload)
            break
          except SlackApiError as e:
            if _sleep_if_rate_limited(e):
              continue
            _logger.error(f"[button.msg.fail] ch={channel_id} err={getattr(e, 'response', {}).get('data', {})}")
            break

      except Exception:
        traceback.print_exc()

  app_obj = current_app._get_current_object()
  t = threading.Thread(target=_bg, args=(app_obj,), daemon=True)
  t.start()
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
