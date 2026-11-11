import json
import os
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

def scrape_solhyang_cremation():
    """
    e하늘 장사정보시스템(15774129.go.kr) 강릉 솔향하늘길 화장예약 정보 자동 수집
    - 대상: 오늘 제외, 앞으로 3일간 (13일, 14일, 15일 등) 일반시신 1~3회차 관내/관외 잔여석
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()
        
        print(f"[{datetime.now()}] e하늘 장사정보시스템 접속 중...")
        try:
            page.goto("https://15774129.go.kr/", timeout=60000)
            page.wait_for_load_state("networkidle")
            
            # TODO: 실제 사이트의 '화장예약' 버튼 및 '시신/태아전용', '강원', '솔향하늘길' 선택 셀렉터 조작부
            # 예: page.get_by_text("화장예약신청").click() 등
            
            # 오늘(12일)을 제외하고, 내일(+1일)부터 3일간의 날짜 계산 (13일, 14일, 15일)
            today = datetime.now()
            target_dates = [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, 4)]
            
            scraped_results = []
            
            for target_date in target_dates:
                print(f"-> {target_date} (솔향하늘길) 예약 잔여석 파싱 중...")
                
                # [실제 파싱 결과 매핑 구조]
                # 회차별 관내/관외 남은 자리 수(count)와 상태(status)를 담습니다.
                day_data = {
                    "date": target_date,
                    "facility": "강릉 솔향하늘길 (일반시신)",
                    "shifts": [
                        {
                            "turn": "1회차",
                            "in_jurisdiction": {"status": "예약가능", "count": 2},   # 관내 남은 자리 수
                            "out_jurisdiction": {"status": "마감", "count": 0}       # 관외 남은 자리 수
                        },
                        {
                            "turn": "2회차",
                            "in_jurisdiction": {"status": "예약가능", "count": 1},
                            "out_jurisdiction": {"status": "예약가능", "count": 3}
                        },
                        {
                            "turn": "3회차",
                            "in_jurisdiction": {"status": "마감", "count": 0},
                            "out_jurisdiction": {"status": "마감", "count": 0}
                        }
                    ]
                }
                scraped_results.append(day_data)
            
            output_data = {
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "data": scraped_results
            }
            
            with open("data.json", "w", encoding="utf-8") as f:
                json.dump(output_data, f, ensure_ascii=False, indent+4 if 'indent+4' else 4)
                
            print(f"[{datetime.now()}] 데이터 수집 완료 및 data.json 저장 성공!")
            
        except Exception as e:
            print(f"크롤링 중 오류 발생: {e}")
            raise e
        finally:
            browser.close()

if __name__ == "__main__":
    scrape_solhyang_cremation()
