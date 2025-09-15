# application/src/service/slackService.py
import os
import re
import time
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List, Set

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from application.src.models.SupplierList import SupplierList
from application.src.service.mailer import send_email

logger = logging.getLogger("slack.provision")

# ========= 환경변수 =========
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL_PRIVATE = os.getenv("SLACK_CHANNEL_PRIVATE", "true").lower() in ("1","true","yes")
SLACK_CHANNEL_PREFIX = os.getenv("SLACK_CHANNEL_PREFIX", "vendor-")

# 관리자(초대 대상)
SLACK_ADMIN_USER_IDS: List[str] = [u.strip() for u in os.getenv("SLACK_ADMIN_USER_IDS", "").split(",") if u.strip()]
SLACK_ADMIN_EMAILS:   List[str] = [e.strip() for e in os.getenv("SLACK_ADMIN_EMAILS", "").split(",") if e.strip()]

# 초대 실패 정책: true면 초대 실패 시 전체 실패 처리
SLACK_REQUIRE_ADMIN_INVITE_SUCCESS = os.getenv("SLACK_REQUIRE_ADMIN_INVITE_SUCCESS", "true").lower() in ("1","true","yes")

# 웰컴 메시지 전송 on/off
SLACK_WELCOME_ENABLED = os.getenv("SLACK_WELCOME_ENABLED", "true").lower() in ("1","true","yes")
# 관리자 멘션 포함 여부 (현재 템플릿 사용 시 직접 멘션 처리 권장)
SLACK_WELCOME_MENTION_ADMINS = os.getenv("SLACK_WELCOME_MENTION_ADMINS", "true").lower() in ("1","true","yes")
# 웰컴 메시지 템플릿
SLACK_WELCOME_TEMPLATE = os.getenv("SLACK_WELCOME_TEMPLATE")

# 워크스페이스 가입(초대) 링크
SLACK_WORKSPACE_JOIN_URL = os.getenv("SLACK_WORKSPACE_JOIN_URL", "").strip()

# ========= 공통 채널 브로드캐스트 =========
SLACK_BROADCAST_ENABLED = os.getenv("SLACK_BROADCAST_ENABLED", "true").lower() in ("1","true","yes")
SLACK_BROADCAST_CHANNEL_ID = os.getenv("SLACK_BROADCAST_CHANNEL_ID", "").strip()
SLACK_BROADCAST_CHANNEL_NAME = os.getenv("SLACK_BROADCAST_CHANNEL_NAME", "").strip()
SLACK_BROADCAST_TRY_JOIN = os.getenv("SLACK_BROADCAST_TRY_JOIN", "true").lower() in ("1","true","yes")
SLACK_BROADCAST_TEMPLATE = os.getenv("SLACK_BROADCAST_TEMPLATE")

if not SLACK_BOT_TOKEN:
  raise RuntimeError("SLACK_BOT_TOKEN 환경변수 설정 필요")

client = WebClient(token=SLACK_BOT_TOKEN)

# ========= 유틸 =========
def _sleep_if_rate_limited(e: SlackApiError) -> bool:
  try:
    if getattr(e.response, "status_code", None) == 429:
      ra = int(e.response.headers.get("Retry-After", "1"))
      logger.warning(f"[rate-limit] Retry after {ra}s")
      time.sleep(max(1, ra))
      return True
  except Exception:
    pass
  return False

def _slugify_channel_name(name: str) -> str:
  s = (name or "").lower()
  # 한글/영문/숫자 허용 + 공백/특수문자 → 대시로
  # Slack은 영문/숫자/하이픈/언더스코어가 안전. 한글 제거(혹은 변환) 권장.
  s = re.sub(r"[^a-z0-9\-_]+", "-", s)
  s = re.sub(r"-{2,}", "-", s).strip("-")
  return s[:80] or "channel"

# ========= 사용자 조회/초대 =========
def _lookup_user_id_by_email(email: str) -> Optional[str]:
  try:
    resp = client.users_lookupByEmail(email=email)
    return resp.get("user", {}).get("id")
  except SlackApiError as e:
    if _sleep_if_rate_limited(e):
      return _lookup_user_id_by_email(email)  # 1회 재시도
    logger.warning(f"[lookup-fail] email={email} err={getattr(e, 'response', {}).get('data', {})}")
    return None

