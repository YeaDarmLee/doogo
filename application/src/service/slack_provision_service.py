# application/src/service/slackService.py
# -*- coding: utf-8 -*-
"""
Slack 서비스 레이어 (오케스트레이션 전담)

핵심
- 단건 API 호출(채널/메시지/사용자/업로드)은 전부 slack_service 로 위임
- 이 파일은 '업무 플로우'만 조립: 채널 생성 → 관리자 초대 → 웰컴 → 브로드캐스트
"""

import os
import re
import time
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List, Set

from application.src.models.SupplierList import SupplierList
from application.src.service.email_service import send_email
from application.src.utils import template as TEMPLATE
from application.src.service import slack_service as SU

logger = logging.getLogger("slack.provision")

# ========= 환경변수 =========
SLACK_CHANNEL_PRIVATE = os.getenv("SLACK_CHANNEL_PRIVATE", "true").lower() in ("1","true","yes")
SLACK_CHANNEL_PREFIX = os.getenv("SLACK_CHANNEL_PREFIX", "vendor-")

# 관리자(초대 대상) — U아이디/이메일 혼용 허용
SLACK_ADMIN_USER_IDS: List[str] = [u.strip() for u in os.getenv("SLACK_ADMIN_USER_IDS", "").split(",") if u.strip()]
SLACK_ADMIN_EMAILS:   List[str] = [e.strip() for e in os.getenv("SLACK_ADMIN_EMAILS", "").split(",") if e.strip()]

# 워크스페이스 가입(초대) 링크
SLACK_WORKSPACE_JOIN_URL = os.getenv("SLACK_WORKSPACE_JOIN_URL", "").strip()

# ========= 공통 채널 브로드캐스트 =========
SLACK_BROADCAST_CHANNEL_ID = os.getenv("SLACK_BROADCAST_CHANNEL_ID", "").strip()
SLACK_BROADCAST_CHANNEL_NAME = os.getenv("SLACK_BROADCAST_CHANNEL_NAME", "").strip()

# WebClient
client = SU.ensure_client()


# ============================================================================
# 내부: 채널명 슬러그
# ============================================================================
def _slugify_channel_name(name: str) -> str:
  """
  Slack 채널명 규칙에 맞춰 슬러그 생성
  - 공백→'_' / 특수문자 제거 / 영문 소문자 / 한글 허용 / 최대 80자
  """
  s = (name or "").strip().lower()
  s = re.sub(r"\s+", "_", s)                     # 공백 → '_'
  s = re.sub(r"[^a-z0-9가-힣\-_]", "", s)          # 허용 문자만
  s = re.sub(r"_{2,}", "_", s)                    # 연속 '_' 정리
  s = re.sub(r"-{2,}", "-", s)                    # 연속 '-' 정리
  s = s.strip("_-")
  return s[:80] or "channel"


# ============================================================================
# 내부: 관리자 초대(유틸 위임 사용)
# ============================================================================
def _collect_admin_user_ids() -> List[str]:
  """
  관리자로 초대할 사용자 집합 수집
  - 이미 'U***'인 값은 그대로
  - 이메일은 lookup으로 U아이디 변환 (실패시 제외)
  """
  ids: Set[str] = set([u for u in SLACK_ADMIN_USER_IDS if u.startswith("U")])
  for email in SLACK_ADMIN_EMAILS:
    uid = SU.lookup_user_id_by_email(email)  # 실패 시 None
    if uid:
      ids.add(uid)
  return list(ids)


def _invite_admins(channel_id: str) -> None:
  """
  채널에 관리자(들) 초대
  - 이미 멤버/already_in_channel 등은 slack_service.invite_user에서 True 처리
  """
  admin_ids = _collect_admin_user_ids()
  if not admin_ids:
    logger.info("[invite-skip] admin list empty")
    return

  for uid in admin_ids:
    ok = SU.invite_user(channel_id, uid)
    if not ok:
      logger.error(f"[invite-fail] ch={channel_id} user={uid}")
  logger.info(f"[invite-ok] ch={channel_id} admins={','.join(admin_ids)}")


