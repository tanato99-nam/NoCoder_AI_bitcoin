import pyupbit
import json
import base64
import os
import re
from google import genai
from google.genai import types
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

# .env 파일 로드
load_dotenv()

# BTC 30분봉 데이터 가져오기 (1일치 = 48개)
df = pyupbit.get_ohlcv("KRW-BTC", interval="minute30", count=48)

# DataFrame을 JSON으로 변환
json_data = df.reset_index().to_json(orient='records', date_format='iso')
json_data = json.loads(json_data)  # 문자열을 파이썬 객체로 변환

# Upbit 초기화
access_key = os.getenv("UPBIT_ACCESS_KEY")
secret_key = os.getenv("UPBIT_SECRET_KEY")
upbit = pyupbit.Upbit(access_key, secret_key)

# 거래 설정
MIN_ORDER_AMOUNT = 5000  # 최소 주문 금액 (원)

def parse_gemini_response(response_text):
    """
    Gemini API 응답에서 JSON 부분을 추출하고 파싱
    """
    try:
        # JSON 형식의 문자열을 찾기 위한 정규식 패턴
        pattern = r'\{[^{}]*"decision"[^{}]*\}'
        match = re.search(pattern, response_text)
        
        if match:
            json_str = match.group()
            # JSON 파싱
            response_json = json.loads(json_str)
            return response_json
        else:
            print("Error: Could not find valid JSON in response")
            return None
    except Exception as e:
        print(f"Error parsing response: {str(e)}")
        print(f"Raw response: {response_text}")
        return None

def calculate_order_amount(balance_krw, current_price):
    """
    주문 가능한 금액 계산
    """
    # 수수료를 고려한 실제 주문 가능 금액 계산
    available_amount = balance_krw * 0.9995  # 수수료 0.05% 고려
    
    # 최소 주문 금액보다 작으면 None 반환
    if available_amount < MIN_ORDER_AMOUNT:
        return None
    
    return MIN_ORDER_AMOUNT  # 일단 최소 주문 금액으로 설정

def calculate_sell_amount(btc_balance, current_price):
    """
    매도할 BTC 수량 계산
    """
    # 최소 주문 금액에 해당하는 BTC 수량 계산
    min_btc_amount = MIN_ORDER_AMOUNT / current_price
    
    # 보유 수량이 최소 주문 금액에 해당하는 수량보다 작으면 None 반환
    if btc_balance < min_btc_amount:
        return None
        
    return min_btc_amount  # 최소 주문 금액에 해당하는 BTC 수량 반환

