import os
import re, datetime, uuid
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
from zeep import Client, exceptions as zeep_exceptions


class BaroBillError(Exception):
  def __init__(self, code: str, message: str = ""):
    self.code = code
    self.message = message or f"BaroBill API error: {code}"
    super().__init__(self.message)


class BaroBillClient:
  """
  바로빌 공통 + 세금계산서 발행
  - CheckCorpIsMember
  - RegistCorp
  - GetBaroBillURL
  - RegistAndIssueTaxInvoice
  """

  def __init__(self) -> None:
    load_dotenv()

    self.cert_key = os.getenv("BAROBILL_CERTKEY")
    self.corp_num = (os.getenv("BAROBILL_CORPNUM") or "").replace("-", "")
    env = os.getenv("BAROBILL_ENV", "test").lower()

    if env == "prod":
      self.wsdl = "https://ws.baroservice.com/TI.asmx?WSDL"
    else:
      self.wsdl = "https://testws.baroservice.com/TI.asmx?WSDL"

    if not self.cert_key or not self.corp_num:
      raise ValueError("BAROBILL_CERTKEY, BAROBILL_CORPNUM 환경변수를 설정하세요.")

    self.client = Client(self.wsdl)

  # 관리번호 자동 생성
  def _make_mgt_key(self, target_corp_num: str) -> str:
    # 예: DOOGO-20251031-000001
    prefix = os.getenv("BAROBILL_MGT_PREFIX", "DOOGO")
    today = datetime.datetime.now().strftime("%Y%m%d")
    # 간단하게 시분초 붙이기
    timepart = datetime.datetime.now().strftime("%H%M%S")
    return f"{prefix}-{today}{timepart}"

  # ─────────────────────
  # 공통 1: 가입여부
  # ─────────────────────
  def check_corp_is_member(self, target_corp_num: str) -> bool:
    try:
      result = self.client.service.CheckCorpIsMember(
        CERTKEY=self.cert_key,
        CorpNum=self.corp_num,
        CheckCorpNum=target_corp_num.replace("-", ""),
      )
    except zeep_exceptions.Error as e:
      raise BaroBillError("-99999", f"SOAP error: {e}")

    if isinstance(result, int) and result < 0:
      raise BaroBillError(str(result), f"CheckCorpIsMember failed: {result}")

    return bool(result)

  # ─────────────────────
  # 공통 2: 회원등록
  # ─────────────────────
  def regist_corp(
    self,
    corp_num: str,
    corp_name: str,
    ceo_name: str,
    biz_type: str,
    biz_class: str,
    post_num: str,
    addr1: str,
    addr2: str,
    member_name: str,
    user_id: str = "",
    user_pwd: str = "",
    grade: str = "",
    tel: str = "",
    hp: str = "",
    email: str = "",
  ) -> int:
    try:
      result = self.client.service.RegistCorp(
        CERTKEY=self.cert_key,
        CorpNum=corp_num.replace("-", ""),
        CorpName=corp_name,
        CEOName=ceo_name,
        BizType=biz_type,
        BizClass=biz_class,
        PostNum=post_num,
        Addr1=addr1,
        Addr2=addr2,
        MemberName=member_name,
        ID=user_id,
        PWD=user_pwd,
        Grade=grade,
        TEL=tel,
        HP=hp,
        Email=email,
      )
    except zeep_exceptions.Error as e:
      raise BaroBillError("-99999", f"SOAP error: {e}")

    if isinstance(result, int) and result < 0:
      raise BaroBillError(str(result), f"RegistCorp failed: {result}")

    return int(result)

  # ─────────────────────
  # 공통 3: URL
  # ─────────────────────
  def get_barobill_url(
    self,
    target_corp_num: Optional[str] = None,
    togo: str = "",
    user_id: str = "",
    user_pwd: str = "",
  ) -> str:
    corp_for_url = (target_corp_num or self.corp_num).replace("-", "")

    try:
      result = self.client.service.GetBaroBillURL(
        CERTKEY=self.cert_key,
        CorpNum=corp_for_url,
        ID=user_id,
        PWD=user_pwd,
        TOGO=togo,
      )
    except zeep_exceptions.Error as e:
      raise BaroBillError("-99999", f"SOAP error: {e}")

    if isinstance(result, str) and re.compile(r"^-\d{5}$").match(result):
      raise BaroBillError(result, f"GetBaroBillURL failed: {result}")

    return str(result)

  # ─────────────────────
  # 세금계산서: 등록+발행 한 번에
  # ─────────────────────
  def regist_and_issue_taxinvoice(
    self,
    target_corp_num: str,
    target_corp_name: str,
    target_ceo: str = "",
    target_addr: str = "",
    target_contact: str = "",
    target_tel: str = "",
    target_email: str = "",
    target_id: str = "",
    items: Optional[List[Dict[str, Any]]] = None,
    write_date: Optional[str] = None,
    amount_total: str = "",
    tax_total: str = "",
    total_amount: str = "",
    send_sms: bool = True,
    mgt_key: Optional[str] = None,
  ) -> int:
    """
    두고 -> 대상 업체로 세금계산서 발행
    - write_date 없으면 오늘
    - mgt_key 없으면 자동 생성
    """
    items = items or []

    # 1) 날짜 기본값
    if not write_date:
      write_date = datetime.datetime.now().strftime("%Y%m%d")

    # 2) 관리번호 기본값
    if not mgt_key:
      mgt_key = self._make_mgt_key(target_corp_num)

    TaxInvoice = self.client.get_type("ns0:TaxInvoice")
    InvoiceParty = self.client.get_type("ns0:InvoiceParty")
    ArrayOfItem = self.client.get_type("ns0:ArrayOfTaxInvoiceTradeLineItem")
    ItemType = self.client.get_type("ns0:TaxInvoiceTradeLineItem")

    line_items = [
      ItemType(
        Name=i.get("name", ""),
        Information=i.get("information", ""),
        ChargeableUnit=i.get("qty", ""),
        UnitPrice=i.get("unit_price", ""),
        Amount=i.get("amount", ""),
        Tax=i.get("tax", ""),
        Description=i.get("description", ""),
      )
      for i in items
    ]

    invoice = TaxInvoice(
      IssueDirection=1,
      TaxInvoiceType=1,
      TaxType=1,
      TaxCalcType=1,
      PurposeType=2,
      WriteDate=write_date,
      AmountTotal=amount_total,
      TaxTotal=tax_total,
      TotalAmount=total_amount,
      InvoicerParty=InvoiceParty(
        MgtNum=mgt_key,              # ← 우리쪽 관리번호
        CorpNum=self.corp_num,
        CorpName="주식회사 두고",
        CEOName="문원오",
        Addr="세종특별자치시 갈매로 353, 제5층 5023호(어진동)",
        ContactName="문원오",  # ✅ 필수
        TEL="01090062186",
        Email="doogobiz@gmail.com",
        ContactID="ceo@doogo.co"
      ),
      InvoiceeParty=InvoiceParty(
        MgtNum="",                   # 상대쪽은 비워둬도 됨
        CorpNum=target_corp_num.replace("-", ""),
        CorpName=target_corp_name,
        CEOName=target_ceo,
        Addr=target_addr,
        ContactName=target_contact,  # ✅ 여기!
        TEL=target_tel,
        Email=target_email,
        ContactID = target_id
      ),
      BrokerParty=InvoiceParty(CorpNum=""),
      TaxInvoiceTradeLineItems=ArrayOfItem(line_items),
    )

    try:
      result = self.client.service.RegistAndIssueTaxInvoice(
        CERTKEY=self.cert_key,
        CorpNum=self.corp_num,   # 발행자는 항상 두고
        Invoice=invoice,
        SendSMS=send_sms,
        ForceIssue=False,
        MailTitle="[두고] 세금계산서 발행 안내",
      )
    except zeep_exceptions.Error as e:
      raise BaroBillError("-99999", f"SOAP error: {e}")

    if isinstance(result, int) and result < 0:
      raise BaroBillError(str(result), f"RegistAndIssueTaxInvoice failed: {result}")

    return int(result)