# ============================================================================
# 내부: 웰컴/브로드캐스트
# ============================================================================
def _send_welcome_message(channel_id: str, supplier: SupplierList) -> None:
  """
  신규 채널에 웰컴 메시지 전송
  - 실패해도 플로우 중단하지 않음
  """
  def _safe(v): return v if (v and str(v).strip()) else "-"
  template_msg = TEMPLATE.render(
    "welcome",
    company=_safe(supplier.companyName),
    supplier_id=_safe(supplier.supplierID),
    supplier_pw=_safe(supplier.supplierPW)
  )

  if SU.post_text(channel_id, template_msg):
    logger.info(f"[welcome-ok] ch={channel_id}")
  else:
    logger.error(f"[welcome-fail] ch={channel_id}")


def _resolve_broadcast_channel_id() -> Optional[str]:
  """
  브로드캐스트 대상 채널 ID 결정
  - 환경변수 ID 우선, 없으면 이름→ID 해석
  """
  if SLACK_BROADCAST_CHANNEL_ID:
    return SLACK_BROADCAST_CHANNEL_ID
  if SLACK_BROADCAST_CHANNEL_NAME:
    return SU.resolve_channel_id_by_name(SLACK_BROADCAST_CHANNEL_NAME)
  return None


def _send_broadcast_to_common_channel(created_channel_id: str, created_channel_name: str, supplier: SupplierList) -> None:
  """
  공통 채널에 신규 공급사 채널 생성 알림을 방송(옵션)
  - 실패해도 플로우 중단하지 않음
  """
  target_id = _resolve_broadcast_channel_id()
  if not target_id:
    logger.warning("[broadcast-skip] 대상 채널 ID/이름 설정 없음 또는 해석 실패")
    return

  def _safe(v): return v if (v and str(v).strip()) else "-"
  template_msg = TEMPLATE.render(
    "channel_created",
    manager=_safe(supplier.manager),
    number=_safe(supplier.number),
    email=_safe(supplier.email),
    channel_mention=f"<#{created_channel_id}>",
  )

  ok1 = SU.post_text(target_id, template_msg)
  ok2 = SU.post_text(created_channel_id, template_msg)
  if ok1 and ok2:
    logger.info(f"[broadcast-ok] target={target_id}")
  else:
    logger.error(f"[broadcast-fail] target={target_id}, created={created_channel_id}")


# ============================================================================
# 메인 오케스트레이션
#   채널 생성 → (관리자 초대) → 웰컴 → 공통 채널 방송
# ============================================================================
def create_slack_channel(supplier: SupplierList) -> Dict[str, Any]:
  """
  공급사 전용 Slack 채널을 생성하고 초기화
  1) 채널 생성(프라이빗 기본)
  2) 관리자 초대(환경변수 기반 U아이디/이메일)
  3) 웰컴 메시지(옵션)
  4) 공통 채널 방송(옵션)
  """
  # 0) 이미 채널이 있으면 스킵(하위 호환 필드명 유지)
  slack_channel_id: Optional[str] = getattr(supplier, "slackChannelId", None)
  if slack_channel_id:
    logger.info(f"[skip-existing] seq={supplier.seq} ch={slack_channel_id}")
    return {"ok": True, "channel_id": slack_channel_id, "channel_name": None, "skipped": "already_provisioned"}

  # 1) 채널명 슬러그화
  raw_name = f"{SLACK_CHANNEL_PREFIX}{supplier.companyName or ''}"
  channel_name = _slugify_channel_name(raw_name)
  logger.info(f"[create-start] seq={supplier.seq} name={channel_name} private={SLACK_CHANNEL_PRIVATE}")

  # 2) 채널 생성 (이름 충돌 시 타임스탬프 부가 1회 재시도)
  ch_id = SU.create_channel(channel_name, private=SLACK_CHANNEL_PRIVATE, ensure_join=True)
  if not ch_id:
    retry_name = f"{channel_name}-{int(time.time())}"
    logger.warning(f"[retry-name] new={retry_name}")
    ch_id = SU.create_channel(retry_name, private=SLACK_CHANNEL_PRIVATE, ensure_join=True)
    channel_name = retry_name if ch_id else channel_name
  if not ch_id:
    raise RuntimeError("conversations.create 실패(재시도 포함)")

  # 3) 관리자 초대
  _invite_admins(ch_id)

  # 4) 웰컴 메시지(옵션)
  _send_welcome_message(ch_id, supplier)

  # 5) 공통 채널 방송(옵션)
  _send_broadcast_to_common_channel(ch_id, channel_name, supplier)

  return {"ok": True, "channel_id": ch_id, "channel_name": channel_name}


