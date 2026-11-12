"""
e하늘 장사정보시스템(15774129.go.kr) 화장예약 현황 수집기

원칙
 1) 값을 코드에 박아 넣지 않는다. 화면(팝업 '화장예약현황상세')의 표를 읽은 값만 저장한다.
 2) 조회 실패/검증 실패 시 data.json 을 덮어쓰지 않는다(옛 데이터 유지 + 실패 표시).
 3) 날짜는 한국시간(KST) 기준. (GitHub Actions 서버는 UTC 라서 반드시 필요)
 4) 시설마다 팝업 제목에 시설명이 있는지 확인해서 다른 시설 데이터가 섞이지 않게 한다.
 5) 구분(관내/관외)은 체크박스를 실제로 바꾸고 새로고침한 뒤, 체크 상태까지 검증한다.
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
SITE_URL = "https://15774129.go.kr/"
REGION = "강원"
DAYS = 3                      # 오늘 포함 며칠
SCOPES = ["관내", "관외"]     # 인접이 필요하면 "인접" 추가
DEBUG_DIR = "debug"

# 이름: 팝업 제목에 들어있어야 하는 키워드(여러 표기 대응)
# 실제 사이트(화장시설현황 > 강원) 목록의 시설명 그대로. row = 목록 행을 찾는 글자
FACILITIES = [
    {"name": "강릉 솔향하늘길",   "row": "솔향하늘길",       "keys": ["솔향하늘길"]},
    {"name": "속초시승화원",       "row": "속초시승화원",     "keys": ["속초"]},
    {"name": "동해삼척공동화장장", "row": "동해삼척 공동화장장", "keys": ["동해", "삼척"]},
]
LIST_URL = "https://15774129.go.kr/new/esky_p/esky_index.do#MENU:M210900000"  # 화장시설현황(예약현황 및 구비서류)

# ───────────────────────── 순수 파싱 함수 (브라우저 없이 테스트 가능) ─────────────────────────
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
TIME_RE = re.compile(r"(\d{2}:\d{2})\s*~\s*(\d{2}:\d{2})")
TURN_RE = re.compile(r"(\d+)\s*회차")


def parse_cell(text):
    """'08:00~10:00\n예약가능(4)' -> dict"""
    t = re.sub(r"\s+", " ", text or "").strip()
    m = TIME_RE.search(t)
    time = f"{m.group(1)}~{m.group(2)}" if m else None
    rest = TIME_RE.sub("", t).strip()
    if "예약가능" in rest:
        r = re.search(r"예약가능\s*\((\d+)\)", rest)
        return {"status": "예약가능", "remaining": int(r.group(1)) if r else None,
                "text": rest, "time": time}
    for s in ("예약완료", "예약불가", "마감"):
        if s in rest:
            return {"status": "예약불가" if s == "마감" else s, "remaining": 0,
                    "text": rest, "time": time}
    return {"status": "확인필요", "remaining": None, "text": rest, "time": time}


def parse_table_payload(payload):
    """
    payload = {"dates": ["2026-11-12 목요일", ...], "rows": [{"turn": "01회차", "cells": [...]}, ...]}
    -> {날짜: {회차번호: cell_dict}}
    """
    dates = []
    for d in payload["dates"]:
        m = DATE_RE.search(d)
        if not m:
            raise ValueError(f"날짜 헤더를 읽을 수 없음: {d!r}")
        dates.append(m.group(0))
    if not dates:
        raise ValueError("날짜 헤더가 없음")

    out = {d: {} for d in dates}
    for row in payload["rows"]:
        tm = TURN_RE.search(row["turn"])
        if not tm:
            continue
        turn = int(tm.group(1))
        cells = row["cells"]
        if len(cells) != len(dates):
            raise ValueError(f"{row['turn']}: 칸 수({len(cells)})와 날짜 수({len(dates)}) 불일치")
        for d, c in zip(dates, cells):
            out[d][turn] = parse_cell(c)
    if not any(out.values()):
        raise ValueError("회차 행이 하나도 없음")
    return out


def build_schedules(scope_tables, target_dates):
    """scope_tables = {'관내': {날짜:{회차:cell}}, '관외': {...}} -> 화면용 구조"""
    schedules = []
    for d in target_dates:
        turns = set()
        for tbl in scope_tables.values():
            turns |= set(tbl.get(d, {}).keys())
        if not turns:
            raise ValueError(f"{d} 데이터가 표에 없음")
        shifts = []
        for t in sorted(turns):
            scopes, time = {}, None
            for sc, tbl in scope_tables.items():
                cell = tbl.get(d, {}).get(t)
                if cell:
                    time = time or cell["time"]
                    scopes[sc] = {k: cell[k] for k in ("status", "remaining", "text")}
                else:
                    scopes[sc] = {"status": "확인필요", "remaining": None, "text": "데이터 없음"}
            shifts.append({"turn": f"{t:02d}회차", "time": time, "scopes": scopes})
        schedules.append({"date": d, "shifts": shifts})
    return schedules


# ───────────────────────── 브라우저 조작 ─────────────────────────
# 팝업(모달) 루트 찾기: '화장예약현황상세' 글자를 가진 요소에서 위로 올라가며 '회차' 표가 있는 조상
JS_MODAL = """
function findModal(){
  const all=[...document.querySelectorAll('*')].filter(e=>e.children.length===0 && (e.textContent||'').includes('화장예약현황상세'));
  for(const h of all){
    let n=h;
    while(n && n!==document.body){
      if(n.querySelector && [...n.querySelectorAll('table')].some(t=>/\\d+\\s*회차/.test(t.innerText))) return n;
      n=n.parentElement;
    }
  }
  return null;
}
"""

JS_READ_TABLE = JS_MODAL + """
() => {
  const m=findModal(); if(!m) return null;
  const t=[...m.querySelectorAll('table')].find(t=>/\\d+\\s*회차/.test(t.innerText));
  const trs=[...t.querySelectorAll('tr')];
  const head=trs.find(r=>/\\d{4}-\\d{2}-\\d{2}/.test(r.innerText));
  const dates=[...head.querySelectorAll('th,td')].map(c=>c.innerText.trim()).filter(x=>/\\d{4}-\\d{2}-\\d{2}/.test(x));
  const rows=[];
  for(const r of trs){
    const cells=[...r.querySelectorAll('th,td')].map(c=>c.innerText.trim());
    if(cells.length && /^\\d+\\s*회차$/.test(cells[0])) rows.push({turn:cells[0], cells:cells.slice(1)});
  }
  const hm=(m.innerText.match(/\\d{4}년\\s*\\d+월\\s*\\d+일\\s*\\d+시\\s*\\d+분\\s*\\d+초[^\\n]*/)||[''])[0];
  return {dates, rows, title:m.innerText.slice(0,300), stamp:hm};
}
"""

JS_SCOPE_STATE = JS_MODAL + """
(labels) => {
  const m=findModal(); if(!m) return null;
  const res={};
  for(const l of labels){
    const el=[...m.querySelectorAll('label,span,a,div')].find(e=>e.children.length===0 && e.textContent.trim()===l);
    let inp=null;
    if(el){ inp=(el.closest('label')&&el.closest('label').querySelector('input'))
          || (el.previousElementSibling&&el.previousElementSibling.matches&&el.previousElementSibling.matches('input')&&el.previousElementSibling)
          || (el.parentElement&&el.parentElement.querySelector('input')); }
    res[l]= inp ? !!inp.checked : null;
  }
  return res;
}
"""


def save_debug(page, tag):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    safe = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", tag)
    try:
        page.screenshot(path=f"{DEBUG_DIR}/{safe}.png", full_page=True)
        with open(f"{DEBUG_DIR}/{safe}.html", "w", encoding="utf-8") as f:
            f.write(page.content())
    except Exception as e:  # noqa
        print("디버그 저장 실패:", e)


def open_facility_modal(page, fac):
    """강원 탭 -> 시설 -> 일반시신 예약현황 팝업 열기. 사이트 구조가 바뀌면 여기만 고치면 된다."""
    # 확인된 경로: 화장시설현황 화면 -> 지역 '강원' 버튼 -> 시설 행의 [예약 현황] 버튼 -> 팝업
    page.goto(LIST_URL, timeout=60000)
    page.wait_for_load_state("networkidle")

    page.get_by_role("button", name=re.compile(rf"^{REGION}")).first.click()
    page.wait_for_timeout(1000)

    # 목록이 강원 시설로 바뀌었는지 확인 (다른 지역 행을 누르는 사고 방지)
    row = page.locator("tr", has_text=fac["row"]).first
    row.wait_for(timeout=15000)
    if "강원" not in row.inner_text():
        raise RuntimeError("강원 목록으로 바뀌지 않음")

    # 버튼 글자가 '예약\n현황' 처럼 줄바꿈되어 있어 정규식으로 찾는다
    row.get_by_text(re.compile(r"예약\s*현황")).first.click()

    page.get_by_text("화장예약현황상세").first.wait_for(timeout=20000)
    page.wait_for_timeout(1000)


def set_scope(page, scope):
    """구분 체크박스를 scope 하나만 켜고 새로고침. 상태까지 검증."""
    state = page.evaluate(JS_SCOPE_STATE, ["관내", "인접", "관외"])
    if state is None:
        raise RuntimeError("팝업을 찾을 수 없음")
    for label, checked in state.items():
        want = (label == scope)
        if checked is None:
            if label == scope:
                raise RuntimeError(f"'{label}' 체크박스를 찾을 수 없음")
            continue
        if checked != want:
            page.get_by_text(label, exact=True).last.click()
            page.wait_for_timeout(200)
    page.get_by_text("새로고침", exact=False).last.click()
    page.wait_for_timeout(1500)
    page.wait_for_load_state("networkidle")

    state = page.evaluate(JS_SCOPE_STATE, ["관내", "인접", "관외"])
    for label, checked in state.items():
        if checked is None:
            continue
        if checked != (label == scope):
            raise RuntimeError(f"구분 설정 실패: {state}")


def read_table_with_paging(page, target_dates):
    """target_dates 가 모두 보일 때까지 '5일 후' 를 눌러 합친다."""
    merged, stamp = {}, None
    for _ in range(4):
        payload = page.evaluate(JS_READ_TABLE)
        if not payload:
            raise RuntimeError("예약표를 읽을 수 없음")
        stamp = payload["stamp"] or stamp
        part = parse_table_payload(payload)
        merged.update(part)
        if all(d in merged for d in target_dates):
            return merged, stamp, payload["title"]
        page.get_by_text("5일 후", exact=False).last.click()
        page.wait_for_timeout(1500)
    raise RuntimeError(f"날짜를 찾지 못함: {[d for d in target_dates if d not in merged]}")


def scrape_facility(page, fac, target_dates):
    open_facility_modal(page, fac)
    if not page.get_by_text("일반시신").count():
        raise RuntimeError("일반시신 항목을 찾을 수 없음")

    scope_tables, stamps = {}, []
    for sc in SCOPES:
        set_scope(page, sc)
        tbl, stamp, title = read_table_with_paging(page, target_dates)
        if not any(k in title for k in fac["keys"]):
            raise RuntimeError(f"다른 시설 팝업으로 보임(제목: {title[:40]!r})")
        scope_tables[sc] = tbl
        stamps.append(stamp)
        # 다음 구분 조회를 위해 첫 5일 화면으로 복귀
        if sc != SCOPES[-1]:
            for _ in range(3):
                btn = page.get_by_text("5일 전", exact=False)
                if btn.count():
                    btn.last.click()
                    page.wait_for_timeout(500)

    return {"schedules": build_schedules(scope_tables, target_dates),
            "source_time": next((s for s in stamps if s), None)}


def main():
    from playwright.sync_api import sync_playwright

    now = datetime.now(KST)
    target_dates = [(now + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(DAYS)]

    # 이전 데이터(실패 시 유지용)
    old = {}
    if os.path.exists("data.json"):
        try:
            old = {f["facility_name"]: f for f in json.load(open("data.json", encoding="utf-8")).get("facilities", [])}
        except Exception:
            old = {}

    results, ok_count = [], 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 1000},
            locale="ko-KR", timezone_id="Asia/Seoul",
        )
        # 나중에 직접 API 호출로 바꿀 수 있도록 네트워크 주소를 기록
        net = []
        context.on("response", lambda r: net.append(f"{r.status} {r.request.method} {r.url}")
                   if r.request.resource_type in ("xhr", "fetch") else None)

        for fac in FACILITIES:
            page = context.new_page()
            print(f"[{datetime.now(KST):%H:%M:%S}] {fac['name']} 조회 중...")
            try:
                data = scrape_facility(page, fac, target_dates)
                results.append({"facility_name": fac["name"], "ok": True, "error": None,
                                "fetched_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), **data})
                ok_count += 1
            except Exception as e:
                print(f"  ✗ 실패: {e}", file=sys.stderr)
                save_debug(page, fac["name"])
                prev = old.get(fac["facility_name"], {})
                results.append({"facility_name": fac["name"], "ok": False, "error": str(e)[:200],
                                "fetched_at": prev.get("fetched_at"),
                                "source_time": prev.get("source_time"),
                                "schedules": prev.get("schedules", [])})   # 옛 값 유지(화면에서 '조회실패'로 표시)
            finally:
                page.close()

        if net:
            os.makedirs(DEBUG_DIR, exist_ok=True)
            open(f"{DEBUG_DIR}/network.log", "w", encoding="utf-8").write("\n".join(net))
        browser.close()

    if ok_count == 0:
        print("모든 시설 조회 실패 → data.json 을 갱신하지 않습니다.", file=sys.stderr)
        sys.exit(1)

    out = {"updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
           "target_dates": target_dates, "facilities": results}
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"완료: {ok_count}/{len(FACILITIES)}개 시설 성공")


if __name__ == "__main__":
    main()