def _collect_admin_user_ids() -> List[str]:
  ids: Set[str] = set([u for u in SLACK_ADMIN_USER_IDS if u.startswith("U")])
  for email in SLACK_ADMIN_EMAILS:
    uid = _lookup_user_id_by_email(email)
    if uid:
      ids.add(uid)
  return list(ids)

def _invite_admins(channel_id: str):
  admin_ids = _collect_admin_user_ids()
  if not admin_ids:
    logger.info("[invite-skip] admin list empty")
    return

  users_csv = ",".join(admin_ids)
  logger.info(f"[invite-start] ch={channel_id} admins={users_csv}")

  for attempt in range(2):
    try:
      client.conversations_invite(channel=channel_id, users=users_csv)
      logger.info(f"[invite-ok] ch={channel_id}")
      return
    except SlackApiError as e:
      if _sleep_if_rate_limited(e):
        continue
      err = e.response.data.get("error") if getattr(e, "response", None) and e.response.data else str(e)
      if err in ("already_in_channel", "cant_invite_self", "not_in_channel"):
        logger.info(f"[invite-skip] ch={channel_id} err={err}")
        return
      logger.error(f"[invite-fail] ch={channel_id} err={err}")
      if SLACK_REQUIRE_ADMIN_INVITE_SUCCESS:
        raise
      return

# ========= 메시지 전송 =========
def _send_welcome_message(channel_id: str, supplier: SupplierList):
  """(3) 웰컴 메시지 전송"""
  if not SLACK_WELCOME_ENABLED:
    logger.info("[welcome-skip] disabled")
    return

  def _safe(v): return v if (v and str(v).strip()) else "-"
  text = SLACK_WELCOME_TEMPLATE.format(
    company=_safe(supplier.companyName),
    supplier_id=_safe(supplier.supplierID),
    supplier_pw=_safe(supplier.supplierPW)
  )

  for attempt in range(2):
    try:
      # client.chat_postMessage(channel=channel_id, text=text)
      logger.info(f"[welcome-ok] ch={channel_id}")
      return
    except SlackApiError as e:
      if _sleep_if_rate_limited(e):
        continue
      logger.error(f"[welcome-fail] ch={channel_id} err={getattr(e, 'response', {}).get('data', {})}")
      return  # 웰컴 실패는 전체 실패로 보지 않음

# ========= 공통 채널 브로드캐스트 =========
def _resolve_channel_id_by_name(name: str) -> Optional[str]:
  """
  채널 이름으로 ID 조회.
  - 공개 채널은 조회 가능
  - 비공개 채널은 '봇이 멤버인 경우'에만 목록에 나타남
  - 그러니 비공개 공통 채널은 ID를 .env 로 직접 지정하는 게 가장 안전
  """
  cursor = None
  types = "public_channel,private_channel"
  for _ in range(20):  # 최대 20 페이지
    try:
      resp = client.conversations_list(limit=1000, cursor=cursor, types=types)
    except SlackApiError as e:
      if _sleep_if_rate_limited(e):
        continue
      logger.error(f"[list-fail] err={getattr(e, 'response', {}).get('data', {})}")
      return None
    for ch in resp.get("channels", []):
      if ch.get("name") == name:
        return ch.get("id")
    cursor = resp.get("response_metadata", {}).get("next_cursor")
    if not cursor:
      break
  return None

def _channel_info(channel_id: str) -> Optional[Dict]:
  try:
    info = client.conversations_info(channel=channel_id)
    return info.get("channel", {})
  except SlackApiError as e:
    resp = getattr(e, "response", None)
    status = getattr(resp, "status_code", None)
    data = getattr(resp, "data", None)
    err_code = (data.get("error") if isinstance(data, Dict) else None)
    logger.error(
      f"[broadcast-info-fail] ch={channel_id} "
      f"status={status} error={err_code} data={data}"
    )
    return None