# ============================================================================
# (옵션) 각종 알림 유틸
# ============================================================================
def notify_invite_mail_sent(email: str, supplier_name: str = "", supplier_channel_id: Optional[str] = None) -> None:
  """
  초대(가입 유도) 메일 발송 알림
  - 브로드캐스트 채널
  - 공급사 채널(있으면)
  """
  when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  
  template_msg = TEMPLATE.render(
    "mail_sent",
    supplier_name=supplier_name,
    email=email,
    when=when
  )
  if SLACK_BROADCAST_CHANNEL_ID:
    SU.post_text(SLACK_BROADCAST_CHANNEL_ID, template_msg)
  if supplier_channel_id:
    SU.post_text(supplier_channel_id, template_msg)


def send_workspace_join_invite_email(to_email: str, supplier_name: str) -> bool:
  """
  (엔터프라이즈 미사용 시) 가입 안내 메일 발송
  - 가입 완료 후 배치가 lookupByEmail → conversations.invite 수행
  """
  to = (to_email or "").strip()
  if not (to and SLACK_WORKSPACE_JOIN_URL):
    logger.info(f"[invite-mail-skip] to={to} join_url={SLACK_WORKSPACE_JOIN_URL}")
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
) -> None:
  """
  가입 완료된 사용자를 전용 채널에 초대(또는 이미 멤버)했음을 공지
  - 브로드캐스트 채널
  - 공급사 채널(있으면)
  """
  when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  who = f"<@{user_id}>" if user_id else f"`{email}`"
  
  template_msg = TEMPLATE.render(
    "user_joined",
    supplier_name=supplier_name,
    who=who,
    when=when
  )
  if SLACK_BROADCAST_CHANNEL_ID:
    SU.post_text(SLACK_BROADCAST_CHANNEL_ID, template_msg)
  if supplier_channel_id:
    SU.post_text(supplier_channel_id, template_msg)


def notify_contract_sent(
  recipient_email: str,
  supplier_name: str = "",
  document_id: Optional[str] = None,
  supplier_channel_id: Optional[str] = None
) -> None:
  """
  eformsign 계약서 '전송 성공' 알림
  - 브로드캐스트 채널
  - 공급사 전용 채널(있으면)
  """
  when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  
  template_msg = TEMPLATE.render(
    "eformsign_sent",
    supplier_name=supplier_name,
    recipient_email=recipient_email,
    when=when
  )
  
  if SLACK_BROADCAST_CHANNEL_ID:
    SU.post_text(SLACK_BROADCAST_CHANNEL_ID, template_msg)
  if supplier_channel_id:
    SU.post_text(supplier_channel_id, template_msg)


def notify_contract_failed(
  recipient_email: str,
  supplier_name: str = "",
  reason: str = "",
  supplier_channel_id: Optional[str] = None
) -> None:
  """
  eformsign 계약서 '전송 실패' 알림
  - 브로드캐스트 채널
  - 공급사 전용 채널(있으면)
  """
  when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  
  template_msg = TEMPLATE.render(
    "eformsign_failed",
    supplier_name=supplier_name,
    recipient_email=recipient_email,
    when=when,
    reason=reason,
  )
  if SLACK_BROADCAST_CHANNEL_ID:
    SU.post_text(SLACK_BROADCAST_CHANNEL_ID, template_msg)
  if supplier_channel_id:
    SU.post_text(supplier_channel_id, template_msg)
