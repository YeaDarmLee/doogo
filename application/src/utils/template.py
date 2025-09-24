# application/src/service/slack_template.py
# -*- coding: utf-8 -*-
"""
Slack 메시지 템플릿 전용 모듈
- 메시지 문구/형식만 책임, 전송은 다른 레이어(slack_utils 등)가 담당
- 모든 시작은 여기서: 템플릿 키 + 파라미터만 넘기면 문자열을 돌려줌
"""

from __future__ import annotations
from typing import Dict

# 필요하면 "ko", "en" 등으로 분기 가능
LOCALE = "ko"

# -----------------------------
# 템플릿 정의 (중복 없이 한 곳)
# -----------------------------
_TEMPLATES_KO: Dict[str, str] = {
  # 채널 생성
  "channel_created": (
    ":loudspeaker: *신규 공급사 채널 개설*\n"
    "- 담당자: {manager}\n"
    "- 연락처: `{number}`\n"
    "- 이메일: `{email}`\n"
    "- 채널: {channel_mention}"
  ),

  # 웰컴
  "welcome": (
    ":tada: `{company}` 공급사 지원 채널이 생성되었습니다.\n"
    "- 아이디: `onedayboxb2b`\n"
    "- 공급사 ID: `{supplier_id}`\n"
    "- 임시 PW: `{supplier_pw}`\n"
    "첫 로그인 시 비밀번호를 재설정해 주세요."
  ),

  # 초대메일 발송
  "mail_sent": (
    ":email: *Slack 가입 유도 메일 발송*\n"
    "- 공급사: {supplier_name}\n"
    "- 대상: `{email}`\n"
    "- 시각: {when}"
  ),
  
  # 초대완료
  "user_joined": (
    ":chains: *채널 초대 완료*\n"
    "- 공급사: {supplier_name}\n"
    "- 대상: {who}\n"
    "- 시각: {when}"
  ),

  # 계약(eformsign)
  "eformsign_sent": (
    ":page_facing_up: *계약서 전송 완료*\n"
    "- 공급사: {supplier_name}\n"
    "- 수신자: `{recipient_email}`\n"
    "- 시각: {when}"
  ),
  "eformsign_failed": (
    ":warning: *계약서 전송 실패*\n"
    "- 공급사: {supplier_name}\n"
    "- 수신자: `{recipient_email}`\n"
    "- 시각: {when}"
    "- 사유: {reason}"
  ),
  "eformsign_success": (
    ":page_with_curl: *계약서 작성 완료*\n"
    "- 공급사: {supplier_name}\n"
    "- 이메일: `{recipient_email}`\n"
    "- 상태: {status}"
  ),

  # 사전 계약(외부제출) 안내
  "skip_notice": (
    ":package: *사전 계약 완료 공급사*\n"
    "- 공급사: {supplier_name}"
  ),

  # 최종 팁 안내
  "created_success_tip": (
    ":tada: `{supplier_name}` 공급사 지원 채널이 생성되었습니다.\n"
    "관리자 링크: https://eclogin.cafe24.com/Shop/?url=Init&login_mode=3\n"
    "아이디: `onedayboxb2b` / 공급사 ID: `{supplier_id}` / PW: `{supplier_pw}`\n"
    "첫 로그인 시 비밀번호 재설정 화면이 나오면 원하시는 비밀번호로 변경해 주세요. :smile:\n"
    "*정상적인 정산을 위해 토스페이먼츠로 전달된 셀러 등록을 위한 본인인증을 완료해주세요!*\n"
    ":round_pushpin: 운영 공지/가이드는 <#C09DBG0UYCS>, <#C09EAJ46Z5J> 채널을 참고해 주세요."
  ),
}

_TEMPLATES = _TEMPLATES_KO  # 현재는 한국어만 사용

# -----------------------------
# 렌더러 (안전한 포맷팅)
# -----------------------------
def render(key: str, **kwargs) -> str:
  """
  템플릿 키와 파라미터로 완성된 메시지 문자열을 반환.
  - 누락 키는 '-'로 대체하여 런타임 KeyError 방지
  - 멘션/채널 표기는 호출부에서 만들어 넣거나 그대로 텍스트 처리
  """
  tpl = _TEMPLATES.get(key)
  if not tpl:
    return f"[template-missing:{key}]"

  # 누락 값 기본 처리
  safe_kwargs = {k: (v if (v is not None and str(v).strip() != '') else '-') for k, v in kwargs.items()}

  # 템플릿에 없는 키가 넘어와도 무시되도록 format_map 패턴 사용
  class _SafeDict(dict):
    def __missing__(self, k):  # 누락된 placeholder는 '-'로
      return '-'

  return tpl.format_map(_SafeDict(safe_kwargs))
