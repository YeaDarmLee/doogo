# application/jobs/supplierJobs.py
# -*- coding: utf-8 -*-
"""
공급사 배치 처리(Job):
- Slack 채널 생성/초대/알림 등은 서비스/유틸로 위임하고,
  이 파일은 '처리 순서와 상태 전환'에만 집중한다.
"""

import os
from datetime import datetime
from typing import Dict, Optional

from sqlalchemy import text, select, and_, or_
from sqlalchemy.exc import SQLAlchemyError

from application.src.models import db
from application.src.models.SupplierList import SupplierList

# 오케스트레이션(채널 생성/알림)은 서비스 레이어 사용
from application.src.service.slack_provision_service import (
  create_slack_channel,  # 기존 호출부 호환
  send_workspace_join_invite_email,
  notify_invite_mail_sent,
  notify_user_invited_to_channel,
  notify_contract_sent,
  notify_contract_failed
)

from application.src.utils import template as TEMPLATE
from application.src.service import slack_service as SU

SLACK_BROADCAST_CHANNEL_ID = os.getenv("SLACK_BROADCAST_CHANNEL_ID", "").strip()

# stateCode semantics:
#   None : 미처리
#   R    : 승인 대기중
#   P    : 작업중(락)
#   A    : 준비 완료(채널 생성 성공 등)
#   E    : 오류
#   I    : 초대 메일 발송됨(가입 대기 상태)


def _acquire_lock(lock_key: str) -> bool:
  got = db.session.execute(text("SELECT GET_LOCK(:k, 0)"), {"k": lock_key}).scalar()
  return bool(got)


def _release_lock(lock_key: str):
  try:
    db.session.execute(text("SELECT RELEASE_LOCK(:k)"), {"k": lock_key})
  except Exception:
    pass


def _lookup_user_id_by_email(email: str) -> Optional[str]:
  """
  이메일 → Slack 사용자 ID 조회.
  - 내부 WebClient 사용/예외 처리/레이트리밋 처리는 slack_service 가 담당.
  """
  return SU.lookup_user_id_by_email(email)


def process_pending_suppliers(batch_size: int = 10, lock_key: str = "job_supplier_slack"):
  """
  대상:
    - None && (채널 생성 필요 or 계약 필요)
    - I(가입 대기) : 가입 확인 재시도만 수행 (이때 lookup 실패면 '메일 재발송 금지')

  흐름:
    1) 대상 조회 → prev_state 맵 확보 → 모두 P로 마킹
    2) 각 항목 처리
       - need_slack → 채널 생성
       - lookupByEmail
         * uid 없고 prev_state == 'I' → 메일 보내지 않고 I로 되돌림(종료)
         * uid 없고 prev_state != 'I' → 메일 1회 발송 후 I로 전환(종료)
         * uid 있으면 → 채널 초대
       - need_contract → 계약 진행
       - 마무리 상태 정리
  """
  if not _acquire_lock(lock_key):
    return

  try:
    base_target = or_(
      SupplierList.stateCode.is_(None),  # STATE_CODE IS NULL
      and_(
        SupplierList.stateCode == "RA",
        SupplierList.stateCode.isnot(None),
        SupplierList.supplierID.isnot(None),
        SupplierList.supplierPW.isnot(None),
      ),
      SupplierList.stateCode == "I",     # STATE_CODE = 'I'
    )

    rows = (
      db.session.execute(
        select(SupplierList)
        .where(base_target)
        .order_by(SupplierList.seq.asc())
        .limit(batch_size)
      ).scalars().all()
    )

    if not rows:
      return

    # 1) prev_state 기억 후 모두 P로 마킹 (I는 보류 상태 유지)
    prev_state_map: Dict[int, Optional[str]] = {r.seq: r.stateCode for r in rows}
    for r in rows:
      if r.stateCode != "I":
        r.stateCode = "P"
    db.session.commit()

    # 2) 처리 루프
    for s in rows:
      prev_state = prev_state_map.get(s.seq)
      try:
        email = (s.email or "").strip()

        need_slack = (not s.channelId) and bool(s.supplierCode) and bool(s.companyName) and bool(email)
        need_contract = bool(s.channelId) and ((s.contractStatus is None) or (s.contractStatus == "E")) and bool(email)

        # 2-1) 채널 생성(오케스트레이션 위임)
        if need_slack:
          res = create_slack_channel(s)
          channel_id = (res or {}).get("channel_id")
          name = (res or {}).get("channel_name") or (res or {}).get("name")
          reused = (res or {}).get("reused")
          renamed = (res or {}).get("renamed")

          if not channel_id:
            s.stateCode = "E"
            db.session.commit()
            print(f"[{datetime.now()}] Slack 처리 실패(채널ID 없음) seq={s.seq} company={s.companyName} res={res}")
            continue

          s.channelId = channel_id
          s.stateCode = "A"
          db.session.commit()
          print(f"[{datetime.now()}] Slack 처리 성공 seq={s.seq} name={name} reused={reused} renamed={renamed} channel_id={channel_id}")
          need_contract = True

        # 2-2) 가입 확인/초대
        if email:
          uid = _lookup_user_id_by_email(email)

          # (A) 미가입 & 이전 상태가 'I' → 메일 재발송 금지, I 유지
          if not uid and prev_state == "I":
            s.stateCode = "I"
            db.session.commit()
            print(f"[{datetime.now()}] 가입 대기 유지(seq={s.seq}) email={email} (재발송 금지)")
            continue

          # (B) 미가입 & 최초 → 가입 유도 메일 1회 발송 후 I 전환
          if not uid and prev_state != "I":
            sent = send_workspace_join_invite_email(email, s.companyName)
            if sent:
              notify_invite_mail_sent(email=email, supplier_name=s.companyName, supplier_channel_id=s.channelId)
            note = "발송됨" if sent else "발송실패/스킵"
            s.stateCode = "I"
            db.session.commit()
            print(f"[{datetime.now()}] 가입 유도 메일 {note} seq={s.seq} email={email} → state='I'")
            continue

          # (C) 가입됨 → 채널 초대 (slack_service.invite_user 사용)
          if uid and s.channelId:
            invited_ok = SU.invite_user(s.channelId, uid)
            if invited_ok:
              print(f"[{datetime.now()}] 채널 초대 성공 ch={s.channelId} email={email} uid={uid}")
              s.stateCode = "A"
              db.session.commit()
              # 성공/이미 멤버 모두 알림
              notify_user_invited_to_channel(
                email=email,
                user_id=uid,
                supplier_name=s.companyName,
                supplier_channel_id=s.channelId,
              )
            else:
              print(f"[{datetime.now()}] 채널 초대 실패 ch={s.channelId} email={email}")

        # 2-3) 계약서 전송
        if need_contract:
          _after_slack_success(s)

        # 2-4) 마무리 상태 정리
        if s.stateCode == "P":
          s.stateCode = "A"
          db.session.commit()

      except Exception as e:
        s.stateCode = "E"
        try:
          db.session.commit()
        except Exception:
          db.session.rollback()
        print(f"[{datetime.now()}] Slack/계약 처리 실패 seq={s.seq} company={s.companyName} err={e}")

  except SQLAlchemyError as e:
    db.session.rollback()
    print("DB error during job:", e)
  except Exception as e:
    db.session.rollback()
    print("Unexpected error during job:", e)
  finally:
    _release_lock(lock_key)