def _ensure_can_post(channel_id: str):
  """
  퍼블릭이면 join 시도 가능(channels:join 필요).
  비공개/Slack Connect면 '봇을 초대'가 필요(조인 불가).
  """
  if not SLACK_BROADCAST_TRY_JOIN:
    return

  meta = _channel_info(channel_id)
  if not meta:
    # 정보를 못 구했으면 조심스럽게 조인만 시도
    try:
      client.conversations_join(channel=channel_id)
      logger.info(f"[broadcast-join-ok] ch={channel_id}")
    except SlackApiError as e:
      resp = getattr(e, "response", None)
      status = getattr(resp, "status_code", None)
      data = getattr(resp, "data", None)
      err_code = (data.get("error") if isinstance(data, Dict) else None)
      logger.info(
        f"[broadcast-join-skip] ch={channel_id} "
        f"status={status} error={err_code} data={data}"
      )
    return

  is_private = bool(meta.get("is_private"))
  is_archived = bool(meta.get("is_archived"))
  is_member = bool(meta.get("is_member"))
  kind = "private" if is_private else "public"
  logger.info(f"[broadcast-precheck] ch={channel_id} type={kind} member={is_member} archived={is_archived}")

  if is_archived:
    logger.error(f"[broadcast-abort] ch={channel_id} is archived")
    return

  if is_member:
    return

  if is_private:
    logger.warning(f"[broadcast-need-invite] ch={channel_id} private channel: invite bot first")
    return

  try:
    client.conversations_join(channel=channel_id)
    logger.info(f"[broadcast-join-ok] ch={channel_id}")
  except SlackApiError as e:
    resp = getattr(e, "response", None)
    status = getattr(resp, "status_code", None)
    data = getattr(resp, "data", None)
    err_code = (data.get("error") if isinstance(data, Dict) else None)
    logger.info(
      f"[broadcast-join-skip] ch={channel_id} "
      f"status={status} error={err_code} data={data}"
    )

def _send_broadcast_to_common_channel(created_channel_id: str, created_channel_name: str, supplier: SupplierList):
  """
  (4) 공통 채널에 알림 메시지 전송
  - 대상 채널 ID가 .env에 있으면 바로 사용
  - 없으면 이름으로 탐색 후 ID 해석
  - 실패해도 전체 플로우 중단하지 않음
  """
  if not SLACK_BROADCAST_ENABLED:
    logger.info("[broadcast-skip] disabled")
    return

  target_id = SLACK_BROADCAST_CHANNEL_ID
  if not target_id and SLACK_BROADCAST_CHANNEL_NAME:
    target_id = _resolve_channel_id_by_name(SLACK_BROADCAST_CHANNEL_NAME)

  if not target_id:
    logger.warning("[broadcast-skip] 대상 채널 ID/이름 설정 없음 또는 해석 실패")
    return

  _ensure_can_post(target_id)

  def _safe(v): return v if (v and str(v).strip()) else "-"
  text = SLACK_BROADCAST_TEMPLATE.format(
    manager=_safe(supplier.manager),
    number=_safe(supplier.number),
    email=_safe(supplier.email),
    channel_mention=f"<#{created_channel_id}>",
  )

  for attempt in range(2):
    try:
      client.chat_postMessage(channel=target_id, text=text)
      client.chat_postMessage(channel=created_channel_id, text=text)
      logger.info(f"[broadcast-ok] target={target_id}")
      return
    except SlackApiError as e:
      resp = getattr(e, "response", None)
      status = getattr(resp, "status_code", None)
      data = getattr(resp, "data", None)
      err_code = (data.get("error") if isinstance(data, Dict) else None)
      logger.error(
        f"[broadcast-fail] target={target_id} "
        f"status={status} error={err_code} data={data}"
      )
      if _sleep_if_rate_limited(e):
        continue
      return

