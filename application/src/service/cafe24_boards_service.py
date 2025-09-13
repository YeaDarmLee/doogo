# application/src/service/cafe24_boards_service.py
# -*- coding: utf-8 -*-
import logging, os, re, html as _html
from datetime import datetime
from typing import Dict, Any, List, Optional
from flask import current_app
import requests

from application.src.service.slackService import client
from application.src.repositories.SupplierListRepository import SupplierListRepository
from application.src.models.SupplierList import SupplierList  # ✅ DB 저장에 필요

# OAuth 토큰 유틸
from application.src.service.cafe24_oauth_service import get_access_token

logger = logging.getLogger("cafe24.boards")

# 게시판 라우팅
BOARD_ROUTE = {
  2: "broadcast_only",       # 공급사 입점
  4: "broadcast_and_vendor", # 상품후기
  6: "broadcast_and_vendor", # 상품 Q&A
}

# 게시판 번호 ↔ 이름
BOARD_NAME_MAP = {
  1: "공지사항",
  5: "멤버쉽가입",
  1002: "대량 구매 문의",
  3: "자주묻는 질문",
  3001: "대량주문",
  101: "브랜드 입점 문의",
  8: "이벤트",
  6: "상품 Q&A",
  2: "공급사 입점",
  4: "상품후기",
  9: "1:1 맞춤상담",
  7: "자료실",
  1001: "한줄메모",
}

# 상태 코드 지정: 'R' = 승인 대기(Review)
STATE_WAITING_REVIEW = "R"  # 승인 대기 상태(신규 지정)

# 벤더 채널 프리픽스
VENDOR_PREFIX = os.getenv("SLACK_VENDOR_PREFIX", "vendor-").strip() or "vendor-"

# ---------- 유틸 ----------
def _safe_trunc(s: Optional[str], max_len: int) -> Optional[str]:
  if s is None:
    return None
  s = str(s).strip()
  return s[:max_len] if len(s) > max_len else s

def _sanitize_company_name(name: Optional[str]) -> str:
  """
  회사명에서 특수문자와 공백 제거 후 반환
  """
  if not name:
    return ""
  # 특수문자 제거
  cleaned = re.sub(r"[^0-9a-zA-Z가-힣]", "", name)
  # 공백 제거
  cleaned = cleaned.replace(" ", "")
  return cleaned

