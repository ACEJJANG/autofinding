"""
e하늘 장사정보시스템(15774129.go.kr) 화장예약 현황 수집기

원칙
 1) 값을 코드에 박아 넣지 않는다. 팝업(화장예약현황상세)의 표를 읽은 값만 저장한다.
 2) 조회 실패/검증 실패 시 data.json 을 덮어쓰지 않는다(옛 데이터 유지 + 실패 표시).
 3) 날짜는 한국시간(KST) 기준.
 4) 팝업 제목 "2026년 11월 12일 10시 11분 58초(일반시신-관외)" 에서 유형·구분을 읽어
    우리가 요청한 것(일반시신 / 관내·관외)과 같은지 반드시 검증한다.
 5) 팝업이 같은 화면의 레이어든, iframe 이든, 새 창이든 모든 프레임을 뒤져서 찾는다.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
LIST_URL = "https://15774129.go.kr/new/esky_p/esky_index.do#MENU:M210900000"  # 화장시설현황
REGION = "강원"
DAYS = 3                      # 오늘 포함 며칠
SCOPES = ["관내", "관외"]
DEBUG_DIR = "debug"

# row: 시설 목록 행에서 찾을 글자 / keys: (참고) 팝업 안에 시설명이 있으면 확인
FACILITIES = [
    {"name": "강릉 솔향하늘길",   "row": "솔향하늘길",         "keys": ["솔향하늘길"]},
    {"name": "속초시승화원",       "row": "속초시승화원",       "keys": ["속초"]},
    {"name": "동해삼척공동화장장", "row": "동해삼척 공동화장장", "keys": ["동해", "삼척"]},
]

# ───────────────────────── 순수 파싱 (브라우저 없이 테스트 가능) ─────────────────────────
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
TIME_RE = re.compile(r"(\d{2}:\d{2})\s*~\s*(\d{2}:\d{2})")
TURN_RE = re.compile(r"(\d+)\s*회차")
STAMP_RE = re.compile(r"(\d{4})년\s*(\d+)월\s*(\d+)일\s*(\d+)시\s*(\d+)분\s*(\d+)초\s*\(\s*([^)\-]+)-([^)]+)\)")


def parse_stamp(text):
    """'2026년 11월 12일 10시 11분 58초(일반시신-관외)' -> ('2026-11-12 10:11:58', '일반시신', '관외')"""
    m = STAMP_RE.search(text or "")
    if not m:
        return None
    y, mo, d, h, mi, s = (int(x) for x in m.groups()[:6])
    return f"{y:04d}-{mo:02d}-{d:02d} {h:02d}:{mi:02d}:{s:02d}", m.group(7).strip(), m.group(8).strip()


def parse_cell(text):
    t = re.sub(r"\s+", " ", text or "").strip()
    m = TIME_RE.search(t)
    time_ = f"{m.group(1)}~{m.group(2)}" if m else None
    rest = TIME_RE.sub("", t).strip()
    if "예약가능" in rest:
        r = re.search(r"예약가능\s*\((\d+)\)", rest)
        return {"status": "예약가능", "remaining": int(r.group(1)) if r else None, "text": rest, "time": time_}
    for s in ("예약완료", "예약불가", "취소후대기", "취소 후 대기"):
        if s in rest:
            return {"status": s.replace(" ", ""), "remaining": 0, "text": rest, "time": time_}
    return {"status": "확인필요", "remaining": None, "text": rest, "time": time_}


def parse_table_payload(payload):
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
        if len(row["cells"]) != len(dates):
            raise ValueError(f"{row['turn']}: 칸 수({len(row['cells'])})와 날짜 수({len(dates)}) 불일치")
        for d, c in zip(dates, row["cells"]):
            out[d][turn] = parse_cell(c)
    if not any(out.values()):
        raise ValueError("회차 행이 하나도 없음")
    return out


def build_schedules(scope_tables, target_dates):
    schedules = []
    for d in target_dates:
        turns = set()
        for tbl in scope_tables.values():
            turns |= set(tbl.get(d, {}).keys())
        if not turns:
            raise ValueError(f"{d} 데이터가 표에 없음")
        shifts = []
        for t in sorted(turns):
            scopes, time_ = {}, None
            for sc, tbl in scope_tables.items():
                cell = tbl.get(d, {}).get(t)
                if cell:
                    time_ = time_ or cell["time"]
                    scopes[sc] = {k: cell[k] for k in ("status", "remaining", "text")}
                else:
                    scopes[sc] = {"status": "확인필요", "remaining": None, "text": "데이터 없음"}
            shifts.append({"turn": f"{t:02d}회차", "time": time_, "scopes": scopes})
        schedules.append({"date": d, "shifts": shifts})
    return schedules


# ───────────────────────── 브라우저 쪽 JS ─────────────────────────
# 주의: Playwright 는 문자열이 '하나의 함수식'이어야 함수로 실행한다. 그래서 헬퍼는 함수 안에 넣는다.
JS_FIND = """
function findModal(){
  const hs=[...document.querySelectorAll('*')].filter(e=>e.children.length===0 && (e.textContent||'').includes('화장예약현황상세'));
  for(const h of hs){
    let n=h;
    while(n && n!==document.body){
      if([...n.querySelectorAll('table')].some(t=>/\\d+\\s*회차/.test(t.innerText))) return n;
      n=n.parentElement;
    }
  }
  // 팝업이 iframe 안에 있어 제목이 바깥에 있는 경우
  if([...document.querySelectorAll('table')].some(t=>/\\d+\\s*회차/.test(t.innerText))) return document.body;
  return null;
}
"""

JS_HAS_MODAL = "() => {" + JS_FIND + " return !!findModal(); }"

JS_READ_TABLE = "() => {" + JS_FIND + """
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
  return {dates, rows, text:m.innerText.slice(0,1500)};
}"""

JS_SCOPE_STATE = "(labels) => {" + JS_FIND + """
  const m=findModal(); if(!m) return null;
  const res={};
  for(const l of labels){
    const el=[...m.querySelectorAll('label,span,a,div,li')].find(e=>e.children.length===0 && e.textContent.trim()===l);
    let inp=null;
    if(el){ inp=(el.closest('label')&&el.closest('label').querySelector('input'))
          || (el.previousElementSibling&&el.previousElementSibling.matches&&el.previousElementSibling.matches('input')&&el.previousElementSibling)
          || (el.parentElement&&el.parentElement.querySelector('input')); }
    res[l]= inp ? !!inp.checked : null;
  }
  return res;
}"""


# ───────────────────────── 브라우저 조작 ─────────────────────────
def all_frames(context):
    for pg in context.pages:
        for fr in pg.frames:
            yield fr


def find_modal_frame(context, timeout=20):
    """팝업이 있는 프레임(같은 화면/iframe/새 창 모두)을 찾는다."""
    end = time.time() + timeout
    while time.time() < end:
        for fr in all_frames(context):
            try:
                if fr.evaluate(JS_HAS_MODAL):
                    return fr
            except Exception:
                pass
        time.sleep(0.4)
    raise RuntimeError("예약현황 팝업을 찾지 못함")


def save_debug(context, tag):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    safe = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", tag)
    for i, pg in enumerate(context.pages):
        try:
            pg.screenshot(path=f"{DEBUG_DIR}/{safe}_{i}.png", full_page=True)
            for j, fr in enumerate(pg.frames):
                open(f"{DEBUG_DIR}/{safe}_{i}_{j}.html", "w", encoding="utf-8").write(fr.content())
        except Exception as e:  # noqa
            print("디버그 저장 실패:", e)


def open_facility_modal(page, fac):
    """화장시설현황 -> 강원 -> 시설 행의 [예약 현황] -> 팝업"""
    page.goto(LIST_URL, timeout=60000)
    page.wait_for_load_state("networkidle")

    page.get_by_role("button", name=re.compile(rf"^{REGION}")).first.click()
    page.wait_for_timeout(1000)

    row = page.locator("tr", has_text=fac["row"]).first
    row.wait_for(timeout=15000)
    if REGION not in row.inner_text():
        raise RuntimeError("강원 목록으로 바뀌지 않음")

    row.get_by_text(re.compile(r"예약\s*현황")).first.click()
    return find_modal_frame(page.context)


def set_scope(fr, scope):
    """구분 체크박스를 scope 하나만 켜고 새로고침. 상태까지 검증."""
    labels = ["관내", "인접", "관외"]
    state = fr.evaluate(JS_SCOPE_STATE, labels)
    if state is None:
        raise RuntimeError("팝업을 찾을 수 없음")
    if state.get(scope) is None:
        raise RuntimeError(f"'{scope}' 체크박스를 찾을 수 없음: {state}")
    for label, checked in state.items():
        if checked is None:
            continue
        if checked != (label == scope):
            fr.get_by_text(label, exact=True).last.click()
            fr.wait_for_timeout(200)

    state = fr.evaluate(JS_SCOPE_STATE, labels)
    for label, checked in state.items():
        if checked is not None and checked != (label == scope):
            raise RuntimeError(f"구분 설정 실패: {state}")

    fr.get_by_text("새로고침", exact=False).last.click()
    fr.wait_for_timeout(1500)


def read_table_with_paging(fr, scope, target_dates):
    merged, stamp = {}, None
    for _ in range(4):
        payload = fr.evaluate(JS_READ_TABLE)
        if not payload:
            raise RuntimeError("예약표를 읽을 수 없음")
        st = parse_stamp(payload["text"])
        if not st:
            raise RuntimeError("팝업 제목(조회 시각·유형·구분)을 읽을 수 없음")
        stamp, kind, got_scope = st
        # 핵심 검증: 화면에 실제로 떠 있는 표가 우리가 요청한 '일반시신-구분' 인지
        if kind != "일반시신" or got_scope != scope:
            raise RuntimeError(f"요청({scope})과 다른 표가 읽힘: {kind}-{got_scope}")
        merged.update(parse_table_payload(payload))
        if all(d in merged for d in target_dates):
            return merged, stamp
        fr.get_by_text("5일 후", exact=False).last.click()
        fr.wait_for_timeout(1500)
    raise RuntimeError(f"날짜를 찾지 못함: {[d for d in target_dates if d not in merged]}")


def go_first_page(fr):
    for _ in range(3):
        btn = fr.get_by_text("5일 전", exact=False)
        if btn.count():
            btn.last.click()
            fr.wait_for_timeout(600)


def scrape_facility(page, fac, target_dates):
    fr = open_facility_modal(page, fac)
    scope_tables, stamps = {}, []
    for sc in SCOPES:
        go_first_page(fr)
        set_scope(fr, sc)
        tbl, stamp = read_table_with_paging(fr, sc, target_dates)
        scope_tables[sc] = tbl
        stamps.append(stamp)
    return {"schedules": build_schedules(scope_tables, target_dates),
            "source_time": min(stamps)}   # e하늘 화면 기준 가장 오래된 조회 시각


def main(list_url=None, headless=True, chrome_path=None):
    from playwright.sync_api import sync_playwright
    global LIST_URL
    if list_url:
        LIST_URL = list_url

    now = datetime.now(KST)
    target_dates = [(now + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(DAYS)]

    old = {}
    if os.path.exists("data.json"):
        try:
            old = {f["facility_name"]: f for f in json.load(open("data.json", encoding="utf-8")).get("facilities", [])}
        except Exception:
            old = {}

    results, ok_count = [], 0
    with sync_playwright() as p:
        kw = {"executable_path": chrome_path} if chrome_path else {}
        browser = p.chromium.launch(headless=headless, args=["--no-sandbox", "--disable-setuid-sandbox"], **kw)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 1000}, locale="ko-KR", timezone_id="Asia/Seoul")
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
                save_debug(context, fac["name"])
                prev = old.get(fac["name"], {})
                results.append({"facility_name": fac["name"], "ok": False, "error": str(e)[:200],
                                "fetched_at": prev.get("fetched_at"), "source_time": prev.get("source_time"),
                                "schedules": prev.get("schedules", [])})
            finally:
                for pg in list(context.pages):      # 새 창으로 열린 팝업까지 정리
                    pg.close()

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