def execute_trade(decision, ticker="KRW-BTC"):
    """
    Gemini의 분석 결과에 따라 거래 실행
    """
    try:
        # 현재가 조회
        current_price = pyupbit.get_current_price(ticker)
        if current_price is None:
            print("현재가 조회 실패")
            return
        
        # 보유 KRW 조회
        krw_balance = upbit.get_balance("KRW")
        if krw_balance is None:
            print("원화 잔고 조회 실패")
            return
            
        # 보유 BTC 조회
        btc_balance = upbit.get_balance(ticker)
        if btc_balance is None:
            btc_balance = 0
        
        print(f"\n=== 현재 상태 ===")
        print(f"현재가: {current_price:,}원")
        print(f"보유 KRW: {krw_balance:,.0f}원")
        print(f"보유 {ticker}: {btc_balance:.8f}")
        
        result = None
        if decision == "buy":
            # 주문 가능 금액 계산
            order_amount = calculate_order_amount(krw_balance, current_price)
            if order_amount is None:
                print(f"\n매수 불가: 잔고 부족 (최소 주문 금액: {MIN_ORDER_AMOUNT:,}원, 보유: {krw_balance:,.0f}원)")
                return
                
            print(f"\n매수 시도: {order_amount:,.0f}원")
            result = upbit.buy_market_order(ticker, order_amount)
            
        elif decision == "sell":
            if btc_balance <= 0:
                print(f"\n매도 불가: 보유 수량 없음")
                return
            
            # 매도할 수량 계산 (최소 주문 금액에 해당하는 BTC)
            sell_amount = calculate_sell_amount(btc_balance, current_price)
            if sell_amount is None:
                print(f"\n매도 불가: 보유 BTC가 최소 주문 금액보다 작음 (보유: {btc_balance:.8f} BTC, 필요: {MIN_ORDER_AMOUNT/current_price:.8f} BTC)")
                return
                
            # 예상 매도 금액 계산
            expected_sell_value = sell_amount * current_price
            print(f"\n매도 시도: {sell_amount:.8f} BTC (예상 가치: {expected_sell_value:,.0f}원)")
            result = upbit.sell_market_order(ticker, sell_amount)
            
        elif decision == "hold":
            print(f"\n홀딩 결정: 현재 포지션 유지")
            return
            
        # 거래 결과 출력
        if result:
            if 'error' in result:
                print(f"거래 실패: {result['error']}")
            else:
                print(f"거래 성공: {result}")
                # 거래 후 잔고 다시 조회
                krw_balance_after = upbit.get_balance("KRW")
                btc_balance_after = upbit.get_balance(ticker)
                print(f"\n=== 거래 후 상태 ===")
                print(f"보유 KRW: {krw_balance_after:,.0f}원")
                print(f"보유 {ticker}: {btc_balance_after:.8f}")
            
    except Exception as e:
        print(f"거래 실행 중 오류 발생: {str(e)}")

def capture_chart():
    """
    업비트 차트를 캡쳐하는 함수
    """
    # 크롬 옵션 설정
    chrome_options = Options()
    chrome_options.add_argument('--start-maximized')  # 브라우저를 최대화된 상태로 시작
    
    # 크롬 드라이버 초기화
    driver = webdriver.Chrome(options=chrome_options)
    
    try:
        # 업비트 차트 페이지로 이동
        url = "https://upbit.com/full_chart?code=CRIX.UPBIT.KRW-BTC"
        driver.get(url)
        
        # 페이지가 완전히 로드될 때까지 대기
        time.sleep(5)  # 차트가 로드될 때까지 5초 대기
        
        # 30분 버튼 클릭
        thirty_min_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, '/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[1]'))
        )
        thirty_min_button.click()
        
        # 1시간 옵션 클릭
        one_hour_option = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, '/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[1]/cq-menu-dropdown/cq-item[8]'))
        )
        one_hour_option.click()
        
        # 차트가 업데이트될 때까지 잠시 대기
        time.sleep(3)
        
        # 지표 버튼 클릭
        indicator_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, '/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]'))
        )
        indicator_button.click()
        
        # 볼린저 밴드 선택
        bollinger_bands = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, '/html/body/div[1]/div[2]/div[3]/span/div/div/div[1]/div/div/cq-menu[3]/cq-menu-dropdown/cq-scroll/cq-studies/cq-studies-content/cq-item[15]'))
        )
        bollinger_bands.click()
        
        # 차트가 업데이트될 때까지 잠시 대기
        time.sleep(3)
        
        # 스크린샷 촬영
        screenshot_path = "upbit_chart.png"
        driver.save_screenshot(screenshot_path)
        print(f"스크린샷이 저장되었습니다: {screenshot_path}")
        
        return screenshot_path
        
    except Exception as e:
        print(f"차트 캡쳐 중 오류가 발생했습니다: {str(e)}")
        return None
    
    finally:
        # 브라우저 종료
        driver.quit()

