# test_barobill.py
from decimal import Decimal, ROUND_HALF_UP
from barobill_service import BaroBillClient, BaroBillError


def split_vat(total: int, vat_rate: Decimal = Decimal("0.1")) -> tuple[int, int]:
  """
  세금 포함 금액 → (공급가액, 세액) 으로 분리
  예) 110000 → (100000, 10000)
  """
  total_dec = Decimal(total)
  supply = (total_dec / (Decimal(1) + vat_rate)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
  tax = total_dec - supply
  return int(supply), int(tax)


def main():
  # 1) 클라이언트 생성 (.env 없으면 여기서 터진다)
  baro = BaroBillClient()

  # 2) 테스트용 발행 대상 (A업체)
  target_corp_num = "6594301282"     # 테스트용 사업자번호
  target_corp_name = "데브브릿지"
  target_ceo = "이예닮"
  target_addr = "경기 고양시 덕양구 내유동 444-6"
  target_contact = "이예닮"
  target_tel = "01059442263"
  target_email = "gnswpwhrqkf3@gmail.com"
  target_id = "gkkiccvd12"

  # 3) 우리가 실제로 아는 건 '세금 포함 전체 금액'
  final_amount = 4535  # ← 여기만 바꿔가면서 테스트

  # 4) 공급가/세액 분리
  supply, tax = split_vat(final_amount)

  print(f"[TEST] total={final_amount}, supply={supply}, tax={tax}")

  try:
    res = baro.regist_and_issue_taxinvoice(
      target_corp_num=target_corp_num,
      target_corp_name=target_corp_name,
      target_ceo=target_ceo,
      target_addr=target_addr,
      target_contact=target_contact,
      target_tel=target_tel,
      target_email=target_email,
      target_id=target_id,
      amount_total=str(supply),
      tax_total=str(tax),
      total_amount=str(final_amount),
      items=[
        {
          "name": "판매 지급 수수료",
          "qty": "1",
          "unit_price": str(final_amount),
          "amount": str(supply),
          "tax": str(tax),
          "description": "테스트 발행",
        }
      ],
      send_sms=False,  # 테스트에서는 문자 안보내게
    )
    # 여기까지 오면 SOAP 호출은 성공해서 코드가 온 거야
    if res == 0:
      print("✅ 세금계산서 발행 성공")
    else:
      # 0이 아닌 양수가 올 수도 있어서 일단 찍어보자
      print(f"⚠️ 발행 응답 코드: {res}")

  except BaroBillError as e:
    # 바로빌이 준 실제 오류코드
    print(f"❌ 발행 실패: code={e.code}, message={e.message}")


if __name__ == "__main__":
  main()
