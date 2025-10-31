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
from application.src.repositories.SupplierDetailRepository import SupplierDetailRepository
from application.src.service.eformsign_service import after_slack_success
from application.src.service.barobill_service import BaroBillClient, BaroBillError

# 오케스트레이션(채널 생성/알림)은 서비스 레이어 사용
from application.src.service.slack_provision_service import (
  create_slack_channel,  # 기존 호출부 호환
  send_workspace_join_invite_email,
  notify_invite_mail_sent,
  notify_user_invited_to_channel
)

from application.src.service import slack_service as SU
from application.src.utils import template as TEMPLATE

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
          after_slack_success(s)
        
        # 2-4) 바로빌 회원등록
        sd = SupplierDetailRepository.findBySupplierSeq(s.seq)
        baro = BaroBillClient()

        try:
          if not baro.check_corp_is_member(sd.businessRegistrationNumber):
            baro.regist_corp(
              corp_num=sd.businessRegistrationNumber,
              corp_name=s.companyName,
              ceo_name=sd.representativeName,
              biz_type=sd.bizType,
              biz_class=sd.bizClass,
              post_num="",    # 더 이상 사용되지 않는 항목
              addr1=sd.bizAddr,
              addr2="",
              member_name=s.manager,
              user_id=s.supplierID,
              user_pwd=s.supplierPW,
              grade=s.managerRank,
              tel=s.number,
              email=s.email,
            )

          url = baro.get_barobill_url(
            sd.businessRegistrationNumber,
            user_id=s.supplierID,
            togo="CERT"
          )
          
          template_msg = TEMPLATE.render(
            "barobill_cert_required",
            supplier_name=s.companyName,
            corp_num=sd.businessRegistrationNumber,
            supplier_id=s.supplierID,
            supplier_pw=s.supplierPW,
            cert_url=url,
          )
          SU.post_text(s.channelId, template_msg)

        except BaroBillError as e:
          print(f"API 실패: {e.code} / {e.message}")

        # 2-5) 마무리 상태 정리
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