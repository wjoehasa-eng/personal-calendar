"""
내 캘린더 — 화면 동작 검증 (Playwright, 데스크톱 1440×900 / 휴대폰 390×844)
================================================================
시트 연결 없이(이 브라우저 저장만으로) 화면 기능 전체를 확인합니다.
· 개인 캘린더: 월간·주간·일간·일정 보기, 드래그, 공휴일, 구글 전송, 캘린더 관리
· 육아: 전환 탭 3종, 육아 홈(오늘·내일·준비물·행사), 주간 시간표, 일정 편집, 아이 이름
동기화(두 기기) 검증은 test/sync.py 를 보세요.

실행:  python3 test/verify.py
스크린샷·결과: 기본 verification/2026-11-26/ , PCAL_OUT 환경변수로 위치를 바꿀 수 있습니다.
"""
import datetime as dt
import functools, http.server, json, os, pathlib, socketserver, threading

ROOT = pathlib.Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("PCAL_PORT", "8791"))
OUT = pathlib.Path(os.environ.get("PCAL_OUT") or (ROOT / "verification" / "2026-11-26"))
OUT.mkdir(parents=True, exist_ok=True)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(("127.0.0.1", PORT), functools.partial(Quiet, directory=str(ROOT)))
threading.Thread(target=httpd.serve_forever, daemon=True).start()

from playwright.sync_api import sync_playwright

BASE = f"http://127.0.0.1:{PORT}/index.html"
TODAY = dt.date.today()


def ymd(days):
    return (TODAY + dt.timedelta(days=days)).isoformat()


CALS = [
    {"id": "personal", "name": "개인", "color": "#039be5", "visible": True, "order": 0},
    {"id": "family", "name": "가족", "color": "#33b679", "visible": True, "order": 1},
    {"id": "invest", "name": "투자", "color": "#8e24aa", "visible": True, "order": 2},
    {"id": "memo", "name": "메모", "color": "#f4511e", "visible": True, "order": 3},
]


def ev(id, title, start, end=None, allday=False, cal="personal", loc="", repeat="none"):
    return {"id": id, "title": title, "start": start, "end": end or start, "allDay": allday,
            "repeat": repeat, "calId": cal, "location": loc, "memo": "",
            "googleId": "", "updatedAt": "2026-11-25T00:00:00.000Z"}


SEED = [
    ev("e1", "테스트 회의", ymd(1) + "T10:00", ymd(1) + "T11:00", loc="회의실"),
    ev("e2", "가족 외식", ymd(2), allday=True, cal="family", loc="동네 식당"),
    ev("e3", "투자 복기", ymd(3) + "T15:00", ymd(3) + "T16:00", cal="invest"),
    ev("e4", "장보기", ymd(5), allday=True, cal="memo"),
    ev("e5", "주간 회의", ymd(4) + "T09:00", ymd(4) + "T09:30", repeat="weekly"),
    ev("e6", "병원 검진", ymd(8) + "T14:00", ymd(8) + "T15:00"),
]

# ---------------------------------------------------------------- 육아
KIDS = [
    {"id": "kid1", "name": "아이 1", "color": "#3B82F6", "updatedAt": ""},
    {"id": "kid2", "name": "아이 2", "color": "#F59E0B", "updatedAt": ""},
]


def kid_ev(eid, title, date, start="16:00", end="17:30", kids=("kid1",), cat="academy",
           supplies=None, rrule="", allday=False, end_date=""):
    return {
        "id": eid, "title": title, "childIds": list(kids), "cat": cat, "date": date,
        "endDate": end_date, "allDay": allday,
        "start": "" if allday else start, "end": "" if allday else end,
        "place": "", "memo": "",
        "supplies": [{"id": "s-%s-%d" % (eid, i), "text": t, "done": {}} for i, t in enumerate(supplies or [])],
        "rrule": rrule, "exdates": [],
        "createdAt": "2026-11-01T00:00:00.000Z", "updatedAt": "2026-11-01T00:00:00.000Z",
    }


KID_SEED = [
    kid_ev("k1", "수영", ymd(0), "10:00", "11:00", cat="academy", kids=("kid2",),
           supplies=["수영복", "물통"], rrule="FREQ=WEEKLY;BYDAY=TU,TH"),
    kid_ev("k4", "태권도", ymd(0), "16:00", "17:30", kids=("kid1",),
           supplies=["도복"]),
    kid_ev("k2", "예방접종", ymd(3), "09:30", "10:00", kids=(), cat="hospital"),
    kid_ev("k3", "체험학습 신청서", ymd(1), allday=True, kids=("kid2",), cat="supplies",
           supplies=["신청서"]),
]


def seed_kids(ctx):
    js = """
    try{
      if(!localStorage.getItem('__kseeded')){
        localStorage.setItem('pcal.kids.kids.v1', %s);
        localStorage.setItem('pcal.kids.events.v1', %s);
        localStorage.setItem('pcal.tab.v1','kidHome');
        localStorage.setItem('__kseeded','1');
      }
      localStorage.setItem('pcal_demo_purged_v1','1');
    }catch(e){}
    """ % (json.dumps(json.dumps(KIDS, ensure_ascii=False)), json.dumps(json.dumps(KID_SEED, ensure_ascii=False)))
    ctx.add_init_script(js)


def seed(ctx, events):
    js = """
    try {
      if(!localStorage.getItem('__seeded')){
        localStorage.setItem('pcal.events.v1', %s);
        localStorage.setItem('pcal.cals.v1', %s);
        localStorage.setItem('__seeded','1');
      }
      localStorage.setItem('pcal_demo_purged_v1','1');
    } catch(e) {}
    """ % (json.dumps(json.dumps(events)), json.dumps(json.dumps(CALS)))
    ctx.add_init_script(js)


report, errors, failures = {}, [], []


def step(name, fn):
    try:
        fn()
        report[name] = "ok"
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{name}: {type(exc).__name__}: {str(exc)[:300]}")
        report[name] = "FAIL"