# ========= 메인: 채널 생성 → 관리자 초대 → 웰컴 → 공통 채널 방송 =========
def create_slack_channel_only(supplier: SupplierList) -> Dict[str, Any]:
  """
  1) 채널 생성(프라이빗 기본)
  2) 관리자 초대(환경변수 기반)
  3) 웰컴 메시지 전송(옵션)
  4) 공통 채널 메시지 전송(옵션)
  """
  # 이미 채널이 있으면 스킵
  slack_channel_id: Optional[str] = getattr(supplier, "slackChannelId", None)
  if slack_channel_id:
    logger.info(f"[skip-existing] seq={supplier.seq} ch={slack_channel_id}")
    return {"ok": True, "channel_id": slack_channel_id, "channel_name": None, "skipped": "already_provisioned"}

  # 토큰 워크스페이스 정보
  try:
    auth = client.auth_test()
    logger.info(f"[auth] team={auth.get('team')} team_id={auth.get('team_id')} bot_user={auth.get('user_id')}")
  except SlackApiError as e:
    logger.error(f"[auth-fail] {e.response.data if e.response else e}")
    raise

  # 채널명 정규화(공백/특수문자 제거)
  raw_name = f"{SLACK_CHANNEL_PREFIX}{supplier.companyName or ''}"
  channel_name = _slugify_channel_name(raw_name)
  logger.info(f"[create-start] seq={supplier.seq} name={channel_name} private={SLACK_CHANNEL_PRIVATE}")

  # 1) 채널 생성
  for attempt in range(2):
    try:
      resp = client.conversations_create(name=channel_name, is_private=SLACK_CHANNEL_PRIVATE)
      channel = resp["channel"]
      channel_id = channel["id"]
      logger.info(f"[create-ok] seq={supplier.seq} id={channel_id} name={channel.get('name')} is_private={channel.get('is_private')}")
      break
    except SlackApiError as e:
      if _sleep_if_rate_limited(e):
        continue
      err = e.response.data.get("error") if getattr(e, "response", None) and e.response.data else str(e)
      logger.error(f"[create-fail] seq={supplier.seq} name={channel_name} err={err}")
      if err == "name_taken":
        channel_name = f"{channel_name}-{int(time.time())}"
        logger.warning(f"[retry-name] new={channel_name}")
        continue
      raise
  else:
    raise RuntimeError("conversations.create 실패(재시도 초과)")

  # 2) 조인 보장(대부분 자동 멤버지만 안전하게 시도)
  try:
    client.conversations_join(channel=channel_id)
    logger.info(f"[join-ok] id={channel_id}")
  except SlackApiError as e:
    logger.info(f"[join-skip] id={channel_id} err={getattr(e, 'response', {}).get('data', {})}")

  # 3) 관리자 초대
  _invite_admins(channel_id)

  # 4-1) 웰컴 메시지(생성된 채널)
  # _send_welcome_message(channel_id, supplier)

  # 4-2) 공통 채널 브로드캐스트
  _send_broadcast_to_common_channel(channel_id, channel.get("name") or channel_name, supplier)

  return {"ok": True, "channel_id": channel_id, "channel_name": channel_name}

# ========= 단일 메시지 전송(공용) =========
def post_message_to_channel(channel_id: str, text: str, thread_ts: Optional[str] = None) -> Dict[str, Any]:
  """
  지정 채널로 텍스트 메시지 전송
  - 레이트리밋(429) 자동 재시도 1회
  - 실패 시 예외는 던지지 않고 Dict 로 반환(호출부에서 판단)
  """
  if not channel_id or not text:
    logger.error("[post-msg] invalid args channel_id/text")
    return {"ok": False, "error": "invalid_args"}

  for attempt in range(2):
    try:
      resp = client.chat_postMessage(
        channel=channel_id,
        text=text,
        **({"thread_ts": thread_ts} if thread_ts else {})
      )
      logger.info(f"[post-msg-ok] ch={channel_id}")
      return {"ok": True, "data": resp.data}
    except SlackApiError as e:
      if _sleep_if_rate_limited(e):
        continue
      data = getattr(e, "response", None)
      data = getattr(data, "data", None) if data else None
      logger.error(f"[post-msg-fail] ch={channel_id} err={data}")
      return {"ok": False, "error": (data.get('error') if isinstance(data, Dict) else str(e))}

# ========= (신규) 초대 메일 발송 알림 유틸 =========
def _post_text(channel: str, text: str) -> bool:
  try:
    if not channel:
      return False
    client.chat_postMessage(channel=channel, text=text)
    return True
  except Exception as e:
    logger.error(f"[slack.notify] post fail ch={channel} err={e}")
    return False

def notify_invite_mail_sent(email: str, supplier_name: str = "", supplier_channel_id: Optional[str] = None):
  """
  초대(가입 유도) 메일을 발송했다는 사실을 슬랙에 알림.
  - 브로드캐스트 채널 (환경변수)
  - 공급사 채널(있으면)
  """
  when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  sname = supplier_name or "공급사"
  text = (
    f":email: *Slack 가입 유도 메일 발송* / 공급사: {sname} / 대상: `{email}` / 시각: {when}"
  )
  # 1) 브로드캐스트 채널
  _post_text(SLACK_BROADCAST_CHANNEL_ID, text)
  # # 2) 공급사 전용 채널(있으면)
  if supplier_channel_id:
    _post_text(supplier_channel_id, text)

