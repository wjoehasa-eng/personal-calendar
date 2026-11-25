import json, threading, functools, http.server, socketserver, pathlib, traceback

ROOT = pathlib.Path(__file__).resolve().parents[3]
PORT = 8791
OUT = ROOT / "verification" / "2026-11-25-personal-calendar"
OUT.mkdir(parents=True, exist_ok=True)

class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass

def serve():
    handler = functools.partial(Quiet, directory=str(ROOT))
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

serve()
from playwright.sync_api import sync_playwright

BASE = f"http://127.0.0.1:{PORT}/work/personal-calendar/index.html"
URL = BASE + "?demo=1"
report, errors, failures = {}, [], []

def step(name, fn):
    try:
        fn()
        report[name] = "ok"
    except Exception as e:
        failures.append(f"{name}: {type(e).__name__}: {str(e)[:300]}")
        report[name] = "FAIL"

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--lang=ko-KR"])
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ko-KR")
    page = ctx.new_page()
    page.on("console", lambda m: errors.append(f"[console.{m.type}] {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))
    page.goto(URL, wait_until="load")
    page.wait_for_timeout(700)

    report["title"] = page.title()
    report["header"] = page.inner_text("#htitle")
    report["month_chips"] = page.locator("#monthBody .chip").count()
    report["month_rows"] = page.locator("#monthBody .week-row").count()
    report["today_cell"] = page.locator("#monthBody .cell.today").count()
    report["cal_rows"] = page.locator("#calList .cal-row").count()
    page.screenshot(path=str(OUT / "01-month.png"))

    # ---- quick add ----
    def quick_add():
        cell = page.locator('#monthBody .cell[data-date]').nth(24)
        box = cell.bounding_box()
        page.mouse.click(box["x"] + 14, box["y"] + box["height"] - 12)
        page.wait_for_timeout(300)
        assert page.locator("#qa").is_visible(), "quick add popup not visible"
        report["quickadd_default_time"] = page.input_value("#qaStart")
        page.screenshot(path=str(OUT / "02-quickadd.png"))
        page.fill("#qaTitle", "테스트 일정")
        page.keyboard.press("Enter")
        page.wait_for_timeout(400)
        assert "테스트 일정" in page.inner_text("#monthBody"), "saved event not in month"
        evs = json.loads(page.evaluate("localStorage.getItem('pcal.events.v1')"))
        ev = [e for e in evs if e["title"] == "테스트 일정"][0]
        report["quickadd_event"] = {k: ev.get(k) for k in ("title", "start", "end", "allDay", "calId")}
    step("quick_add", quick_add)

    # ---- drag & drop month ----
    def drag_month():
        chip = page.locator("#monthBody .chip.allday").first
        key_before = chip.get_attribute("data-key")
        cbox = chip.bounding_box()
        dest = page.locator('#monthBody .cell[data-date]').nth(33)
        ddate = dest.get_attribute("data-date")
        dbox = dest.bounding_box()
        page.mouse.move(cbox["x"] + 20, cbox["y"] + 9)
        page.mouse.down()
        page.mouse.move(cbox["x"] + 60, cbox["y"] + 26, steps=6)
        page.mouse.move(dbox["x"] + 40, dbox["y"] + dbox["height"] - 26, steps=14)
        page.mouse.up()
        page.wait_for_timeout(600)
        if page.locator("#confirm").is_visible():
            page.click("#cbOk"); page.wait_for_timeout(400)
        evs = json.loads(page.evaluate("localStorage.getItem('pcal.events.v1')"))
        moved = [e for e in evs if e["title"] == key_before.split("@")[0] or e["id"] == key_before.split("@")[0]]
        report["drag_key"] = key_before
        report["drag_target"] = ddate
        report["drag_moved_to"] = moved[0]["start"] if moved else None
        assert moved and (moved[0]["start"].startswith(ddate) or moved[0]["end"].startswith(ddate)), "event not moved to target date"
        page.wait_for_timeout(2700)
    step("drag_month", drag_month)

    # ---- week view ----
    def week_view():
        page.click("#viewBtn"); page.wait_for_timeout(150)
        page.click('#viewMenu [data-view="week"]'); page.wait_for_timeout(600)
        report["week_tevs"] = page.locator("#tvInner .tev").count()
        report["week_allday"] = page.locator("#tvAllDay .ad-chip").count()
        report["nowline"] = page.locator(".nowline").count()
        report["week_header"] = page.inner_text("#htitle")
        assert page.locator("#tvInner .tev").count() > 0
        page.screenshot(path=str(OUT / "03-week.png"))
    step("week_view", week_view)

    # ---- drag-create on time grid ----
    def drag_create():
        col = page.locator("#tvInner .tday").nth(2)
        cb = col.bounding_box()
        page.mouse.move(cb["x"] + 30, cb["y"] + 48 * 10)
        page.mouse.down()
        page.mouse.move(cb["x"] + 30, cb["y"] + 48 * 11.5, steps=12)
        page.mouse.up()
        page.wait_for_timeout(500)
        assert page.locator("#modal").is_visible(), "event modal did not open"
        report["modal_start"] = page.input_value("#mStartTime")
        report["modal_end"] = page.input_value("#mEndTime")
        report["modal_dates"] = [page.input_value("#mStartDate"), page.input_value("#mEndDate")]
        page.fill("#mTitle", "시간 선택 일정")
        page.wait_for_timeout(200)
        page.screenshot(path=str(OUT / "04-event-modal.png"))
        page.click("#mSave")
        page.wait_for_timeout(500)
        assert "시간 선택 일정" in page.inner_text("#viewTime"), "saved event missing in week view"
    step("drag_create", drag_create)

    # ---- detail popover + simulate send ----
    def detail_send():
        page.click("#viewBtn"); page.wait_for_timeout(120)
        page.click('#viewMenu [data-view="month"]'); page.wait_for_timeout(500)
        page.locator("#monthBody .chip.timed").first.click()
        page.wait_for_timeout(400)
        assert page.locator("#pop").is_visible(), "popover not visible"
        report["pop_title"] = page.inner_text("#popTitle")
        report["pop_body"] = page.inner_text("#popBody").replace("\n", " | ")
        page.screenshot(path=str(OUT / "05-detail.png"))
        page.click("#popSend")
        page.wait_for_timeout(300)
        assert page.locator("#confirm").is_visible(), "confirm dialog missing"
        page.click("#cbOk")
        page.wait_for_timeout(700)
        assert page.locator("#pop").is_visible(), "popover did not reopen after send"
        report["pop_after_send"] = page.inner_text("#pop").replace("\n", " | ")
        assert "전송됨" in report["pop_after_send"], "sent badge missing"
        page.screenshot(path=str(OUT / "06-sent.png"))
        page.click("#popClose")
        page.wait_for_timeout(200)
    step("detail_send", detail_send)

    # ---- edit modal from popover ----
    def edit_modal():
        page.locator("#monthBody .chip.timed").first.click()
        page.wait_for_timeout(300)
        page.click("#popEdit")
        page.wait_for_timeout(400)
        report["edit_modal_open"] = page.locator("#modal").is_visible()
        report["edit_modal_title"] = page.input_value("#mTitle")
        page.screenshot(path=str(OUT / "07-edit-modal.png"))
        page.click("#mCancel"); page.wait_for_timeout(200)
        if page.locator("#pop").is_visible():
            page.click("#popClose")
    step("edit_modal", edit_modal)

    # ---- schedule ----
    def schedule():
        page.click("#viewBtn"); page.wait_for_timeout(120)
        page.click('#viewMenu [data-view="schedule"]'); page.wait_for_timeout(500)
        report["schedule_days"] = page.locator("#sch .sch-day").count()
        report["schedule_items"] = page.locator("#sch .sch-item").count()
        assert page.locator("#sch .sch-item").count() > 0
        page.screenshot(path=str(OUT / "08-schedule.png"))
    step("schedule", schedule)

    # ---- search ----
    def search():
        page.fill("#q", "외식")
        page.wait_for_timeout(450)
        report["search_hits"] = page.locator("#searchPanel .sp-item").count()
        assert report["search_hits"] > 0
        page.screenshot(path=str(OUT / "09-search.png"))
        page.locator("#searchPanel .sp-item").first.click()
        page.wait_for_timeout(600)
        report["search_jump_view"] = page.inner_text("#htitle")
        if page.locator("#pop").is_visible():
            page.click("#popClose")
        page.fill("#q", "")
        page.keyboard.press("Escape")
    step("search", search)

    # ---- holidays (December) ----
    def holidays():
        page.goto(URL + "&d=2026-12-20", wait_until="load")
        page.wait_for_timeout(600)
        report["holiday_texts"] = page.locator("#monthBody .hol").all_inner_texts()
        page.screenshot(path=str(OUT / "10-december-holiday.png"))
        assert "기독탄신일" in " ".join(report["holiday_texts"]), "christmas holiday missing"
    step("holidays", holidays)

    # ---- settings ----
    def settings():
        page.goto(URL, wait_until="load")
        page.wait_for_timeout(500)
        page.click("#settingsBtn"); page.wait_for_timeout(300)
        report["settings_open"] = page.locator("#settings").is_visible()
        page.screenshot(path=str(OUT / "11-settings.png"))
        page.click("#setClose")
    step("settings", settings)

    ctx.close()

    # ---------------- mobile ----------------
    mctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                               is_mobile=True, has_touch=True, locale="ko-KR")
    mp = mctx.new_page()
    mp.on("console", lambda m: errors.append(f"[mobile.console.{m.type}] {m.text}") if m.type == "error" else None)
    mp.on("pageerror", lambda e: errors.append(f"[mobile.pageerror] {e}"))
    mp.goto(URL, wait_until="load")
    mp.wait_for_timeout(800)
    report["mobile_chips"] = mp.locator("#monthBody .chip").count()
    report["mobile_fab"] = mp.locator("#fab").is_visible()
    report["mobile_overflow_x"] = mp.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
    mp.screenshot(path=str(OUT / "12-mobile-month.png"))

    def mobile_drawer():
        mp.click("#menuBtn"); mp.wait_for_timeout(450)
        assert mp.evaluate("document.body.classList.contains('drawer-open')")
        mp.screenshot(path=str(OUT / "13-mobile-drawer.png"))
        mp.mouse.click(360, 420); mp.wait_for_timeout(400)
        assert not mp.evaluate("document.body.classList.contains('drawer-open')"), "drawer did not close"
    step("mobile_drawer", mobile_drawer)

    def mobile_week():
        mp.click("#viewBtn"); mp.wait_for_timeout(200)
        mp.click('#viewMenu [data-view="week"]'); mp.wait_for_timeout(700)
        report["mobile_week_tevs"] = mp.locator("#tvInner .tev").count()
        report["mobile_week_overflow_x"] = mp.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        mp.screenshot(path=str(OUT / "14-mobile-week.png"))
    step("mobile_week", mobile_week)

    def mobile_quickadd():
        mp.evaluate("document.body.classList.remove('drawer-open')")
        mp.click("#viewBtn"); mp.wait_for_timeout(150)
        mp.click('#viewMenu [data-view="month"]'); mp.wait_for_timeout(500)
        cell = mp.locator("#monthBody .cell[data-date]").nth(20)
        cb = cell.bounding_box()
        mp.touchscreen.tap(cb["x"] + 10, cb["y"] + cb["height"] - 8)
        mp.wait_for_timeout(500)
        assert mp.locator("#qa").is_visible(), "mobile quick add not visible"
        mp.screenshot(path=str(OUT / "15-mobile-quickadd.png"))
        mp.fill("#qaTitle", "모바일 일정")
        mp.tap("#qaSave")
        mp.wait_for_timeout(500)
        assert "모바일 일정" in mp.inner_text("#monthBody")
    step("mobile_quickadd", mobile_quickadd)

    def mobile_modal():
        mp.tap("#fab"); mp.wait_for_timeout(2900)
        assert mp.locator("#modal").is_visible()
        mp.screenshot(path=str(OUT / "16-mobile-modal.png"))
    step("mobile_modal", mobile_modal)

    mctx.close()

    # ---------------- edges (no screenshots) ----------------
    ectx = browser.new_context(viewport={"width": 1280, "height": 860}, locale="ko-KR")
    ep = ectx.new_page()
    ep.on("console", lambda m: errors.append(f"[edge.console.{m.type}] {m.text}") if m.type == "error" else None)
    ep.on("pageerror", lambda e: errors.append(f"[edge.pageerror] {e}"))
    ep.goto(URL, wait_until="load")
    ep.wait_for_timeout(600)

    def calendar_toggle():
        before = ep.locator("#monthBody .chip").count()
        ep.click('[data-toggle="family"]'); ep.wait_for_timeout(450)
        after = ep.locator("#monthBody .chip").count()
        report["toggle_counts"] = [before, after]
        assert after < before, "hiding a calendar did not hide its events"
        ep.click('[data-toggle="family"]'); ep.wait_for_timeout(400)
        assert ep.locator("#monthBody .chip").count() == before, "showing a calendar did not restore events"
    step("calendar_toggle", calendar_toggle)

    def add_calendar():
        ep.click("#addCalBtn"); ep.wait_for_timeout(350)
        assert ep.locator("#calModal").is_visible()
        ep.fill("#cName", "테스트")
        ep.click('#cColors [data-color="#d50000"]')
        ep.click("#cSave"); ep.wait_for_timeout(450)
        assert "테스트" in ep.inner_text("#calList"), "new calendar not in list"
        cals = json.loads(ep.evaluate("localStorage.getItem('pcal.cals.v1')"))
        report["calendars"] = [c["name"] for c in cals]
    step("add_calendar", add_calendar)

    def allday_quickadd():
        cell = ep.locator("#monthBody .cell[data-date]").nth(30)
        d = cell.get_attribute("data-date")
        box = cell.bounding_box()
        ep.mouse.click(box["x"] + 12, box["y"] + box["height"] - 10)
        ep.wait_for_timeout(300)
        ep.click("#qaAllDayBtn")
        ep.fill("#qaTitle", "종일 테스트")
        ep.click("#qaSave"); ep.wait_for_timeout(450)
        evs = json.loads(ep.evaluate("localStorage.getItem('pcal.events.v1')"))
        ev = [x for x in evs if x["title"] == "종일 테스트"][0]
        report["allday_event"] = {"start": ev["start"], "end": ev["end"], "allDay": ev["allDay"], "date": d}
        assert ev["allDay"] is True and ev["start"] == d
    step("allday_quickadd", allday_quickadd)

    def keyboard():
        ep.keyboard.press("w"); ep.wait_for_timeout(500)
        assert ep.locator("#viewTime").is_visible(), "W shortcut did not switch to week"
        ep.keyboard.press("t"); ep.wait_for_timeout(400)
        ep.keyboard.press("n"); ep.wait_for_timeout(400)
        assert ep.locator("#modal").is_visible(), "N shortcut did not open editor"
        ep.keyboard.press("Escape"); ep.wait_for_timeout(300)
        assert not ep.locator("#modal").is_visible(), "Esc did not close editor"
        report["keyboard"] = "ok"
    step("keyboard", keyboard)

    def edit_and_delete():
        ep.click("#viewBtn"); ep.wait_for_timeout(200)
        ep.click('#viewMenu [data-view="month"]'); ep.wait_for_timeout(500)
        ep.locator("#monthBody .chip.timed").first.click(); ep.wait_for_timeout(350)
        assert ep.locator("#pop").is_visible()
        ep.click("#popEdit"); ep.wait_for_timeout(400)
        ep.fill("#mTitle", "수정된 일정")
        ep.fill("#mLocation", "테스트 장소")
        ep.click("#mSave"); ep.wait_for_timeout(500)
        assert "수정된 일정" in ep.inner_text("#monthBody"), "edited title not rendered"
        evs = json.loads(ep.evaluate("localStorage.getItem('pcal.events.v1')"))
        ev = [x for x in evs if x["title"] == "수정된 일정"][0]
        report["edited_event"] = {"location": ev["location"], "start": ev["start"]}
        # 삭제
        ep.locator('#monthBody .chip[data-key]').first.click(); ep.wait_for_timeout(350)
        key = ep.locator("#pop").get_attribute("data-key") if False else None
        ep.click("#popDel"); ep.wait_for_timeout(350)
        assert ep.locator("#confirm").is_visible(), "delete confirm missing"
        ep.click("#cbOk"); ep.wait_for_timeout(600)
        evs2 = json.loads(ep.evaluate("localStorage.getItem('pcal.events.v1')"))
        report["counts"] = [len(evs), len(evs2)]
        assert len(evs2) == len(evs) - 1, "event not deleted"
    step("edit_and_delete", edit_and_delete)
    ectx.close()

    # ---------------- first-run empty state ----------------
    nctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ko-KR")
    np = nctx.new_page()
    np.on("console", lambda m: errors.append(f"[empty.console.{m.type}] {m.text}") if m.type == "error" else None)
    np.on("pageerror", lambda e: errors.append(f"[empty.pageerror] {e}"))
    np.goto(BASE, wait_until="load")
    np.wait_for_timeout(600)
    report["empty_chips"] = np.locator("#monthBody .chip").count()
    report["empty_banner"] = np.locator("#emptyBanner").is_visible()
    np.screenshot(path=str(OUT / "17-empty.png"))
    if np.locator("#loadDemo").is_visible():
        np.click("#loadDemo"); np.wait_for_timeout(600)
    report["after_demo_chips"] = np.locator("#monthBody .chip").count()
    assert report["after_demo_chips"] > 0, "demo button did not fill events"
    nctx.close()

    browser.close()

report["errors"] = errors
report["failures"] = failures
print(json.dumps(report, ensure_ascii=False, indent=2))
with open(OUT / "report.json", "w") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
