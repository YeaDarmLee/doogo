# application/src/service/cafe24_boards_service.py
# -*- coding: utf-8 -*-
import logging, os, re, html as _html
from datetime import datetime
from typing import Dict, Any, List, Optional
from flask import current_app
import requests

from application.src.repositories.SupplierListRepository import SupplierListRepository
from application.src.models.SupplierList import SupplierList  # ✅ DB 저장에 필요

from application.src.service import slack_service as SU
from application.src.utils.text_utils import (
  html_to_text, clean_qa_review_content,
  safe_trunc, sanitize_company_name
)
from application.src.utils.cafe24_utils import (
  BOARD_ROUTE, BOARD_NAME_MAP
)

# OAuth 토큰 유틸
from application.src.service.cafe24_oauth_service import get_access_token

logger = logging.getLogger("cafe24.boards")

# 상태 코드 지정: 'R' = 승인 대기(Review)
STATE_WAITING_REVIEW = "R"  # 승인 대기 상태(신규 지정)

# 벤더 채널 프리픽스
VENDOR_PREFIX = os.getenv("SLACK_VENDOR_PREFIX", "vendor-").strip() or "vendor-"

class Cafe24BoardsService:
  def __init__(self, broadcast_env: str = "SLACK_BROADCAST_CHANNEL_ID"):
    self.broadcast = os.getenv(broadcast_env, "").strip()
    self.base_url = (os.getenv("CAFE24_BASE_URL", "") or "").strip().rstrip("/")  # 예: https://onedayboxb2b.cafe24api.com
    # 선택: 당일 조회 실패 시 ±1일 백업 검색 허용 (현 구현은 당일 1샷만 사용)
    self.search_fallback_day = int(os.getenv("BOARD_SEARCH_FALLBACK_DAY", "0"))  # 0:off, 1:on

    if not self.base_url:
      logger.warning("CAFE24_BASE_URL is not set; API calls will fail.")

  # ---------------- Cafe24 helpers ----------------
  def _api_base(self) -> str:
    """ BASE_URL + /api/v2/admin """
    if not self.base_url:
      raise ValueError("CAFE24_BASE_URL not configured")
    return f"{self.base_url}/api/v2/admin"

  def _auth_headers(self) -> Dict[str, str]:
    """OAuth 액세스 토큰을 가져와 Authorization 헤더 구성."""
    token = get_access_token()
    return {
      "Authorization": f"Bearer {token}",
      "Content-Type": "application/json",
    }

  def _get_articles(
    self,
    board_no: int,
    day_str: str,
    offset: int = 0,
    limit: int = 100,
    fields: Optional[str] = None
  ) -> List[Dict[str, Any]]:
    """
    GET /api/v2/admin/boards/{board_no}/articles
    - 파라미터: start_date, end_date (YYYY-MM-DD), offset(최대 8000), limit(1~100)
    """
    url = f"{self._api_base()}/boards/{board_no}/articles"
    headers = self._auth_headers()
    params = {
      "start_date": day_str,
      "end_date": day_str,
      "offset": offset,
      "limit": limit,
    }
    if fields:
      params["fields"] = fields

    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    data = res.json() or {}
    return data.get("articles") or []

  def _pick_article(self, board_no: int, post_no: Any, run_date: datetime) -> Optional[Dict[str, Any]]:
    """
    실행일 기준 당일(date_str)로 조회(offset=0, limit=100) 후 article_no 매칭
    """
    day_str = run_date.strftime("%Y-%m-%d")
    fields = "article_no,title,content,created_date,member_id,writer,product_no"
    try:
      items = self._get_articles(board_no, day_str, offset=0, limit=100, fields=fields)
    except Exception as e:
      logger.exception(f"[board:fetch] board={board_no} date={day_str} error={e}")
      return None

    for a in items:
      if str(a.get("article_no")) == str(post_no):
        return a
    return None

  def _get_product_supplier_code(self, product_no: int) -> Optional[str]:
    """
    GET /api/v2/admin/products/{product_no}
    fields=product_no,supplier_code 만 요청 → supplier_code 단일 반환
    """
    url = f"{self._api_base()}/products/{int(product_no)}"
    headers = self._auth_headers()
    params = {"fields": "product_no,supplier_code"}
    try:
      res = requests.get(url, headers=headers, params=params, timeout=10)
      res.raise_for_status()
      data = res.json() or {}
      product = data.get("product") or {}
      code = product.get("supplier_code")
      if isinstance(code, str) and code.strip():
        return code.strip().upper()
      return None
    except Exception as e:
      logger.exception(f"[product:fetch-supplier] product_no={product_no} err={e}")
      return None

  def _resolve_supplier_code_from_article(self, article: Dict[str, Any]) -> Optional[str]:
    """article.product_no → 제품 단건 조회 → supplier_code(단일) 반환"""
    raw = article.get("product_no")
    if raw is None or str(raw).strip() == "":
      return None
    try:
      pno = int(str(raw).strip())
    except Exception:
      return None
    return self._get_product_supplier_code(pno)

  # ---------------- content cleaners ----------------
  def _parse_board2_application(self, html_str: str) -> Dict[str, str]:
    """
    섹션 인지 파싱:
    - 섹션1(사업자): 정상 라벨 → biz_* 키
    - 섹션2(담당자/상품): 정상 라벨 → mgr_*/상품 키
    - 섹션3(정산/세금계산서): 현행 라벨(담당자명/직책/연락처)을 의미 기준으로 → settle_bank/settle_account/tax_email
    """
    result: Dict[str, str] = {}
    if not html_str:
      return result

    sec_pat = re.compile(r"<section[^>]*>(.*?)</section>", re.I | re.S)
    title_pat = re.compile(r'<div\s+class="se-title">\s*(.*?)\s*</div>', re.I | re.S)
    pair_pat  = re.compile(
      r'<div\s+class="item-tit">\s*(.*?)\s*</div>\s*'
      r'<div\s+class="item-cont">\s*(.*?)\s*</div>',
      re.I | re.S
    )

    def norm(s: str) -> str:
      return (html_to_text(s) or "").strip()

    MAP_BIZ = {
      "상호명": "biz_company_name",
      "대표자명": "biz_representative",
      "사업자번호": "biz_reg_no",
      "업종/업태": "biz_type",
      "사업자주소": "biz_addr",
      "사업자전화번호": "biz_phone",
    }
    MAP_CONTACT = {
      "담당자명": "mgr_name",
      "직책": "mgr_title",
      "연락처": "mgr_phone",
      "슬랙 초대 받을 이메일": "slack_email",
      "상품 공급 유형": "supply_type",
      "주력 카테고리": "main_category",
      "주력 상품 URL": "main_product_url",
      "운영중인 홈페이지 및 쇼핑몰 URL": "homepage_url",
      "공급사 택배 마감시간": "ship_cutoff",
    }
    # ★ 섹션3 현재 라벨을 의미에 맞게 강제 매핑
    MAP_SETTLE = {
      "담당자명": "settle_bank",     # 예: 카카오뱅크
      "직책": "settle_account",       # 예: 00000000000
      "연락처": "tax_email",          # 예: gnswpwhrqkf3@gmail.com
      "문의 사항": "inquiry",
    }

    def which_section(title: str) -> str:
      t = (title or "").replace(" ", "")
      if "사업자" in t:
        return "biz"
      if "담당자" in t and "상품" in t:
        return "contact"
      if "정산" in t or "세금계산서" in t or "통장" in t:
        return "settle"
      return "unknown"

    for sec in sec_pat.finditer(html_str):
      block = sec.group(1)
      tit_m = title_pat.search(block)
      sec_title = norm(tit_m.group(1)) if tit_m else ""
      kind = which_section(sec_title)

      pairs = [(norm(a), norm(b)) for a, b in pair_pat.findall(block)]

      if kind == "biz":
        for k, v in pairs:
          std = MAP_BIZ.get(k)
          if std:
            result[std] = v

      elif kind == "contact":
        for k, v in pairs:
          std = MAP_CONTACT.get(k)
          if std:
            result[std] = v

      elif kind == "settle":
        for k, v in pairs:
          std = MAP_SETTLE.get(k)
          if std:
            result[std] = v

      else:
        # 분석용 백업(미지정 섹션) — 충돌 없게 기존 키 없을 때만 저장
        for k, v in pairs:
          if k and v and k not in result:
            result[k] = v

    return result

  def _format_board2_application(self, parsed: Dict[str, str]) -> List[str]:
    """
    Slack 표시: 반드시 섹션별 표준 키만 사용 → 충돌/오염 방지
    """
    lines: List[str] = []

    biz = [
      ("상호명", parsed.get("biz_company_name")),
      ("대표자명", parsed.get("biz_representative")),
      ("사업자번호", parsed.get("biz_reg_no")),
      ("업종/업태", parsed.get("biz_type")),
      ("사업자주소", parsed.get("biz_addr")),
      ("사업자전화번호", parsed.get("biz_phone")),
    ]
    b = [f"{k}: {v.strip()}" for k, v in biz if (v or "").strip()]
    if b:
      lines.append("[사업자 정보]")
      lines.extend(b)

    contact = [
      ("담당자명", parsed.get("mgr_name")),
      ("직책", parsed.get("mgr_title")),
      ("연락처", parsed.get("mgr_phone")),
      ("슬랙 초대 이메일", parsed.get("slack_email")),
      ("상품 공급 유형", parsed.get("supply_type")),
      ("주력 카테고리", parsed.get("main_category")),
      ("주력 상품 URL", parsed.get("main_product_url")),
      ("자사몰/쇼핑몰 URL", parsed.get("homepage_url")),
      ("택배 마감시간", parsed.get("ship_cutoff")),
    ]
    c = [f"{k}: {v.strip()}" for k, v in contact if (v or "").strip()]
    if c:
      if lines: lines.append("")
      lines.append("[담당자·상품]")
      lines.extend(c)

    settle = [
      ("정산 은행명", parsed.get("settle_bank")),
      ("정산 계좌번호", parsed.get("settle_account")),
      ("세금계산서 이메일", parsed.get("tax_email")),
      ("문의사항", parsed.get("inquiry")),
    ]
    s = [f"{k}: {v.strip()}" for k, v in settle if (v or "").strip()]
    if s:
      if lines: lines.append("")
      lines.append("[정산·세금계산서]")
      lines.extend(s)

    return lines

  # ---------------- builders ----------------
  def _build_text(self, title: str, body_lines: List[str]) -> str:
    body = "\n".join(body_lines)
    return f"*{title}*\n```{body}```"

  # ---------------- persistence (보드2 자동 저장) ----------------
  def _persist_supplier_application(self, parsed: Dict[str, str]) -> Optional[SupplierList]:
    """
    저장 우선순위(현행 입력 스키마에 정확히 맞춤):
    - 이메일: slack_email → tax_email
    - 연락처: mgr_phone → biz_phone
    - URL: homepage_url → main_product_url
    """
    try:
      company = sanitize_company_name(parsed.get("biz_company_name"))
      supplier_url = (parsed.get("homepage_url") or parsed.get("main_product_url") or "").strip()
      manager = (parsed.get("mgr_name") or "").strip()
      manager_rank = (parsed.get("mgr_title") or "").strip()
      phone = (parsed.get("mgr_phone") or parsed.get("biz_phone") or "").strip()
      email = (parsed.get("slack_email") or parsed.get("tax_email") or "").strip()
      supplier_id = email.split("@")[0] if email else ""

      entity = SupplierList(
        companyName = safe_trunc(company, 100),
        supplierURL = safe_trunc(supplier_url, 255),
        manager     = safe_trunc(manager, 100),
        managerRank = safe_trunc(manager_rank, 50),
        number      = safe_trunc(phone, 50),
        email       = safe_trunc(email, 255),
        supplierID  = supplier_id,
        supplierPW  = "qksksk1324$",
        stateCode   = STATE_WAITING_REVIEW,  # 'R'
        contractTemplate = "A",
        contractPercent  = "15",
      )
      saved = SupplierListRepository.save(entity)
      logger.info(f"[board2-save-ok] seq={saved.seq} company={saved.companyName!r} state={saved.stateCode}")
      return saved
    except Exception as e:
      logger.exception(f"[board2-save-fail] err={e}")
      return None

  # ---------------- main ----------------
  def notify_board_created(self, payload: Dict[str, Any], topic: str):
    try:
      resource = payload.get("resource") or {}
      board_no = resource.get("board_no")
      post_no = resource.get("no")
      member_id = resource.get("member_id")
      writer = resource.get("writer")

      # 번호 → 이름 치환
      try:
        bno = int(board_no)
      except Exception:
        bno = None
      board_name = BOARD_NAME_MAP.get(bno, str(board_no))
      route = BOARD_ROUTE.get(bno, "broadcast_only")

      # 당일 기사 단건 찾기
      run_dt = datetime.now()
      article = self._pick_article(bno, post_no, run_dt) if bno else None

      body_lines = [
        f"Board: {board_name}",
        f"Post No: {post_no}",
        f"작성자: {writer} ({member_id})",
      ]

      if article:
        title = (article.get("title") or "").strip()
        created = (article.get("created_date") or "").strip()
        raw_content = (article.get("content") or "").strip()

        if bno in (4, 6):
          # Q&A/후기 정제
          clean = clean_qa_review_content(raw_content, max_len=400)
          body_lines += [f"제목: {title}", f"등록일시: {created}", f"내용: {clean}" if clean else "내용: (비어 있음)"]

        elif bno == 2:
          # 공급사 입점: 파싱 + 저장(승인 대기)
          parsed = self._parse_board2_application(raw_content)
          # 저장 시도
          saved = self._persist_supplier_application(parsed)
          board2_lines = self._format_board2_application(parsed)
          body_lines += [f"제목: {title}", f"등록일시: {created}"]
          body_lines += (board2_lines if board2_lines else ["내용: (입점 신청서 내용을 해석할 수 없습니다)"])
          # 저장 결과 라인 추가(선택)
          if saved:
            body_lines += [f"저장 상태: 승인 대기({STATE_WAITING_REVIEW}) / seq={saved.seq}"]
          else:
            body_lines += ["저장 상태: 저장 실패(E)"]

        else:
          # 기타 게시판: 기본 정제
          base = html_to_text(raw_content)
          snippet = (base[:200] + "…") if len(base) > 200 else base
          body_lines += [f"제목: {title}", f"등록일시: {created}", f"내용: {snippet}" if snippet else "내용: (비어 있음)"]

      else:
        body_lines.append("※ 게시물 상세 조회에 실패하여 웹훅 원문만 전송됨")

      # 제목에 게시판명 포함
      msg_title = f":memo: [Cafe24] {board_name} 등록 알림"
      text = self._build_text(msg_title, body_lines)

      # 1) 브로드캐스트
      SU.post_text(self.broadcast, text)

      # 2) 공급사 채널 동시 전파(후기/문의)
      if route == "broadcast_and_vendor" and article:
        supplier_code = self._resolve_supplier_code_from_article(article)  # 단일 코드
        if supplier_code:
          supplier = SupplierListRepository.findBySupplierCode(supplier_code)
          if supplier and getattr(supplier, "channelId", None):
            try:
              SU.post_text(supplier.channelId, text)
            except Exception as e:
              logger.warning(f"[board-vendor-post-fail] code={supplier_code} ch={supplier.channelId} err={e}")
          else:
            logger.info(f"[board-vendor-skip] no mapped channel for supplier_code={supplier_code}")
        else:
          logger.info("[board-vendor-skip] supplier_code not resolved from product_no")

      logger.info(f"[board-ok] board={board_no} post={post_no} writer={writer} route={route}")
      return True

    except Exception as e:
      logger.exception(f"[board-fail] {e}")
      try:
        if current_app:
          current_app.logger.exception(e)
      except Exception:
        pass
      return False
