import json
import os
from datetime import datetime, timedelta

def generate_cremation_data():
    """
    강원권 주요 화장시설 (강릉 솔향하늘길, 속초시승화원, 동해삼척공동화장장)
    실시간 예약 현황 데이터 구조 생성기
    """
    today = datetime.now()
    target_dates = [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(3)]
    
    facilities = [
        {
            "facility_name": "강릉 솔향하늘길",
            "url": "https://15774129.go.kr/"
        },
        {
            "facility_name": "속초시승화원",
            "url": "https://15774129.go.kr/"
        },
        {
            "facility_name": "동해삼척공동화장장",
            "url": "https://15774129.go.kr/"
        }
    ]
    
    all_facility_results = []
    
    for fac in facilities:
        facility_dates = []
        for target_date in target_dates:
            # 기본 예시 데이터 구조 (실시간 정확한 확인을 위한 링크 및 상태 포함)
            day_data = {
                "date": target_date,
                "shifts": [
                    {
                        "turn": "01회차",
                        "in_jurisdiction": {"status": "예약가능", "text": "예약가능"},
                        "out_jurisdiction": {"status": "예약불가", "text": "예약불가"}
                    },
                    {
                        "turn": "02회차",
                        "in_jurisdiction": {"status": "예약가능", "text": "예약가능"},
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
            "facility_name": fac["facility_name"],
            "direct_url": fac["url"],
            "schedules": facility_dates
        })
    
    output_data = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "facilities": all_facility_results
    }
    
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=4)
        
    print("데이터 파일 생성 완료")

if __name__ == "__main__":
    generate_cremation_data()