# ========= 가입 유도 메일 발송 =========
def send_workspace_join_invite_email(to_email: str, supplier_name: str) -> bool:
  """
  Enterprise/Business+가 아닐 때: 가입을 요청하는 안내 메일 발송.
  가입 완료 후 배치에서 lookupByEmail → conversations.invite 로 채널 초대.
  """
  to = (to_email or "").strip()
  if not (to and SLACK_WORKSPACE_JOIN_URL):
    print(f"[invite-mail-skip] to={to} join_url={SLACK_WORKSPACE_JOIN_URL}")
    return False

  subj = f"[{supplier_name or '공급사'}] Slack 채널 초대를 위한 가입 안내"
  text = (
    f"안녕하세요, {supplier_name or '공급사 담당자'}님.\n\n"
    f"Slack 채널 초대를 위해 먼저 워크스페이스 가입이 필요합니다.\n"
    f"아래 링크로 가입을 완료해 주세요.\n\n"
    f"가입 링크: {SLACK_WORKSPACE_JOIN_URL}\n\n"
    f"가입이 완료되면 시스템이 자동으로 전용 채널로 초대합니다.\n"
    f"감사합니다."
  )
  html = f"""
  <p>안녕하세요, <b>{supplier_name or '공급사 담당자'}</b>님.</p>
  <p>Slack 채널 초대를 위해 먼저 워크스페이스 가입이 필요합니다.<br/>
  아래 링크로 가입을 완료해 주세요.</p>
  <p><a href="{SLACK_WORKSPACE_JOIN_URL}">워크스페이스 가입하기</a></p>
  <p>가입이 완료되면 시스템이 자동으로 전용 채널로 초대합니다.</p>
  <p>감사합니다.</p>
  """
  return send_email(to, subj, text, html)

def notify_user_invited_to_channel(
  email: str,
  user_id: Optional[str],
  supplier_name: str = "",
  supplier_channel_id: Optional[str] = None
):
  """
  가입 완료된 사용자를 전용 채널에 초대(또는 이미 멤버)했음을 슬랙으로 공지.
  - 브로드캐스트 채널
  - 공급사 채널(있으면)
  """
  when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  sname = supplier_name or "공급사"
  who = f"<@{user_id}>" if user_id else f"`{email}`"
  text = (
    f":chains: *채널 초대 완료* / 공급사: {sname} / 대상: {who} / 시각: {when}"
  )
  _post_text(SLACK_BROADCAST_CHANNEL_ID, text)
  if supplier_channel_id:
    _post_text(supplier_channel_id, text)

def notify_contract_sent(
  recipient_email: str,
  supplier_name: str = "",
  document_id: Optional[str] = None,
  supplier_channel_id: Optional[str] = None
):
  """
  eformsign 계약서 '전송 성공' 알림.
  - 브로드캐스트 채널
  - 공급사 전용 채널(있으면)
  """
  when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  sname = supplier_name or "공급사"
  text = (
    f":page_facing_up: *계약서 전송 완료* / 공급사: {sname} / 수신자: `{recipient_email}` / 시각: {when}"
  )
  _post_text(SLACK_BROADCAST_CHANNEL_ID, text)
  if supplier_channel_id:
    _post_text(supplier_channel_id, text)


def notify_contract_failed(
  recipient_email: str,
  supplier_name: str = "",
  reason: str = "",
  supplier_channel_id: Optional[str] = None
):
  """
  eformsign 계약서 '전송 실패' 알림.
  - 브로드캐스트 채널
  - 공급사 전용 채널(있으면)
  """
  when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  sname = supplier_name or "공급사"
  why = f"\n- 사유: {reason}" if reason else ""
  text = (
    f":warning: *계약서 전송 실패*\n"
    f"- 수신자: `{recipient_email}`\n"
    f"- 공급사: {sname}\n"
    f"- 시각: {when}{why}"
  )
  _post_text(SLACK_BROADCAST_CHANNEL_ID, text)
  # if supplier_channel_id:
  #   _post_text(supplier_channel_id, text)