class Cafe24BoardsService:
  def __init__(self, broadcast_env: str = "SLACK_BROADCAST_CHANNEL_ID"):
    self.broadcast = os.getenv(broadcast_env, "").strip()
    self.base_url = (os.getenv("CAFE24_BASE_URL", "") or "").strip().rstrip("/")  # 예: https://onedayboxb2b.cafe24api.com
    # 선택: 당일 조회 실패 시 ±1일 백업 검색 허용 (현 구현은 당일 1샷만 사용)
    self.search_fallback_day = int(os.getenv("BOARD_SEARCH_FALLBACK_DAY", "0"))  # 0:off, 1:on

    if not self.base_url:
      logger.warning("CAFE24_BASE_URL is not set; API calls will fail.")

  # ---------------- Slack helpers ----------------
  def _post_to_channel(self, channel_id: str, text: str):
    if not channel_id:
      raise ValueError("Broadcast channel is not configured (SLACK_BROADCAST_CHANNEL_ID).")
    client.chat_postMessage(channel=channel_id, text=text)

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
  def _html_to_text(self, html_str: str) -> str:
    """
    HTML → text 변환(경량):
    - <br>, </p>, </div> → 개행 / 태그 제거 / 엔티티 언이스케이프 / 공백 정리
    """
    if not html_str:
      return ""
    s = html_str
    s = re.sub(r"(?i)</p\s*>|<br\s*/?>|</div\s*>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

  def _strip_original_quote(self, text: str) -> str:
    """'[ Original Message ]' 이후 인용 블록 제거"""
    if not text:
      return ""
    m = re.search(r"\[\s*Original\s+Message\s*\]", text, flags=re.IGNORECASE)
    if not m:
      return text
    return text[:m.start()].rstrip()

  def _clean_qa_review_content(self, html_str: str, max_len: int = 400) -> str:
    """보드 4,6: HTML→텍스트, 인용 제거, 길이 제한"""
    txt = self._html_to_text(html_str)
    txt = self._strip_original_quote(txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    return (txt[:max_len] + "…") if len(txt) > max_len else txt

  def _parse_board2_application(self, html_str: str) -> Dict[str, str]:
    """
    보드 2(공급사 입점): <div class="item-tit">제목</div><div class="item-cont">값</div> 쌍 파싱 → {제목: 값}
    """
    result: Dict[str, str] = {}
    if not html_str:
      return result
    pattern = re.compile(
      r'<div\s+class="item-tit">\s*(.*?)\s*</div>\s*<div\s+class="item-cont">\s*(.*?)\s*</div>',
      flags=re.IGNORECASE | re.DOTALL
    )
    for m in pattern.finditer(html_str):
      k_raw, v_raw = m.group(1), m.group(2)
      k = self._html_to_text(k_raw)
      v = self._html_to_text(v_raw)
      if k:
        result[k] = v
    return result

  def _format_board2_application(self, parsed: Dict[str, str]) -> List[str]:
    """보드 2 파싱 결과를 Slack용 라인 배열로 정리"""
    fields = [
      ("회사명", "회사명"),
      ("상품 공급 유형", "상품 공급 유형"),
      ("주력 카테고리", "주력 카테고리"),
      ("주력 상품 정보", "주력 상품 정보"),
      ("자사몰 URL", "자사몰 URL"),
      ("담당자명", "담당자명"),
      ("직책", "직책"),
      ("연락처", "연락처"),
      ("이메일", "이메일"),
      ("문의사항", "문의사항"),
    ]
    lines: List[str] = []
    for label, key in fields:
      val = (parsed.get(key) or "").strip()
      if val:
        lines.append(f"{label}: {val}")
    return lines

  # ---------------- builders ----------------
  def _build_text(self, title: str, body_lines: List[str]) -> str:
    body = "\n".join(body_lines)
    return f"*{title}*\n```{body}```"

  # ---------------- persistence (보드2 자동 저장) ----------------
  def _persist_supplier_application(self, parsed: Dict[str, str]) -> Optional[SupplierList]:
    """
    보드 2 파싱 결과를 SupplierList로 저장(승인 대기 'R')
    필드 길이 제약을 안전하게 잘라 저장.
    """
    try:
      entity = SupplierList(
        companyName = _safe_trunc(_sanitize_company_name(parsed.get("회사명")), 100),
        supplierURL=_safe_trunc(parsed.get("자사몰 URL"), 255),
        manager=_safe_trunc(parsed.get("담당자명"), 100),
        managerRank=_safe_trunc(parsed.get("직책"), 50),
        number=_safe_trunc(parsed.get("연락처"), 50),
        email=_safe_trunc(parsed.get("이메일"), 255),
        stateCode=STATE_WAITING_REVIEW,              # 'R' = 승인 대기
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
          clean = self._clean_qa_review_content(raw_content, max_len=400)
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
          base = self._html_to_text(raw_content)
          snippet = (base[:200] + "…") if len(base) > 200 else base
          body_lines += [f"제목: {title}", f"등록일시: {created}", f"내용: {snippet}" if snippet else "내용: (비어 있음)"]

      else:
        body_lines.append("※ 게시물 상세 조회에 실패하여 웹훅 원문만 전송됨")

      # 제목에 게시판명 포함
      msg_title = f":memo: [Cafe24] {board_name} 등록 알림"
      text = self._build_text(msg_title, body_lines)

      # 1) 브로드캐스트
      self._post_to_channel(self.broadcast, text)

      # 2) 공급사 채널 동시 전파(후기/문의)
      if route == "broadcast_and_vendor" and article:
        supplier_code = self._resolve_supplier_code_from_article(article)  # 단일 코드
        if supplier_code:
          supplier = SupplierListRepository.findBySupplierCode(supplier_code)
          if supplier and getattr(supplier, "channelId", None):
            try:
              self._post_to_channel(supplier.channelId, text)
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
