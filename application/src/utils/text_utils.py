# application/src/utils/text_utils.py
# -*- coding: utf-8 -*-
from __future__ import annotations
import re, html as _html
from typing import Optional

def html_to_text(html_str: str) -> str:
  """
  가벼운 HTML→텍스트 변환:
  - <br>, </p>, </div> 를 개행으로 치환
  - 태그 제거, HTML 엔티티 언이스케이프
  - 공백/개행 다듬기
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

def strip_original_quote(text: str) -> str:
  """
  게시판 본문 중 '[ Original Message ]' 이후 인용 블록 제거.
  """
  if not text:
    return ""
  m = re.search(r"\[\s*Original\s+Message\s*\]", text, flags=re.IGNORECASE)
  if not m:
    return text
  return text[:m.start()].rstrip()

def clean_qa_review_content(html_str: str, max_len: int = 400) -> str:
  """
  Q&A/후기 본문 정제 + 길이 제한.
  """
  txt = html_to_text(html_str)
  txt = strip_original_quote(txt)
  txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
  return (txt[:max_len] + "…") if len(txt) > max_len else txt

def safe_trunc(s: Optional[str], max_len: int) -> Optional[str]:
  """
  문자열을 최대 길이로 안전 절단.
  """
  if s is None:
    return None
  s = str(s).strip()
  return s[:max_len] if len(s) > max_len else s

def sanitize_company_name(name: Optional[str]) -> str:
  """
  회사명에서 특수문자/공백 제거.
  """
  if not name:
    return ""
  cleaned = re.sub(r"[^0-9a-zA-Z가-힣]", "", name)
  cleaned = cleaned.replace(" ", "")
  return cleaned