def wire(page, tag):
    page.on("console", lambda m: errors.append(f"[{tag}.console.{m.type}] {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"[{tag}.pageerror] {e}"))
    page.on("crash", lambda: errors.append(f"[{tag}.crash]"))


with sync_playwright() as p:
    browser = p.chromium.launch(args=["--lang=ko-KR", "--disable-gpu"])

    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ko-KR",
                              timezone_id="Asia/Seoul")
    seed(ctx, SEED)
    page = ctx.new_page()
    wire(page, "pc")
    page.goto(BASE, wait_until="load")
    page.wait_for_timeout(700)

    report["title"] = page.title()
    report["month_chips"] = page.locator("#monthBody .chip").count()
    report["sync_text"] = page.inner_text("#syncText")
    page.screenshot(path=str(OUT / "01-month.png"))
    assert report["month_chips"] >= 4, f"month chips={report['month_chips']}"
    assert "이 브라우저에만 저장" in report["sync_text"], report["sync_text"]

    def quick_add():
        before = json.loads(page.evaluate("localStorage.getItem('pcal.events.v1')"))
        cell = page.locator("#monthBody .cell[data-date]").nth(20)
        box = cell.bounding_box()
        page.mouse.click(box["x"] + 14, box["y"] + box["height"] - 12)
        page.wait_for_timeout(350)
        assert page.locator("#qa").is_visible(), "quick add popup not visible"
        page.screenshot(path=str(OUT / "02-quickadd.png"))
        page.fill("#qaTitle", "빠른 등록 일정")
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        assert "빠른 등록 일정" in page.inner_text("#monthBody"), "saved event not in month"
        after = json.loads(page.evaluate("localStorage.getItem('pcal.events.v1')"))
        assert len(after) == len(before) + 1, f"{len(before)} → {len(after)}"
    step("quick_add", quick_add)

    def drag_month():
        chip = page.locator("#monthBody .chip").first
        key = chip.get_attribute("data-key")
        evid = key.split("@")[0]
        before = [e for e in json.loads(page.evaluate("localStorage.getItem('pcal.events.v1')")) if e["id"] == evid][0]
        cbox = chip.bounding_box()
        dest = page.locator("#monthBody .cell[data-date]").nth(33)
        ddate = dest.get_attribute("data-date")
        dbox = dest.bounding_box()
        page.mouse.move(cbox["x"] + 20, cbox["y"] + 9)
        page.mouse.down()
        page.mouse.move(cbox["x"] + 60, cbox["y"] + 26, steps=6)
        page.mouse.move(dbox["x"] + 40, dbox["y"] + dbox["height"] - 26, steps=14)
        page.mouse.up()
        page.wait_for_timeout(600)
        if page.locator("#confirm").is_visible():
            page.click("#cbOk")
            page.wait_for_timeout(400)
        after = [e for e in json.loads(page.evaluate("localStorage.getItem('pcal.events.v1')")) if e["id"] == evid][0]
        report["drag"] = [before["start"], after["start"], ddate]
        assert after["start"] != before["start"], "event did not move"
        page.wait_for_timeout(2700)
    step("drag_month", drag_month)

    def week_view():
        page.click('#viewTabs [data-viewtab="week"]')
        page.wait_for_timeout(600)
        report["week_tevs"] = page.locator("#tvInner .tev").count()
        report["nowline"] = page.locator(".nowline").count()
        assert report["week_tevs"] > 0
        page.screenshot(path=str(OUT / "03-week.png"))
    step("week_view", week_view)

    def date_number_day():
        # 주간 화면 날짜 머리 → 그 날 일간 보기
        page.click('#viewTabs [data-viewtab="week"]')
        page.wait_for_timeout(500)
        head = page.locator("#tvHead .tv-hcell").nth(3)
        target = head.get_attribute("data-dayview")
        head.click()
        page.wait_for_timeout(500)
        assert page.locator("#viewTime").is_visible(), "week date head did not open day view"
        assert "on" in (page.locator('#viewTabs [data-viewtab="day"]').get_attribute("class") or ""), "day tab not active"
        report["date_head_day"] = [target, page.inner_text("#htitle")]
        assert f"{int(target.split('-')[2])}일" in page.inner_text("#htitle"), page.inner_text("#htitle")
        # 일간 화면 날짜 머리 → 주간 보기
        page.click("#tvHead .tv-hcell")
        page.wait_for_timeout(500)
        assert "on" in (page.locator('#viewTabs [data-viewtab="week"]').get_attribute("class") or ""), "day head did not open week view"
        # 월간 날짜 숫자 → 그 날 일간 보기 (빠른 등록 아님)
        page.click('#viewTabs [data-viewtab="month"]')
        page.wait_for_timeout(500)
        num = page.locator("#monthBody .dnum").nth(10)
        target = num.get_attribute("data-dayview")
        num.click()
        page.wait_for_timeout(500)
        assert page.locator("#viewTime").is_visible(), "month date number did not open day view"
        assert "on" in (page.locator('#viewTabs [data-viewtab="day"]').get_attribute("class") or ""), "day tab not active after number click"
        assert not page.locator("#qa").is_visible(), "quick add opened instead of day view"
        report["date_number_day"] = [target, page.inner_text("#htitle")]
        assert f"{int(target.split('-')[2])}일" in page.inner_text("#htitle"), page.inner_text("#htitle")
        page.screenshot(path=str(OUT / "03b-date-number-day.png"))
        # 숫자 단축키: 3 월간 · 2 주간 · 4 일정 · 1 일간
        page.keyboard.press("3")
        page.wait_for_timeout(400)
        assert page.locator("#viewMonth").is_visible(), "3 key did not open month"
        page.keyboard.press("2")
        page.wait_for_timeout(400)
        assert page.locator("#viewTime").is_visible() and "on" in (page.locator('#viewTabs [data-viewtab="week"]').get_attribute("class") or ""), "2 key did not open week"
        page.keyboard.press("4")
        page.wait_for_timeout(400)
        assert page.locator("#viewSchedule").is_visible(), "4 key did not open schedule"
        page.keyboard.press("1")
        page.wait_for_timeout(400)
        assert page.locator("#viewTime").is_visible() and "on" in (page.locator('#viewTabs [data-viewtab="day"]').get_attribute("class") or ""), "1 key did not open day"
        page.keyboard.press("3")
        page.wait_for_timeout(400)
        assert page.locator("#viewMonth").is_visible()
        page.click('#viewTabs [data-viewtab="week"]')   # 다음 단계(끌어 만들기)는 주간 화면에서 시작합니다
        page.wait_for_timeout(500)
    step("date_number_day", date_number_day)

    def drag_create():
        col = page.locator("#tvInner .tday").nth(2)
        cb = col.bounding_box()
        page.mouse.move(cb["x"] + 30, cb["y"] + 48 * 10)
        page.mouse.down()
        page.mouse.move(cb["x"] + 30, cb["y"] + 48 * 11.5, steps=12)
        page.mouse.up()
        page.wait_for_timeout(500)
        assert page.locator("#modal").is_visible(), "event modal did not open"
        report["modal_times"] = [page.input_value("#mStartTime"), page.input_value("#mEndTime")]
        page.fill("#mTitle", "시간 선택 일정")
        page.screenshot(path=str(OUT / "04-event-modal.png"))
        page.click("#mSave")
        page.wait_for_timeout(500)
        assert "시간 선택 일정" in page.inner_text("#viewTime"), "saved event missing in week view"
    step("drag_create", drag_create)

    def detail_send():
        page.click('#viewTabs [data-viewtab="month"]')
        page.wait_for_timeout(500)
        page.locator("#monthBody .chip").first.click()
        page.wait_for_timeout(400)
        assert page.locator("#pop").is_visible(), "popover not visible"
        report["pop_title"] = page.inner_text("#popTitle")
        page.screenshot(path=str(OUT / "05-detail.png"))
        page.click("#popSend")
        page.wait_for_timeout(300)
        assert page.locator("#confirm").is_visible(), "confirm dialog missing"
        page.click("#cbOk")
        page.wait_for_timeout(700)
        assert page.locator("#pop").is_visible(), "popover did not reopen after send"
        assert "전송됨" in page.inner_text("#pop"), "sent badge missing"
        page.screenshot(path=str(OUT / "06-sent.png"))
        page.click("#popClose")
        page.wait_for_timeout(200)
    step("detail_send", detail_send)

    def edit_modal():
        page.locator("#monthBody .chip").first.click()
        page.wait_for_timeout(300)
        page.click("#popEdit")
        page.wait_for_timeout(400)
        assert page.locator("#modal").is_visible()
        report["edit_title"] = page.input_value("#mTitle")
        page.screenshot(path=str(OUT / "07-edit-modal.png"))
        page.click("#mCancel")
        page.wait_for_timeout(200)
        if page.locator("#pop").is_visible():
            page.click("#popClose")
    step("edit_modal", edit_modal)

    def event_done_color():
        page.locator("#monthBody .chip").first.click()
        page.wait_for_timeout(350)
        assert page.locator("#pop").is_visible(), "popover not visible"
        title = page.inner_text("#popTitle")
        # 1) 이 일정만의 색 지정
        page.click("#popEdit")
        page.wait_for_timeout(400)
        assert page.locator("#mColors [data-evcolor]").count() >= 10, "event color palette missing"
        page.click('#mColors [data-evcolor="#d50000"]')
        page.click("#mSave")
        page.wait_for_timeout(500)
        evs = json.loads(page.evaluate("localStorage.getItem('pcal.events.v1')"))
        target = [e for e in evs if e.get("title") == title][0]
        evid = target["id"]
        assert target.get("color") == "#d50000", target
        chip = page.locator(f'#monthBody .chip[data-key^="{evid}@"]').first
        assert "#d50000" in (chip.get_attribute("style") or ""), chip.get_attribute("style")
        # 2) 완료 토글 → 흑색 + ✓ + 취소선
        chip.click()
        page.wait_for_timeout(350)
        page.click("#popDone")
        page.wait_for_timeout(500)
        evs = json.loads(page.evaluate("localStorage.getItem('pcal.events.v1')"))
        target = [e for e in evs if e["id"] == evid][0]
        assert target.get("done") is True, target
        chip = page.locator(f'#monthBody .chip[data-key^="{evid}@"]').first
        style = chip.get_attribute("style") or ""
        cls = chip.get_attribute("class") or ""
        assert "#3f4042" in style, style
        assert "done" in cls, cls
        assert "✓" in chip.inner_text(), chip.inner_text()
        report["done_chip"] = [style, cls, chip.inner_text().strip()]
        page.screenshot(path=str(OUT / "07b-done.png"))
        # 3) 완료 취소 → 원래 색으로 복귀
        chip.click()
        page.wait_for_timeout(350)
        page.click("#popDone")
        page.wait_for_timeout(500)
        evs = json.loads(page.evaluate("localStorage.getItem('pcal.events.v1')"))
        target = [e for e in evs if e["id"] == evid][0]
        assert target.get("done") is False, target
        chip = page.locator(f'#monthBody .chip[data-key^="{evid}@"]').first
        assert "#d50000" in (chip.get_attribute("style") or ""), chip.get_attribute("style")
        if page.locator("#pop").is_visible():
            page.click("#popClose")
    step("event_done_color", event_done_color)

    def schedule():
        page.click('#viewTabs [data-viewtab="schedule"]')
        page.wait_for_timeout(500)
        report["schedule_items"] = page.locator("#sch .sch-item").count()
        assert report["schedule_items"] > 0
        page.screenshot(path=str(OUT / "08-schedule.png"))
    step("schedule", schedule)

    def search():
        page.fill("#q", "외식")
        page.wait_for_timeout(450)
        hits = page.locator("#searchPanel .sp-item").count()
        report["search_hits"] = hits
        assert hits > 0, "search found nothing"
        page.screenshot(path=str(OUT / "09-search.png"))
        page.locator("#searchPanel .sp-item").first.click()
        page.wait_for_timeout(600)
        if page.locator("#pop").is_visible():
            page.click("#popClose")
        page.fill("#q", "")
        page.keyboard.press("Escape")
    step("search", search)

    def holidays():
        page.goto(BASE + "?d=2026-12-20", wait_until="load")
        page.wait_for_timeout(700)
        texts = page.locator("#monthBody .hol").all_inner_texts()
        report["holiday_texts"] = texts
        page.screenshot(path=str(OUT / "10-december-holiday.png"))
        assert "기독탄신일" in " ".join(texts), "christmas holiday missing"
    step("holidays", holidays)

    def settings():
        page.goto(BASE, wait_until="load")
        page.wait_for_timeout(600)
        page.click("#settingsBtn")
        page.wait_for_timeout(300)
        assert page.locator("#settings").is_visible()
        report["settings_version"] = page.inner_text("#setVersion")
        report["settings_sync"] = page.inner_text("#setSyncInfo")
        page.click("#setRefresh")
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "11-settings.png"))
        page.click("#setClose")
    step("settings", settings)
    page.screenshot(path=str(OUT / "12-desktop-final.png"))
    ctx.close()

    # ---------------------------------------------------------------- 휴대폰
    mctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True,
                               has_touch=True, locale="ko-KR", timezone_id="Asia/Seoul")
    seed(mctx, SEED)
    mp = mctx.new_page()
    wire(mp, "mobile")
    mp.goto(BASE, wait_until="load")
    mp.wait_for_timeout(800)
    report["mobile_chips"] = mp.locator("#monthBody .chip").count()
    report["mobile_fab"] = mp.locator("#fab").is_visible()
    report["mobile_overflow_x"] = mp.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
    mp.screenshot(path=str(OUT / "13-mobile-month.png"))

    def mobile_drawer():
        mp.tap("#menuBtn")
        mp.wait_for_timeout(450)
        assert mp.evaluate("document.body.classList.contains('drawer-open')")
        mp.screenshot(path=str(OUT / "14-mobile-drawer.png"))
        mp.mouse.click(360, 420)
        mp.wait_for_timeout(400)
        assert not mp.evaluate("document.body.classList.contains('drawer-open')"), "drawer did not close"
    step("mobile_drawer", mobile_drawer)

    def mobile_week():
        mp.tap('#viewStrip [data-viewtab="week"]')
        mp.wait_for_timeout(700)
        report["mobile_week_tevs"] = mp.locator("#tvInner .tev").count()
        report["mobile_week_overflow_x"] = mp.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        mp.screenshot(path=str(OUT / "15-mobile-week.png"))
    step("mobile_week", mobile_week)

    def mobile_date_number():
        # 월간 날짜 숫자 → 일간 보기 (모바일)
        mp.tap('#viewStrip [data-viewtab="month"]')
        mp.wait_for_timeout(500)
        num = mp.locator("#monthBody .dnum").nth(8)
        target = num.get_attribute("data-dayview")
        num.tap()
        mp.wait_for_timeout(500)
        assert mp.locator("#viewTime").is_visible(), "mobile date number did not open day view"
        assert "on" in (mp.locator('#viewStrip [data-viewtab="day"]').get_attribute("class") or ""), "mobile day tab not active"
        report["mobile_date_number"] = [target, mp.inner_text("#htitle")]
        day = int(target.split("-")[2])
        head = mp.inner_text("#htitle")
        assert f"{day}일" in head or f". {day}." in head, head
        # 일간 날짜 머리 → 주간
        mp.tap("#tvHead .tv-hcell")
        mp.wait_for_timeout(500)
        assert "on" in (mp.locator('#viewStrip [data-viewtab="week"]').get_attribute("class") or ""), "mobile day head did not open week"
        # 월간으로 돌아와 일요일 숫자가 잘리지 않는지 확인
        mp.tap('#viewStrip [data-viewtab="month"]')
        mp.wait_for_timeout(500)
        geo = mp.evaluate("""()=>{
          const d=document.querySelector('#monthBody .cell:first-child .dnum').getBoundingClientRect();
          const c=document.querySelector('#monthBody .cell:first-child').getBoundingClientRect();
          return {dx:d.x, dw:d.width, cx:c.x, cw:c.width,
                  sw:document.documentElement.scrollWidth, iw:window.innerWidth};}""")
        report["mobile_sunday_dnum"] = geo
        assert geo["dx"] >= 4, geo
        assert geo["dw"] >= 24, geo
        assert geo["sw"] <= geo["iw"] + 1, geo
        mp.screenshot(path=str(OUT / "15b-mobile-month-fixed.png"))
    step("mobile_date_number", mobile_date_number)

    def mobile_quickadd():
        mp.evaluate("document.body.classList.remove('drawer-open')")
        mp.tap('#viewStrip [data-viewtab="month"]')
        mp.wait_for_timeout(500)
        cell = mp.locator("#monthBody .cell[data-date]").nth(20)
        cb = cell.bounding_box()
        mp.touchscreen.tap(cb["x"] + 10, cb["y"] + cb["height"] - 8)
        mp.wait_for_timeout(500)
        assert mp.locator("#qa").is_visible(), "mobile quick add not visible"
        mp.screenshot(path=str(OUT / "16-mobile-quickadd.png"))
        mp.fill("#qaTitle", "모바일 일정")
        mp.tap("#qaSave")
        mp.wait_for_timeout(500)
        assert "모바일 일정" in mp.inner_text("#monthBody")
    step("mobile_quickadd", mobile_quickadd)

    def mobile_modal():
        mp.tap("#fab")
        mp.wait_for_timeout(2900)
        assert mp.locator("#modal").is_visible()
        mp.screenshot(path=str(OUT / "17-mobile-modal.png"))
    step("mobile_modal", mobile_modal)
    mctx.close()

    # ---------------------------------------------------------------- 기타 동작
    ectx = browser.new_context(viewport={"width": 1280, "height": 860}, locale="ko-KR")
    seed(ectx, SEED)
    ep = ectx.new_page()
    wire(ep, "edge")
    ep.goto(BASE, wait_until="load")
    ep.wait_for_timeout(700)

    def calendar_toggle():
        before = ep.locator("#monthBody .chip").count()
        ep.click('[data-toggle="family"]')
        ep.wait_for_timeout(450)
        mid = ep.locator("#monthBody .chip").count()
        assert mid < before, "hiding a calendar did not hide its events"
        ep.click('[data-toggle="family"]')
        ep.wait_for_timeout(400)
        assert ep.locator("#monthBody .chip").count() == before, "showing a calendar did not restore events"
        report["toggle_counts"] = [before, mid]
    step("calendar_toggle", calendar_toggle)

    def add_calendar():
        ep.click("#addCalBtn")
        ep.wait_for_timeout(350)
        assert ep.locator("#calModal").is_visible()
        ep.fill("#cName", "테스트")
        ep.click('#cColors [data-color="#d50000"]')
        ep.click("#cSave")
        ep.wait_for_timeout(450)
        assert "테스트" in ep.inner_text("#calList"), "new calendar not in list"
        report["calendars"] = [c["name"] for c in json.loads(ep.evaluate("localStorage.getItem('pcal.cals.v1')"))]
    step("add_calendar", add_calendar)

    def allday_quickadd():
        cell = ep.locator("#monthBody .cell[data-date]").nth(30)
        d = cell.get_attribute("data-date")
        box = cell.bounding_box()
        ep.mouse.click(box["x"] + 12, box["y"] + box["height"] - 10)
        ep.wait_for_timeout(300)
        ep.click("#qaAllDayBtn")
        ep.fill("#qaTitle", "종일 테스트")
        ep.click("#qaSave")
        ep.wait_for_timeout(450)
        found = [x for x in json.loads(ep.evaluate("localStorage.getItem('pcal.events.v1')")) if x["title"] == "종일 테스트"]
        assert found and found[0]["allDay"] is True and found[0]["start"] == d, str(found)
        report["allday_event"] = {"start": found[0]["start"], "date": d}
    step("allday_quickadd", allday_quickadd)

    def keyboard():
        ep.keyboard.press("w")
        ep.wait_for_timeout(500)
        assert ep.locator("#viewTime").is_visible(), "W shortcut did not switch to week"
        ep.keyboard.press("m")
        ep.wait_for_timeout(400)
        ep.keyboard.press("n")
        ep.wait_for_timeout(400)
        assert ep.locator("#modal").is_visible(), "N shortcut did not open editor"
        ep.keyboard.press("Escape")
        ep.wait_for_timeout(300)
        assert not ep.locator("#modal").is_visible(), "Esc did not close editor"
    step("keyboard", keyboard)

    def edit_and_delete():
        ep.locator("#monthBody .chip").first.click()
        ep.wait_for_timeout(350)
        assert ep.locator("#pop").is_visible()
        ep.click("#popEdit")
        ep.wait_for_timeout(400)
        ep.fill("#mTitle", "수정된 일정")
        ep.fill("#mLocation", "테스트 장소")
        ep.click("#mSave")
        ep.wait_for_timeout(500)
        assert "수정된 일정" in ep.inner_text("#monthBody"), "edited title not rendered"
        before = len(json.loads(ep.evaluate("localStorage.getItem('pcal.events.v1')")))
        ep.locator("#monthBody .chip").first.click()
        ep.wait_for_timeout(350)
        ep.click("#popDel")
        ep.wait_for_timeout(350)
        assert ep.locator("#confirm").is_visible(), "delete confirm missing"
        ep.click("#cbOk")
        ep.wait_for_timeout(600)
        after = len(json.loads(ep.evaluate("localStorage.getItem('pcal.events.v1')")))
        report["delete_counts"] = [before, after]
        assert after == before - 1, "event not deleted"
    step("edit_and_delete", edit_and_delete)
    ectx.close()

    # ---------------------------------------------------------------- 육아 탭 (데스크톱)
    kctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ko-KR",
                               timezone_id="Asia/Seoul")
    seed_kids(kctx)
    kp = kctx.new_page()
    wire(kp, "kids")
    kp.goto(BASE, wait_until="load")
    kp.wait_for_timeout(800)
    report["kid_tabs"] = [kp.locator("#tabBar button").nth(i).inner_text().replace("\n", " ") for i in range(3)]
    report["kid_home_items"] = kp.locator("#kidHome .kitem").count()
    report["kid_home_sups"] = kp.locator("#kidHome .ksup").count()
    assert kp.locator("#tabBar button").count() == 3, report["kid_tabs"]
    assert kp.locator("#viewKidHome").is_visible(), "kids home not shown"
    assert report["kid_home_items"] >= 1, report["kid_home_items"]
    assert report["kid_home_sups"] >= 2, report["kid_home_sups"]
    assert kp.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1") is False
    kp.screenshot(path=str(OUT / "19-kids-home.png"))

    def kids_tabs():
        kp.click('#tabBar [data-tab="kidWeek"]')
        kp.wait_for_timeout(500)
        assert kp.locator("#viewKidWeek").is_visible(), "timetable not shown"
        report["kid_week_blocks"] = kp.locator("#kidWeek .kblk").count()
        assert report["kid_week_blocks"] > 0, "no timetable blocks"
        assert kp.inner_text("#htitle") == "시간표", kp.inner_text("#htitle")
        kp.screenshot(path=str(OUT / "20-kids-week.png"))
        kp.click('#tabBar [data-tab="cal"]')
        kp.wait_for_timeout(450)
        assert kp.locator("#viewMonth").is_visible(), "calendar not shown"
        assert kp.locator("#todayBtn").is_visible(), "calendar nav missing after tab return"
        kp.click('#tabBar [data-tab="kidHome"]')
        kp.wait_for_timeout(400)
        assert kp.locator("#viewKidHome").is_visible()
        assert kp.inner_text("#htitle") == "육아 홈", kp.inner_text("#htitle")
    step("kids_tabs", kids_tabs)

    def kids_filter():
        total = kp.locator("#kidHome .kitem").count()
        kp.click('#kidsBar [data-kidfilter="kid2"]')
        kp.wait_for_timeout(400)
        only = kp.locator("#kidHome .kitem").count()
        report["kid_filter"] = [total, only]
        assert only < total, report["kid_filter"]
        assert "수영" in kp.inner_text("#kidHome")
        assert "태권도" not in kp.inner_text("#kidHome")
        kp.click('#kidsBar [data-kidfilter="all"]')
        kp.wait_for_timeout(350)
        assert kp.locator("#kidHome .kitem").count() == total
    step("kids_filter", kids_filter)

    def kids_supply_check():
        before = kp.locator("#kidHome .ksup.on").count()
        kp.locator("#kidHome .ksup").first.click()
        kp.wait_for_timeout(450)
        after = kp.locator("#kidHome .ksup.on").count()
        report["kid_supply_check"] = [before, after]
        assert after == before + 1, report["kid_supply_check"]
        marks = kp.evaluate("JSON.parse(localStorage.getItem('pcal.kids.events.v1')).flatMap(function(e){return e.supplies||[];}).filter(function(s){return Object.keys(s.done||{}).length>0;}).length")
        assert marks >= 1, "supply check not saved"
    step("kids_supply_check", kids_supply_check)

    def kids_add_event():
        kp.click("#kidAddBtn")
        kp.wait_for_timeout(400)
        assert kp.locator("#kModal").is_visible(), "kids editor not visible"
        kp.fill("#kTitle", "발레")
        kp.fill("#kDate", ymd(0))
        kp.fill("#kStart", "15:00")
        kp.fill("#kEnd", "16:00")
        kp.fill("#kSupInput", "발레복")
        kp.click("#kSupAdd")
        kp.screenshot(path=str(OUT / "21-kids-editor.png"))
        kp.click("#kSave")
        kp.wait_for_timeout(500)
        evs = json.loads(kp.evaluate("localStorage.getItem('pcal.kids.events.v1')"))
        hit = [e for e in evs if e["title"] == "발레"]
        report["kid_added"] = {"date": hit[0]["date"], "start": hit[0]["start"], "sup": hit[0]["supplies"]} if hit else None
        assert hit, "kids event not saved"
        assert hit[0]["date"] == ymd(0) and hit[0]["start"] == "15:00", report["kid_added"]
        assert hit[0]["supplies"] and hit[0]["supplies"][0]["text"] == "발레복"
        assert "발레" in kp.inner_text("#kidHome")
    step("kids_add_event", kids_add_event)

    def kids_edit_delete():
        kp.locator("#kidHome .kitem", has_text="발레").first.click()
        kp.wait_for_timeout(400)
        assert kp.input_value("#kTitle") == "발레", kp.input_value("#kTitle")
        kp.fill("#kTitle", "발레(수정)")
        kp.click("#kSave")
        kp.wait_for_timeout(450)
        titles = [e["title"] for e in json.loads(kp.evaluate("localStorage.getItem('pcal.kids.events.v1')"))]
        assert "발레(수정)" in titles, titles
        kp.locator("#kidHome .kitem", has_text="발레(수정)").first.click()
        kp.wait_for_timeout(350)
        kp.click("#kDel")
        kp.wait_for_timeout(350)
        assert kp.locator("#confirm").is_visible(), "delete confirm missing"
        kp.click("#cbOk")
        kp.wait_for_timeout(500)
        titles = [e["title"] for e in json.loads(kp.evaluate("localStorage.getItem('pcal.kids.events.v1')"))]
        report["kid_titles_after_delete"] = titles
        assert "발레(수정)" not in titles, titles
    step("kids_edit_delete", kids_edit_delete)

    def kids_repeat():
        kp.click("#kidAddBtn")
        kp.wait_for_timeout(350)
        kp.fill("#kTitle", "축구")
        kp.fill("#kDate", ymd(0))
        kp.select_option("#kRep", "weekly")
        kp.wait_for_timeout(250)
        kp.click('#kDow [data-kd="MO"]')
        kp.click('#kDow [data-kd="WE"]')
        kp.click("#kSave")
        kp.wait_for_timeout(500)
        ev = [e for e in json.loads(kp.evaluate("localStorage.getItem('pcal.kids.events.v1')")) if e["title"] == "축구"]
        report["kid_rrule"] = ev[0]["rrule"] if ev else None
        assert ev and ev[0]["rrule"].startswith("FREQ=WEEKLY"), report["kid_rrule"]
        assert "BYDAY" in ev[0]["rrule"], report["kid_rrule"]
        kp.click('#tabBar [data-tab="kidWeek"]')
        kp.wait_for_timeout(450)
        assert "축구" in kp.inner_text("#kidWeek"), "repeat event missing in timetable"
    step("kids_repeat", kids_repeat)

    def kids_rename():
        kp.click("#settingsBtn")
        kp.wait_for_timeout(350)
        kp.fill('#setKids [data-kidname="kid1"]', "첫째")
        kp.locator('#setKids [data-kidname="kid1"]').press("Tab")
        kp.wait_for_timeout(500)
        stored = kp.evaluate("JSON.parse(localStorage.getItem('pcal.kids.kids.v1'))[0].name")
        report["kid_renamed"] = stored
        assert stored == "첫째", stored
        kp.click("#setClose")
        kp.wait_for_timeout(300)
        assert "첫째" in kp.inner_text("#kidsBar"), kp.inner_text("#kidsBar")
    step("kids_rename", kids_rename)
    kctx.close()

    # ---------------------------------------------------------------- 반복: 맞춤 규칙(요일 다중·종료일)과 수정·삭제 범위
    import calendar
    MONTH_START = (TODAY.replace(day=1) + dt.timedelta(days=32)).replace(day=1)

    def mday(n):
        return (MONTH_START + dt.timedelta(days=n - 1)).isoformat()

    BY = (0, 2, 4)   # 월·수·금 (파이썬 weekday: 월=0)
    LAST = 20
    OCCS = [n for n in range(1, LAST + 1) if (MONTH_START + dt.timedelta(days=n - 1)).weekday() in BY]
    UNTIL = (MONTH_START + dt.timedelta(days=LAST - 1)).strftime("%Y%m%d")

    rctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ko-KR",
                               timezone_id="Asia/Seoul")
    seed(rctx, [])
    rp = rctx.new_page()
    wire(rp, "repeat")
    rp.goto(BASE, wait_until="load")
    rp.wait_for_timeout(800)
    rp.click("#nextBtn")
    rp.wait_for_timeout(500)

    def rec_state():
        return json.loads(rp.evaluate("localStorage.getItem('pcal.events.v1')"))

    def rec_by_id(eid):
        return [e for e in rec_state() if e.get("id") == eid][0]

    def rec_create():
        rp.click("#createBtn")
        rp.wait_for_timeout(350)
        rp.fill("#mTitle", "맞춤 반복 일정")
        rp.fill("#mStartDate", mday(1))
        rp.fill("#mStartTime", "10:00")
        rp.fill("#mEndTime", "11:00")
        rp.select_option("#mRepeat", "custom")
        rp.wait_for_timeout(250)
        assert rp.locator("#mRepPanel").is_visible(), "repeat panel hidden"
        for wd in ("SU", "MO", "TU", "WE", "TH", "FR", "SA"):
            btn = rp.locator('#mDow [data-kd="%s"]' % wd)
            on = "on" in (btn.get_attribute("class") or "")
            if on != (wd in ("MO", "WE", "FR")):
                btn.click()
        rp.select_option("#mEndType", "until")
        rp.fill("#mUntil", mday(LAST))
        rp.wait_for_timeout(200)
        report["rec_hint"] = rp.inner_text("#mRepeatHint")
        rp.click("#mSave")
        rp.wait_for_timeout(600)
        e = [x for x in rec_state() if x.get("title") == "맞춤 반복 일정"][0]
        report["rec_rrule"] = e.get("rrule")
        assert e.get("rrule") == "FREQ=WEEKLY;BYDAY=MO,WE,FR;UNTIL=" + UNTIL, report["rec_rrule"]
        assert e.get("repeat") == "weekly", e.get("repeat")
        n = rp.locator('#monthBody .chip[data-key^="%s@"]' % e["id"]).count()
        report["rec_chips"] = [n, len(OCCS)]
        assert n == len(OCCS), report["rec_chips"]
        rp.screenshot(path=str(OUT / "26-repeat-month.png"))
    step("rec_custom_rule", rec_create)

    def rec_edit_one():
        e = [x for x in rec_state() if x.get("title") == "맞춤 반복 일정"][0]
        occ = mday(OCCS[1])
        rp.click('#monthBody .chip[data-key="%s@%s"]' % (e["id"], occ))
        rp.wait_for_timeout(300)
        rp.click("#popEdit")
        rp.wait_for_timeout(350)
        assert rp.input_value("#mStartDate") == occ, rp.input_value("#mStartDate")
        rp.fill("#mTitle", "맞춤 반복(이 일정만)")
        rp.click("#mSave")
        rp.wait_for_timeout(300)
        assert rp.is_visible("#scope"), "scope dialog missing"
        rp.click('#scopeFoot [data-scope="one"]')
        rp.wait_for_timeout(600)
        series = rec_by_id(e["id"])
        report["rec_one_exdates"] = series.get("exdates")
        assert occ in (series.get("exdates") or []), report["rec_one_exdates"]
        single = [x for x in rec_state() if x.get("title") == "맞춤 반복(이 일정만)"]
        assert len(single) == 1 and not single[0].get("rrule"), single
        assert single[0]["start"].startswith(occ), single[0]["start"]
        assert rp.locator('#monthBody .chip[data-key="%s@%s"]' % (single[0]["id"], occ)).count() == 1
        assert rp.locator('#monthBody .chip[data-key="%s@%s"]' % (e["id"], occ)).count() == 0
        rp.screenshot(path=str(OUT / "27-repeat-one.png"))
    step("rec_edit_one", rec_edit_one)

    def rec_edit_following():
        e = rec_by_id([x for x in rec_state() if x.get("title") == "맞춤 반복 일정"][0]["id"])
        occ = mday(OCCS[3])
        rp.click('#monthBody .chip[data-key="%s@%s"]' % (e["id"], occ))
        rp.wait_for_timeout(300)
        rp.click("#popEdit")
        rp.wait_for_timeout(350)
        rp.fill("#mTitle", "맞춤 반복(이후)")
        rp.click("#mSave")
        rp.wait_for_timeout(300)
        assert rp.is_visible("#scope")
        rp.click('#scopeFoot [data-scope="following"]')
        rp.wait_for_timeout(700)
        series = rec_by_id(e["id"])
        report["rec_following_rule"] = series.get("rrule")
        cut = (dt.date.fromisoformat(occ) - dt.timedelta(days=1)).strftime("%Y%m%d")
        assert "UNTIL=" + cut in (series.get("rrule") or ""), report["rec_following_rule"]
        nxt = [x for x in rec_state() if x.get("title") == "맞춤 반복(이후)"]
        assert len(nxt) == 1, nxt
        assert nxt[0]["start"].startswith(occ), nxt[0]["start"]
        assert "BYDAY=MO,WE,FR" in (nxt[0].get("rrule") or ""), nxt[0].get("rrule")
        report["rec_following_new"] = nxt[0]["id"]
    step("rec_edit_following", rec_edit_following)

    def rec_delete_one():
        eid = report["rec_following_new"]
        occ = mday(OCCS[4])
        rp.click('#monthBody .chip[data-key="%s@%s"]' % (eid, occ))
        rp.wait_for_timeout(300)
        rp.click("#popDel")
        rp.wait_for_timeout(300)
        assert rp.is_visible("#scope"), "scope dialog missing on delete"
        rp.click('#scopeFoot [data-scope="one"]')
        rp.wait_for_timeout(600)
        e = rec_by_id(eid)
        report["rec_del_one"] = e.get("exdates")
        assert occ in (e.get("exdates") or []), report["rec_del_one"]
        assert rp.locator('#monthBody .chip[data-key^="%s@"]' % eid).count() >= 1
    step("rec_delete_one", rec_delete_one)

    def rec_delete_following():
        eid = report["rec_following_new"]
        occ = mday(OCCS[5])
        rp.click('#monthBody .chip[data-key="%s@%s"]' % (eid, occ))
        rp.wait_for_timeout(300)
        rp.click("#popDel")
        rp.wait_for_timeout(300)
        rp.click('#scopeFoot [data-scope="following"]')
        rp.wait_for_timeout(700)
        cut = (dt.date.fromisoformat(occ) - dt.timedelta(days=1)).strftime("%Y%m%d")
        e = rec_by_id(eid)
        report["rec_del_following"] = e.get("rrule")
        assert "UNTIL=" + cut in (e.get("rrule") or ""), report["rec_del_following"]
    step("rec_delete_following", rec_delete_following)

    def rec_delete_all():
        sid = [x for x in rec_state() if x.get("title") == "맞춤 반복 일정"][0]["id"]
        occ = mday(OCCS[2])
        rp.click('#monthBody .chip[data-key="%s@%s"]' % (sid, occ))
        rp.wait_for_timeout(300)
        rp.click("#popDel")
        rp.wait_for_timeout(300)
        rp.click('#scopeFoot [data-scope="all"]')
        rp.wait_for_timeout(600)
        assert all(x.get("id") != sid for x in rec_state()), "series not deleted"
        assert rp.locator('#monthBody .chip[data-key^="%s@"]' % sid).count() == 0
    step("rec_delete_all", rec_delete_all)
    rctx.close()

    # ---------------------------------------------------------------- 육아 반복: 요일·종료일·수정/삭제 범위
    monday = TODAY - dt.timedelta(days=TODAY.weekday())

    def kday(n):
        return (monday + dt.timedelta(days=n)).isoformat()

    k2ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ko-KR",
                                timezone_id="Asia/Seoul")
    seed_kids(k2ctx)
    ksr = kid_ev("k9", "육아 데일리", monday.isoformat(), "15:00", "16:00", kids=("kid1",),
                 rrule="FREQ=DAILY;COUNT=30")
    k2ctx.add_init_script("try{ localStorage.setItem('pcal.kids.events.v1', %s); }catch(e){}"
                          % json.dumps(json.dumps(KID_SEED + [ksr], ensure_ascii=False)))
    kr = k2ctx.new_page()
    wire(kr, "kidrepeat")
    kr.goto(BASE, wait_until="load")
    kr.wait_for_timeout(900)
    kr.click('#tabBar [data-tab="kidWeek"]')
    kr.wait_for_timeout(600)

    def kr_state():
        return json.loads(kr.evaluate("localStorage.getItem('pcal.kids.events.v1')"))

    def kr_by_id(eid):
        return [e for e in kr_state() if e.get("id") == eid][0]

    def kids_scope_one():
        assert kr.locator('#kidWeek .kblk[data-kev="k9"]').count() >= 5, \
            kr.locator('#kidWeek .kblk[data-kev="k9"]').count()
        occ = kday(2)
        kr.click('#kidWeek .kblk[data-kev="k9"][data-kdate="%s"]' % occ)
        kr.wait_for_timeout(400)
        assert kr.locator("#kModal").is_visible()
        kr.fill("#kTitle", "육아 데일리(한번만)")
        kr.click("#kSave")
        kr.wait_for_timeout(350)
        assert kr.is_visible("#scope"), "kids scope dialog missing"
        kr.click('#scopeFoot [data-scope="one"]')
        kr.wait_for_timeout(600)
        series = kr_by_id("k9")
        report["kid_rec_one"] = series.get("exdates")
        assert occ in (series.get("exdates") or []), report["kid_rec_one"]
        single = [x for x in kr_state() if x.get("title") == "육아 데일리(한번만)"]
        assert len(single) == 1 and not single[0].get("rrule"), single
        assert single[0]["date"] == occ, single[0]["date"]
        kr.screenshot(path=str(OUT / "28-kids-repeat-one.png"))
    step("kids_rec_one", kids_scope_one)

    def kids_scope_following():
        occ = kday(4)
        kr.click('#kidWeek .kblk[data-kev="k9"][data-kdate="%s"]' % occ)
        kr.wait_for_timeout(400)
        kr.fill("#kTitle", "육아 데일리(이후)")
        kr.click("#kSave")
        kr.wait_for_timeout(350)
        assert kr.is_visible("#scope")
        kr.click('#scopeFoot [data-scope="following"]')
        kr.wait_for_timeout(700)
        series = kr_by_id("k9")
        cut = (dt.date.fromisoformat(occ) - dt.timedelta(days=1)).strftime("%Y%m%d")
        report["kid_rec_following"] = series.get("rrule")
        assert "UNTIL=" + cut in (series.get("rrule") or ""), report["kid_rec_following"]
        nxt = [x for x in kr_state() if x.get("title") == "육아 데일리(이후)"]
        assert len(nxt) == 1 and nxt[0]["date"] == occ, nxt
        report["kid_rec_new"] = nxt[0]["id"]
    step("kids_rec_following", kids_scope_following)

    def kids_scope_all():
        eid = report["kid_rec_new"]
        occ = kday(5)
        kr.click('#kidWeek .kblk[data-kev="%s"][data-kdate="%s"]' % (eid, occ))
        kr.wait_for_timeout(400)
        kr.click("#kDel")
        kr.wait_for_timeout(350)
        assert kr.is_visible("#scope"), "kids delete scope missing"
        kr.click('#scopeFoot [data-scope="all"]')
        kr.wait_for_timeout(600)
        assert all(x.get("id") != eid for x in kr_state()), "kids series not deleted"
    step("kids_rec_all", kids_scope_all)
    k2ctx.close()

    # ---------------------------------------------------------------- 육아 (휴대폰 390×844)
    kmctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                                locale="ko-KR", timezone_id="Asia/Seoul")
    seed_kids(kmctx)
    km = kmctx.new_page()
    wire(km, "kidsm")
    km.goto(BASE, wait_until="load")
    km.wait_for_timeout(900)
    report["kid_mobile_overflow"] = km.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
    report["kid_mobile_tabs"] = km.locator("#tabBar button").count()
    report["kid_mobile_items"] = km.locator("#kidHome .kitem").count()
    assert report["kid_mobile_overflow"] is False
    assert report["kid_mobile_tabs"] == 3
    assert km.inner_text("#htitle") == "육아 홈", km.inner_text("#htitle")
    km.screenshot(path=str(OUT / "22-kids-mobile-home.png"))

    def kid_mobile_week():
        km.tap('#tabBar [data-tab="kidWeek"]')
        km.wait_for_timeout(600)
        report["kid_mobile_week_overflow"] = km.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        report["kid_mobile_blocks"] = km.locator("#kidWeek .kblk").count()
        assert report["kid_mobile_week_overflow"] is False
        km.screenshot(path=str(OUT / "23-kids-mobile-week.png"))
    step("kid_mobile_week", kid_mobile_week)

    def kid_mobile_modal():
        km.tap("#kidAddBtn")
        km.wait_for_timeout(600)
        assert km.locator("#kModal").is_visible()
        box = km.locator("#kModal .modal-card").bounding_box()
        report["kid_mobile_modal_box"] = {"w": round(box["width"]), "h": round(box["height"])}
        assert box["width"] <= 390
        km.screenshot(path=str(OUT / "24-kids-mobile-editor.png"))
        km.tap("#kCancel")
        km.wait_for_timeout(300)
        km.tap('#tabBar [data-tab="cal"]')
        km.wait_for_timeout(500)
        report["kid_mobile_cal_overflow"] = km.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        assert report["kid_mobile_cal_overflow"] is False
        km.screenshot(path=str(OUT / "25-kids-mobile-calendar.png"))
    step("kid_mobile_modal", kid_mobile_modal)
    kmctx.close()

    # ---------------------------------------------------------------- 처음 여는 화면
    nctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ko-KR")
    nctx.add_init_script("localStorage.setItem('pcal_demo_purged_v1','1');")
    np = nctx.new_page()
    wire(np, "empty")
    np.goto(BASE, wait_until="load")
    np.wait_for_timeout(700)
    report["empty_chips"] = np.locator("#monthBody .chip").count()
    report["empty_cells"] = np.locator("#monthBody .cell").count()
    np.screenshot(path=str(OUT / "18-empty.png"))
    assert report["empty_cells"] > 27, "empty month did not render"
    nctx.close()

    browser.close()

report["errors"] = errors
report["failures"] = failures
print(json.dumps(report, ensure_ascii=False, indent=2))
with open(OUT / "verify-report.json", "w") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
if failures or errors:
    raise SystemExit(1)
