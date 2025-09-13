# application/src/service/cafe24_boards_service.py
# -*- coding: utf-8 -*-
import logging, os, re, html as _html
from datetime import datetime
from typing import Dict, Any, List, Optional
from flask import current_app
import requests

from application.src.service.slackService import client
from application.src.repositories.SupplierListRepository import SupplierListRepository
from application.src.service.cafe24_oauth_service import get_access_token  # ✅ OAuth 토큰 사용

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
    """
    OAuth 액세스 토큰을 가져와 Authorization 헤더 구성.
    - 모듈 내부 캐시로 필요 시 자동 재발급됨.
    """
    token = get_access_token()  # 예외 발생 시 상위에서 로깅/처리
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
    - 현재는 단건 매칭만 필요하므로 offset=0, limit=100으로 1회 조회
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
    실행일 기준 당일(date_str)로 1회 조회(offset=0, limit=100) 후 article_no 매칭
    - 게시물 수가 추후 100건을 넘으면, offset을 100씩 증가시켜 스캔하면 됨.
      예) for off in range(0, 8200, 100): ...
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
    """
    article.product_no → 제품 단건 조회 → supplier_code(단일) 반환
    """
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
    가벼운 HTML → text 변환:
    - <br>, </p>, </div> → 개행
    - 태그 제거
    - HTML 엔티티 언이스케이프(&nbsp; 등)
    - 연속 공백/개행 정리
    """
    if not html_str:
      return ""
    s = html_str
    s = re.sub(r"(?i)</p\s*>|<br\s*/?>|</div\s*>", "\n", s)  # 줄바꿈
    s = re.sub(r"<[^>]+>", "", s)                            # 태그 제거
    s = _html.unescape(s)                                    # 엔티티 해제
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

  def _strip_original_quote(self, text: str) -> str:
    """
    '[ Original Message ]' 이후 인용 블록 제거 (대소문자/공백 관대한 매칭)
    """
    if not text:
      return ""
    m = re.search(r"\[\s*Original\s+Message\s*\]", text, flags=re.IGNORECASE)
    if not m:
      return text
    return text[:m.start()].rstrip()

  def _clean_qa_review_content(self, html_str: str) -> str:
    """
    보드 4,6(후기/Q&A) 전용: HTML→텍스트, 인용 제거
    """
    txt = self._html_to_text(html_str)
    txt = self._strip_original_quote(txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    return txt

  def _parse_board2_application(self, html_str: str) -> Dict[str, str]:
    """
    보드 2(공급사 입점) 전용: <div class="item-tit">제목</div><div class="item-cont">값</div> 쌍 파싱
    반환: {제목: 값}
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
    """
    보드 2 파싱 결과를 Slack용 라인 배열로 정리
    """
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
        f"게시물 번호: {post_no}",
        f"작성자: {writer} ({member_id})",
      ]

      if article:
        title = (article.get("title") or "").strip()
        created = (article.get("created_date") or "").strip()
        raw_content = (article.get("content") or "").strip()

        # 보드별 content 정제
        if bno in (4, 6):
          clean = self._clean_qa_review_content(raw_content)
          body_lines += [
            f"제목: {title}",
            f"내용: {clean}" if clean else "내용: (비어 있음)",
            f"등록일시: {created}",
          ]
        elif bno == 2:
          parsed = self._parse_board2_application(raw_content)
          board2_lines = self._format_board2_application(parsed)
          body_lines += [
            f"제목: {title}",
            f"등록일시: {created}",
          ]
          body_lines += (board2_lines if board2_lines else ["내용: (입점 신청서 내용을 해석할 수 없습니다)"])
        else:
          # 기타 게시판: 기본 HTML→텍스트 후 200자 스니펫
          base = self._html_to_text(raw_content)
          snippet = (base[:200] + "…") if len(base) > 200 else base
          body_lines += [
            f"제목: {title}",
            f"내용: {snippet}" if snippet else "내용: (비어 있음)",
            f"등록일시: {created}",
          ]
      else:
        body_lines.append("※ 게시물 상세 조회에 실패하여 웹훅 원문만 전송됨")

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
