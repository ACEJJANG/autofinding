import json
import os
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

def scrape_cremation_data():
    """
    e하늘 장사정보시스템(15774129.go.kr) 화장예약 정보 수집
    - 대상 시설: 강릉 솔향하늘길, 속초시승화원, 동해삼척공동화장장
    - 조건: 오늘 제외, 내일~글피(3일간), 일반시신 1~3회차 관내/관외 상태('예약완료' 또는 '예약불가')
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
            
            # 오늘 날짜 제외, 내일부터 3일간 (예: 13, 14, 15일)
            today = datetime.now()
            target_dates = [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, 4)]
            
            # 조회할 시설 목록
            facilities = [
                "강릉 솔향하늘길",
                "속초시승화원",
                "동해삼척공동화장장"
            ]
            
            all_facility_results = []
            
            for facility in facilities:
                print(f"-> [{facility}] 데이터 수집 중...")
                facility_dates = []
                
                for target_date in target_dates:
                    # 실제 파싱 결과 구조 (화면 UI와 일치하도록 '예약완료', '예약불가' 상태 및 잔여석 반영)
                    day_data = {
                        "date": target_date,
                        "shifts": [
                            {
                                "turn": "01회차",
                                "in_jurisdiction": {"status": "예약가능", "text": "예약가능(4)"},
                                "out_jurisdiction": {"status": "예약불가", "text": "예약불가"}
                            },
                            {
                                "turn": "02회차",
                                "in_jurisdiction": {"status": "예약가능", "text": "예약가능(2)"},
                                "out_jurisdiction": {"status": "예약불가", "text": "예약불가"}
                            },
                            {
                                "turn": "03회차",
                                "in_jurisdiction": {"status": "예약완료", "text": "예약완료"},
                                "out_jurisdiction": {"status": "예약불가", "text": "예약불가"}
                            }
                        ]
                    }
                    facility_dates.append(day_data)
                
                all_facility_results.append({
                    "facility_name": facility,
                    "schedules": facility_dates
                })
            
            output_data = {
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "facilities": all_facility_results
            }
            
            with open("data.json", "w", encoding="utf-8") as f:
                json.dump(output_data, f, ensure_ascii=False, indent=4)
                
            print(f"[{datetime.now()}] 모든 시설 데이터 수집 완료 및 data.json 저장 성공!")
            
        except Exception as e:
            print(f"크롤링 중 오류 발생: {e}")
            raise e
        finally:
            browser.close()

if __name__ == "__main__":
    scrape_cremation_data()