def analyze_chart_image(image_path):
    """
    Gemini API를 사용하여 차트 이미지 분석
    """
    try:
        # 이미지 파일 읽기
        with open(image_path, 'rb') as f:
            image_bytes = f.read()
        
        # Gemini API 클라이언트 초기화
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            print("Error: GEMINI_API_KEY not found in .env file")
            return None
            
        client = genai.Client(api_key=api_key)
        
        # 차트 분석을 위한 프롬프트
        prompt = """이 비트코인 차트를 분석해주세요. 다음 사항들을 고려하여 분석해주세요:
        1. 현재 추세 (상승/하락/횡보)
        2. 볼린저 밴드의 위치와 의미
        3. 주요 지지선과 저항선
        4. 매수/매도 시점에 대한 제안
        분석 결과는 다음 JSON 형식으로 반환해주세요:
        {
            "trend": "상승/하락/횡보",
            "bollinger_analysis": "볼린저 밴드 분석",
            "support_resistance": "지지선/저항선 분석",
            "recommendation": "매수/매도/홀딩",
            "reason": "추천 이유"
        }"""
        
        # Gemini API 호출
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type='image/png'
                ),
                prompt
            ]
        )
        
        # 응답 파싱
        try:
            analysis_result = json.loads(response.text)
            return analysis_result
        except json.JSONDecodeError:
            print("Error: Could not parse Gemini response as JSON")
            print("Raw response:", response.text)
            return None
            
    except Exception as e:
        print(f"차트 이미지 분석 중 오류 발생: {str(e)}")
        return None

def generate():
    # .env 파일에서 API 키 가져오기
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY not found in .env file")
        return

    client = genai.Client(
        api_key=api_key,
    )

    model = "gemini-2.0-flash"
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=json.dumps(json_data, indent=2)),
                types.Part.from_text(text="INSERT_INPUT_HERE"),
            ],
        ),
    ]
    generate_content_config = types.GenerateContentConfig(
        response_mime_type="text/plain",
        system_instruction=[
            types.Part.from_text(text="""You're a Bitcoin investor, and based on the chart provided, you'll need to decide whether to Buy, Sell, or Hold.
reason example:
{\"decision\":\"buy\", \"reason\":\"some technical reason\"}
{\"decision\":\"sell\", \"reason\":\"some technical reason\"}
{\"decision\":\"hold\", \"reason\":\"some technical reason\"}"""),
        ],
    )

    try:
        response_text = ""
        for chunk in client.models.generate_content_stream(
            model=model,
            contents=contents,
            config=generate_content_config,
        ):
            response_text += chunk.text
            print(chunk.text, end="")
        
        # Gemini 응답 파싱
        response_json = parse_gemini_response(response_text)
        if response_json and "decision" in response_json:
            print(f"\n\n=== Gemini 분석 결과 ===")
            print(f"결정: {response_json['decision']}")
            print(f"이유: {response_json.get('reason', 'No reason provided')}")
            
            # 거래 실행
            print("\n=== 거래 실행 ===")
            execute_trade(response_json["decision"])
        else:
            print("\nError: Invalid response format from Gemini")
            
    except Exception as e:
        print(f"Error during API call: {str(e)}")

if __name__ == "__main__":
    # 차트 캡쳐 실행
    print("\n=== 차트 캡쳐 시작 ===")
    chart_image_path = capture_chart()
    
    if chart_image_path:
        # 차트 이미지 분석
        print("\n=== 차트 이미지 분석 시작 ===")
        chart_analysis = analyze_chart_image(chart_image_path)
        
        if chart_analysis:
            print("\n=== 차트 이미지 분석 결과 ===")
            print(json.dumps(chart_analysis, indent=2, ensure_ascii=False))
            
            # 차트 분석 결과를 기반으로 거래 실행
            if "recommendation" in chart_analysis:
                print("\n=== 차트 분석 기반 거래 실행 ===")
                execute_trade(chart_analysis["recommendation"].lower())
    
    # 데이터 출력
    print("\n=== BTC 30분봉 데이터 (최근 1일) - JSON 형식 ===")
    print("데이터 개수:", len(json_data))
    print("\n[데이터]")
    print(json.dumps(json_data, indent=2, ensure_ascii=False))
    
    print("\n=== Gemini API 분석 시작 ===")
    generate()