def _after_slack_success(supplier: SupplierList):
  """
  Slack 생성 이후 전자서명 발송:
  - contractSkip=1 이면 스킵(S) 처리
  - contractTemplate='A' → 단일 수수료(%)
  - contractTemplate='B' → 임계금액 + 이하/초과 수수료(%)
  - 이메일 없으면 오류(E)
  - 토큰/발송 성공 시 대기(P) → 완료(A)
  """
  # 0) 전처리: eformsign 모듈 체크
  try:
    from application.src.service.eformsign_service import EformsignService, EformsignError
  except Exception as e:
    supplier.contractStatus = "E"
    try:
      db.session.commit()
    except Exception:
      db.session.rollback()
    print(f"[{datetime.now()}] eformsign 모듈 임포트 실패 seq={supplier.seq} err={e}")
    return

  # 1) 스킵 플래그(외부 제출) → 발송 생략
  if getattr(supplier, "contractSkip", False):
    supplier.contractStatus = "S"  # Skipped(외부제출)
    try:
      db.session.commit()
    except Exception:
      db.session.rollback()
    print(f"[{datetime.now()}] eformsign 전송 스킵(외부제출) seq={supplier.seq}")

    # 알림: 스킵 통지
    template_msg = TEMPLATE.render(
      "skip_notice",
      supplier_name=supplier.companyName,
    )
    SU.post_text(supplier.channelId, template_msg)
    SU.post_text(SLACK_BROADCAST_CHANNEL_ID, template_msg)

    template_msg = TEMPLATE.render(
      "created_success_tip",
      supplier_name=supplier.companyName,
      supplier_id=supplier.supplierID,
      supplier_pw=supplier.supplierPW,
    )
    SU.post_text(supplier.channelId, template_msg)
    return

  # 2) 수신 이메일 검사
  recipient_email = (supplier.email or "").strip()
  if not recipient_email:
    supplier.contractStatus = "E"
    try:
      db.session.commit()
    except Exception:
      db.session.rollback()
    print(f"[{datetime.now()}] eformsign 전송 스킵(이메일 없음) seq={supplier.seq}")
    notify_contract_failed(
      recipient_email or "-",
      supplier_name=supplier.companyName,
      reason="이메일 없음",
      supplier_channel_id=supplier.channelId,
    )
    return

  # 3) 계약 템플릿/필드 구성
  t = (getattr(supplier, "contractTemplate", "") or "").upper()
  fields = []
  template_id = None

  if t == "A":
    pct = supplier.contractPercent
    try:
      ok = (pct is not None and 0 <= float(pct) <= 100)
    except Exception:
      ok = False
    if not ok:
      supplier.contractStatus = "E"
      try:
        db.session.commit()
      except Exception:
        db.session.rollback()
      notify_contract_failed(
        recipient_email=recipient_email,
        supplier_name=supplier.companyName,
        reason="계약서 A: 수수료(%) 값이 유효하지 않습니다.",
        supplier_channel_id=supplier.channelId,
      )
      return

    fields = [
      {"id": "수수료", "value": f"수수료 {pct}% 를"}
    ]
    template_id = os.getenv("EFORMSIGN_TEMPLATE_ID_A")

  elif t == "B":
    th = supplier.contractThreshold
    pu = supplier.contractPercentUnder
    po = supplier.contractPercentOver
    ok = True
    try:
      ok = (th is not None and int(th) >= 0 and
            pu is not None and 0 <= float(pu) <= 100 and
            po is not None and 0 <= float(po) <= 100)
    except Exception:
      ok = False
    if not ok:
      supplier.contractStatus = "E"
      try:
        db.session.commit()
      except Exception:
        db.session.rollback()
      notify_contract_failed(
        recipient_email=recipient_email,
        supplier_name=supplier.companyName,
        reason="계약서 B: 임계금액/수수료(%) 값이 유효하지 않습니다.",
        supplier_channel_id=supplier.channelId,
      )
      return

    # 실제 템플릿 필드 키는 eformsign 설정에 맞게 조정 필요
    # fields = [
    #   {"name": "threshold", "value": str(int(th))},
    #   {"name": "percent_under", "value": str(pu)},
    #   {"name": "percent_over", "value": str(po)},
    # ]
    template_id = os.getenv("EFORMSIGN_TEMPLATE_ID_B")

  else:
    supplier.contractStatus = "E"
    try:
      db.session.commit()
    except Exception:
      db.session.rollback()
    notify_contract_failed(
      recipient_email=recipient_email,
      supplier_name=supplier.companyName,
      reason="계약서 템플릿이 선택되지 않았습니다.",
      supplier_channel_id=supplier.channelId,
    )
    return

  if not template_id:
    supplier.contractStatus = "E"
    try:
      db.session.commit()
    except Exception:
      db.session.rollback()
    notify_contract_failed(
      recipient_email=recipient_email,
      supplier_name=supplier.companyName,
      reason="EFORMSIGN 템플릿 ID가 설정되지 않았습니다.",
      supplier_channel_id=supplier.channelId,
    )
    return

  # 4) 전송대기(P) 저장
  try:
    supplier.contractStatus = "P"
    db.session.commit()
  except Exception:
    db.session.rollback()
    print(f"[{datetime.now()}] 계약 상태(P) 저장 실패 seq={supplier.seq}")

  # 5) 토큰 발급 → 문서 생성/전송
  try:
    svc = EformsignService()
    tr = svc.issue_access_token()
    print(
      f"[{datetime.now()}] eformsign 토큰 발급 성공 "
      f"seq={supplier.seq} company={supplier.companyName} api_url={tr.api_url} expires_in={tr.expires_in}"
    )

    recipient_name = (supplier.companyName or "공급사 담당자").strip()

    # 실제 템플릿에 맞춰 필드/옵션 구성 필요
    doc = svc.create_document_from_template(
      token=tr,
      template_id=template_id,
      recipient_email=recipient_email,
      recipient_name=recipient_name,
      fields=fields,
    )
    
    print(f"[{datetime.now()}] eformsign 문서 생성 성공 seq={supplier.seq} doc_id={doc['document_id']}")

    notify_contract_sent(
      recipient_email=recipient_email,
      supplier_name=supplier.companyName,
      supplier_channel_id=supplier.channelId,
    )

    supplier.contractStatus = "A"
    supplier.contractId = doc["document_id"]
    try:
      db.session.commit()
    except Exception:
      db.session.rollback()

  except Exception as e:
    supplier.contractStatus = "E"
    try:
      db.session.commit()
    except Exception:
      db.session.rollback()
    notify_contract_failed(
      recipient_email=recipient_email,
      supplier_name=supplier.companyName,
      reason=str(e),
      supplier_channel_id=supplier.channelId,
    )
    print(f"[{datetime.now()}] eformsign 처리 실패 seq={supplier.seq} err={e}")
