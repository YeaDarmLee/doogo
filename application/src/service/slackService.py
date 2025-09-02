import os
import re
import time
import logging
from typing import Dict, Any, Optional, List, Set

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from application.src.models.SupplierList import SupplierList

logger = logging.getLogger("slack.provision")

# ========= 환경변수 =========
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")  # xoxb-...
SLACK_CHANNEL_PRIVATE = os.getenv("SLACK_CHANNEL_PRIVATE", "true").lower() in ("1","true","yes")
SLACK_CHANNEL_PREFIX = os.getenv("SLACK_CHANNEL_PREFIX", "vendor-")

# 관리자(초대 대상)
SLACK_ADMIN_USER_IDS: List[str] = [u.strip() for u in os.getenv("SLACK_ADMIN_USER_IDS", "").split(",") if u.strip()]
SLACK_ADMIN_EMAILS:   List[str] = [e.strip() for e in os.getenv("SLACK_ADMIN_EMAILS", "").split(",") if e.strip()]

# 초대 실패 정책: true면 초대 실패 시 전체 실패 처리
SLACK_REQUIRE_ADMIN_INVITE_SUCCESS = os.getenv("SLACK_REQUIRE_ADMIN_INVITE_SUCCESS", "true").lower() in ("1","true","yes")

# 웰컴 메시지 전송 on/off
SLACK_WELCOME_ENABLED = os.getenv("SLACK_WELCOME_ENABLED", "true").lower() in ("1","true","yes")
# 관리자 멘션 포함 여부
SLACK_WELCOME_MENTION_ADMINS = os.getenv("SLACK_WELCOME_MENTION_ADMINS", "true").lower() in ("1","true","yes")
# 웰컴 메시지 템플릿(플레이스홀더: {company}, {supplier_id}, {manager}, {number}, {email})
SLACK_WELCOME_TEMPLATE = os.getenv(
    "SLACK_WELCOME_TEMPLATE",
    ":tada: `{company}` 공급사 지원 채널이 생성되었습니다.\n"
    "ID: `{supplier_id}`\n"
    "담당자: {manager} / 연락처: {number} / 이메일: {email}"
)

if not SLACK_BOT_TOKEN:
    raise RuntimeError("SLACK_BOT_TOKEN 환경변수 설정 필요")

client = WebClient(token=SLACK_BOT_TOKEN)

# ========= 유틸 =========
def _slugify(name: str, fallback: str) -> str:
    s = (name or fallback).lower()
    s = re.sub(r"[^a-z0-9\-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return (s or fallback)[:70]

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

def _send_welcome_message(channel_id: str, supplier: SupplierList):
    """웰컴 메시지 전송 (관리자 멘션 포함 옵션)"""
    if not SLACK_WELCOME_ENABLED:
        logger.info("[welcome-skip] disabled")
        return

    # 텍스트 구성
    def _safe(v): return v if (v and str(v).strip()) else "-"
    text = SLACK_WELCOME_TEMPLATE.format(
        company=_safe(supplier.companyName),
        supplier_id=_safe(supplier.supplierID),
        manager=_safe(supplier.manager),
        number=_safe(supplier.number),
        email=_safe(supplier.email),
    )

    # 관리자 멘션(prefix) 추가
    mention_prefix = ""
    if SLACK_WELCOME_MENTION_ADMINS:
        admin_ids = _collect_admin_user_ids()
        if admin_ids:
            mention_prefix = " ".join(f"<@{uid}>" for uid in admin_ids) + " "
    final_text = mention_prefix + text

    for attempt in range(2):
        try:
            client.chat_postMessage(channel=channel_id, text=final_text)
            logger.info(f"[welcome-ok] ch={channel_id}")
            return
        except SlackApiError as e:
            if _sleep_if_rate_limited(e):
                continue
            logger.error(f"[welcome-fail] ch={channel_id} err={getattr(e, 'response', {}).get('data', {})}")
            return  # 메시지는 강제 실패로 보지 않음

# ========= 메인: 채널 생성 → 관리자 초대 → 웰컴 메시지 =========
def create_slack_channel_only(supplier: SupplierList) -> Dict[str, Any]:
    """
    1) 채널 생성(프라이빗 기본)
    2) 관리자 초대(환경변수 기반)
    3) 웰컴 메시지 전송(옵션)
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

    base = _slugify(supplier.companyName or "", "supplier")
    channel_name = f"{SLACK_CHANNEL_PREFIX}{base}-{supplier.seq}"
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

    # 4) 웰컴 메시지
    _send_welcome_message(channel_id, supplier)

    return {"ok": True, "channel_id": channel_id, "channel_name": channel_name}
