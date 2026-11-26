"""
내 캘린더 — 화면 동작 검증 (Playwright, 데스크톱 1440×900 / 휴대폰 390×844)
================================================================
시트 연결 없이(이 브라우저 저장만으로) 화면 기능 전체를 확인합니다.
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
        page.click("#viewBtn")
        page.wait_for_timeout(150)
        page.click('#viewMenu [data-view="week"]')
        page.wait_for_timeout(600)
        report["week_tevs"] = page.locator("#tvInner .tev").count()
        report["nowline"] = page.locator(".nowline").count()
        assert report["week_tevs"] > 0
        page.screenshot(path=str(OUT / "03-week.png"))
    step("week_view", week_view)

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
        page.click("#viewBtn")
        page.wait_for_timeout(120)
        page.click('#viewMenu [data-view="month"]')
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

    def schedule():
        page.click("#viewBtn")
        page.wait_for_timeout(120)
        page.click('#viewMenu [data-view="schedule"]')
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
        mp.tap("#viewBtn")
        mp.wait_for_timeout(200)
        mp.tap('#viewMenu [data-view="week"]')
        mp.wait_for_timeout(700)
        report["mobile_week_tevs"] = mp.locator("#tvInner .tev").count()
        report["mobile_week_overflow_x"] = mp.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        mp.screenshot(path=str(OUT / "15-mobile-week.png"))
    step("mobile_week", mobile_week)

    def mobile_quickadd():
        mp.evaluate("document.body.classList.remove('drawer-open')")
        mp.tap("#viewBtn")
        mp.wait_for_timeout(150)
        mp.tap('#viewMenu [data-view="month"]')
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
