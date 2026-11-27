"""
내 캘린더 — 두 기기 동기화 통합 검증
================================================================
PC(컨텍스트 A)와 휴대폰(컨텍스트 B)을 띄워 놓고, Apps Script 백엔드를
메모리 안에서 흉내 낸(mock) 뒤 다음을 확인합니다.

  1. PC에서 고친 일정이, 새로고침 없이 화면으로 돌아온 휴대폰에 반영되는가
  2. 휴대폰에서 만든 일정이 시트로 올라가 PC에 보이는가
  3. PC에서 지운 일정이 휴대폰에서도 사라지는가
  4. 시트 저장이 실패(오프라인)한 변경이 대기열에 남았다가 나중에 자동으로 올라가는가
  5. 아직 시트에 올린 적 없는 이 기기의 일정이 처음 동기화 때 지워지지 않는가
  6. 큰 저장(saveAll)이 POST 로도, 작은 저장이 JSONP 로도 되는가
  7. PC에서 캘린더 이름을 고치면 시트·휴대폰에 반영되고, 다시 동기화해도 되돌아가지 않는가
  8. 시트에 기본 캘린더만 있어도 이 기기에서 고친 캘린더 이름이 날아가지 않는가
  9. 캘린더 이름 변경이 오프라인에서도 대기열에 남았다가 복구 뒤 올라가는가
 10. 캘린더 삭제가 시트·다른 기기에 반영되고 다시 동기화해도 되살아나지 않는가
 11. 콘솔 오류 0건

실행:  python3 test/sync.py
"""
import functools, http.server, json, os, pathlib, socketserver, threading, time, urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("PCAL_PORT", "8794"))
MOCK_URL = "https://script.google.com/macros/s/mock-calendar-do-not-share/exec"
LATENCY = float(os.environ.get("PCAL_LATENCY", "0.15"))   # Apps Script 응답 지연 흉내
OUT = pathlib.Path(os.environ.get("PCAL_OUT") or (ROOT / "verification" / "2026-11-26-sync"))
OUT.mkdir(parents=True, exist_ok=True)

CALS = [
    {"id": "personal", "name": "개인", "color": "#039be5", "visible": True, "order": 0, "updatedAt": ""},
    {"id": "family", "name": "가족", "color": "#33b679", "visible": True, "order": 1, "updatedAt": ""},
]


def ev(id, title, start, end=None, allday=False, updated=None, rrule="", exdates=None):
    return {
        "id": id, "title": title, "start": start, "end": end or start,
        "allDay": allday, "repeat": "none", "calId": "personal", "location": "",
        "memo": "", "googleId": "", "updatedAt": updated or "2026-11-25T00:00:00.000Z",
        "rrule": rrule, "exdates": exdates or [],
    }


# ---------------------------------------------------------------- mock 백엔드
class Backend:
    def __init__(self):
        self.version = "1.2.0"          # 1.2.0 미만이면 『색상』『완료』 열이 없는 예전 시트를 흉내 냅니다
        self.events = {}
        self.calendars = [dict(c) for c in CALS]
        self.kids_children = []
        self.kids_events = {}
        self.fail = False
        self.kids_fail = False          # 육아 저장만 실패시키는 스위치(통합 업로드 검증용)
        self.calls = []
        self.lock = threading.Lock()

    def old_columns(self):
        """예전 백엔드(1.2.0 미만): 일정 탭에 『색상』『완료』 열이 없어 그 값은 저장되지 않습니다."""
        try:
            return float(self.version.split(".")[1]) < 2
        except Exception:
            return True

    def keep(self, e):
        """실서버처럼 그 시트에 있는 열만 저장합니다."""
        e = dict(e)
        if self.old_columns():
            e.pop("color", None)
            e.pop("done", None)
        return e

    def reset(self, events, kids=None, version=None):
        if version is not None:
            self.version = version
        self.events = {e["id"]: self.keep(e) for e in events}
        self.calendars = [dict(c) for c in CALS]
        self.kids_children = [dict(c) for c in ((kids or {}).get("children") or [])]
        self.kids_events = {e["id"]: dict(e) for e in ((kids or {}).get("events") or [])}
        self.fail = False
        self.kids_fail = False
        self.calls = []

    def load(self):
        return {"ok": True, "version": self.version, "events": list(self.events.values()),
                "calendars": self.calendars, "settings": {},
                "kids": {"children": self.kids_children, "events": list(self.kids_events.values())}}

    def do(self, action, data):
        with self.lock:
            self.calls.append(action)
            if self.fail and action not in ("ping", "load"):
                return {"ok": False, "error": "모의 백엔드 저장 실패"}
            if self.kids_fail and action in ("saveKidsEvent", "deleteKidsEvent", "saveKidsProfile"):
                return {"ok": False, "error": "모의 백엔드 육아 저장 실패"}
            if action in ("ping", "status"):
                return {"ok": True, "version": self.version, "now": "2026-11-26T00:00:00.000Z",
                        "sheet": "[비공개] 내 캘린더", "timezone": "Asia/Seoul"}
            if action == "load":
                return self.load()
            if action == "saveEvent":
                e = data.get("event") or data
                if not e.get("id"):
                    return {"ok": False, "error": "일정 ID가 없습니다."}
                old = self.events.get(e["id"], {})
                e = dict(e)
                e["googleId"] = e.get("googleId") or old.get("googleId") or ""
                self.events[e["id"]] = self.keep(e)
                return {"ok": True, "id": e["id"], "googleId": e["googleId"]}
            if action == "deleteEvent":
                self.events.pop(data.get("id"), None)
                return {"ok": True, "id": data.get("id")}
            if action == "saveCalendars":
                cals = data.get("calendars") or []
                if not isinstance(cals, list) or not cals:
                    return {"ok": False, "error": "캘린더 목록이 없습니다."}
                # 실서버와 같게: 수정시각을 안 보내는 예전 앱이 더 최신 값을 덮지 못하게 막습니다.
                if not any(c.get("updatedAt") for c in cals) and any(c.get("updatedAt") for c in self.calendars):
                    return {"ok": False, "error": "예전 버전 앱에서는 캘린더를 저장할 수 없습니다."}
                self.calendars = [dict(c) for c in cals]
                return {"ok": True, "count": len(self.calendars)}
            if action == "saveAll":
                events = data.get("events")
                if not isinstance(events, list):
                    return {"ok": False, "error": "일정 목록이 없습니다."}
                if not events and self.events and data.get("clear") is not True:
                    return {"ok": False, "error": "올릴 일정이 없어 지우지 않았습니다."}
                self.events = {e["id"]: self.keep(e) for e in events if e.get("id")}
                cals = data.get("calendars")
                if isinstance(cals, list) and cals:
                    self.calendars = [dict(c) for c in cals]
                return {"ok": True, "events": len(self.events)}
            if action == "exportToGoogleCalendar":
                return {"ok": True, "googleId": "gid-mock", "calendar": "기본"}
            if action == "saveKidsEvent":
                e = data.get("event") or data
                if not e.get("id"):
                    return {"ok": False, "error": "육아 일정 ID가 없습니다."}
                self.kids_events[e["id"]] = dict(e)
                return {"ok": True, "id": e["id"]}
            if action == "deleteKidsEvent":
                self.kids_events.pop(data.get("id"), None)
                return {"ok": True, "id": data.get("id")}
            if action == "saveKidsProfile":
                kids = data.get("children") or []
                if not isinstance(kids, list) or not kids:
                    return {"ok": False, "error": "아이 프로필 목록이 없습니다."}
                if not any(c.get("updatedAt") for c in kids) and any(c.get("updatedAt") for c in self.kids_children):
                    return {"ok": False, "error": "이 앱은 아이 프로필 수정시각을 보내지 않는 예전 버전입니다."}
                self.kids_children = [dict(c) for c in kids]
                return {"ok": True, "count": len(self.kids_children)}
            return {"ok": False, "error": "알 수 없는 요청: " + str(action)}


BACK = Backend()

class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass



socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(("127.0.0.1", PORT), functools.partial(Quiet, directory=str(ROOT)))
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"

from playwright.sync_api import sync_playwright

report, failures, errors = {}, [], []


def check(name, cond, detail=""):
    report[name] = "ok" if cond else "FAIL"
    if not cond:
        failures.append(f"{name}: {detail}")


PUMP = [None]   # 기다리는 동안에도 브라우저 쪽 요청을 처리해 주기 위한 페이지


def wait_until(fn, timeout=6.0, step=0.15):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        # time.sleep 은 Playwright 드라이버를 멈춰 모의 백엔드 응답이 처리되지 않습니다.
        if PUMP[0] is not None:
            try:
                PUMP[0].wait_for_timeout(int(step * 1000))
                continue
            except Exception:
                pass
        time.sleep(step)
    return False


def wire_console(page, tag):
    page.on("console", lambda m: errors.append(f"[{tag}.console.{m.type}] {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"[{tag}.pageerror] {e}"))
    page.on("crash", lambda: errors.append(f"[{tag}.crash] renderer crashed"))


def titles(page):
    return page.evaluate("JSON.parse(localStorage.getItem('pcal.events.v1')||'[]').map(e=>e.title)")


def stored(page, key):
    return page.evaluate(f"JSON.parse(localStorage.getItem('{key}')||'{{}}')")


def foreground(page):
    """휴대폰을 들여다보는 순간(탭 복귀·앱 재개)을 흉내 냅니다."""
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")


def edit_title(page, old, new):
    page.locator("#monthBody .chip", has_text=old).first.click()
    page.wait_for_timeout(250)
    page.click("#popEdit")
    page.wait_for_timeout(250)
    page.fill("#mTitle", new)
    page.click("#mSave")
    page.wait_for_timeout(500)


def cal_names(page):
    return page.evaluate("JSON.parse(localStorage.getItem('pcal.cals.v1')||'[]').map(c=>c.name)")


def cal_ids(page):
    return page.evaluate("JSON.parse(localStorage.getItem('pcal.cals.v1')||'[]').map(c=>c.id)")


def kid_titles(page):
    return page.evaluate("JSON.parse(localStorage.getItem('pcal.kids.events.v1')||'[]').map(e=>e.title)")


def kid_names(page):
    return page.evaluate("JSON.parse(localStorage.getItem('pcal.kids.kids.v1')||'[]').map(c=>c.name)")


def kid_pending(page):
    return page.evaluate("JSON.parse(localStorage.getItem('pcal.kids.pending.v1')||'{}')")


def kid_add(page, title, date, start="16:00", end="17:30"):
    page.click('#tabBar [data-tab="kidHome"]')
    page.wait_for_timeout(250)
    page.click("#kidAddBtn")
    page.wait_for_timeout(300)
    page.fill("#kTitle", title)
    page.fill("#kDate", date)
    page.fill("#kStart", start)
    page.fill("#kEnd", end)
    page.click("#kSave")
    page.wait_for_timeout(600)


def open_cal_editor(page, cal_id):
    """사이드바의 캘린더 이름(편집 진입점)을 실제 클릭합니다."""
    page.evaluate(
        "id => { const el=document.querySelector('[data-editcal=\"' + id + '\"]');"
        " if(el) el.click(); }", cal_id)
    page.wait_for_timeout(300)


def rename_cal(page, cal_id, new_name):
    open_cal_editor(page, cal_id)
    page.fill("#cName", new_name)
    page.click("#cSave")
    page.wait_for_timeout(700)


def seed(ctx, events):
    """앱이 처음 켜지기 전 상태(localStorage)를 심습니다 — 이미 시트 주소를 넣어 둔 기기."""
    js = """
    try {
      if(!localStorage.getItem('__seeded')){
        localStorage.setItem('pcal.url', %s);
        localStorage.setItem('pcal.events.v1', %s);
        localStorage.setItem('pcal.cals.v1', %s);
        localStorage.setItem('__seeded','1');
      }
      localStorage.setItem('pcal_demo_purged_v1','1');
    } catch(e) {}
    """ % (json.dumps(MOCK_URL), json.dumps(json.dumps(events or [])), json.dumps(json.dumps(CALS)))
    ctx.add_init_script(js)


def seed_raw(ctx, events, cals, url=None):
    """캘린더 이름을 직접 고쳐 둔 기기처럼 심습니다(url=None 이면 아직 시트에 연결하지 않은 기기)."""
    url_js = ("localStorage.setItem('pcal.url', %s);" % json.dumps(url)) if url else ""
    js = """
    try {
      if(!localStorage.getItem('__seeded')){
        localStorage.setItem('pcal.events.v1', %s);
        localStorage.setItem('pcal.cals.v1', %s);
        %s
        localStorage.setItem('__seeded','1');
      }
      localStorage.setItem('pcal_demo_purged_v1','1');
    } catch(e) {}
    """ % (json.dumps(json.dumps(events or [])), json.dumps(json.dumps(cals)), url_js)
    ctx.add_init_script(js)


def new_page(ctx, tag="main", events=None):
    seed(ctx, events or [])
    page = ctx.new_page()
    PUMP[0] = page
    wire_console(page, tag)
    page.goto(f"{BASE}/index.html", wait_until="load")
    page.wait_for_timeout(1200)
    return page


def seed_extra(ctx, kid_events=None, kid_children=None, known=None, kid_known=None, seen=True):
    """앱이 켜지기 전 상태를 더 심습니다 — 이 기기에만 있던 육아 데이터, 시트에서 본 적 있는 ID 목록 등."""
    parts = []
    if kid_events is not None:
        parts.append("localStorage.setItem('pcal.kids.events.v1', %s);" % json.dumps(json.dumps(kid_events)))
    if kid_children is not None:
        parts.append("localStorage.setItem('pcal.kids.kids.v1', %s);" % json.dumps(json.dumps(kid_children)))
    if known is not None:
        parts.append("localStorage.setItem('pcal.sync.v1', %s);" % json.dumps(json.dumps(
            {"seenNonEmpty": seen, "strikes": 0, "lastPull": 0, "known": known, "backend": "1.2.0"})))
    if kid_known is not None:
        parts.append("localStorage.setItem('pcal.kids.sync.v1', %s);" % json.dumps(json.dumps(
            {"known": kid_known, "seen": seen, "strikes": 0, "profileKnown": [], "unsupported": False})))
    ctx.add_init_script("try{%s}catch(e){}" % "".join(parts))


def kid_event(eid, title, date, start="16:00", end="17:00", updated="2026-11-25T00:00:00.000Z",
              child="kid1", cat="academy"):
    return {"id": eid, "title": title, "childIds": [child], "cat": cat, "date": date, "endDate": "",
            "allDay": False, "start": start, "end": end, "place": "", "memo": "", "supplies": [],
            "rrule": "", "exdates": [], "createdAt": updated, "updatedAt": updated}


def kid_profile(names, updated="2026-11-25T00:00:00.000Z"):
    colors = ["#3B82F6", "#F59E0B"]
    return [{"id": "kid%d" % (i + 1), "name": n, "color": colors[i % len(colors)], "updatedAt": updated}
            for i, n in enumerate(names)]


def mock_route(route):
    try:
        return _mock_route(route)
    except Exception:            # 페이지 이동·새로고침으로 끊긴 요청은 그냥 버립니다(라우트가 막히지 않도록)
        try:
            route.abort()
        except Exception:
            pass


def _mock_route(route):
    req = route.request
    q = urllib.parse.parse_qs(urllib.parse.urlparse(req.url).query)
    cb = (q.get("callback") or [""])[0]
    try:
        if req.method == "POST":
            body = json.loads(req.post_data or "{}")
            action, data = body.get("action"), body
        else:
            action = (q.get("action") or ["ping"])[0]
            data = json.loads((q.get("d") or ["{}"])[0])
        time.sleep(LATENCY)
        payload = json.dumps(BACK.do(action, data), ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        payload = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    ctype = "application/javascript; charset=utf-8" if cb else "application/json; charset=utf-8"
    body = f"{cb}({payload});" if cb else payload
    route.fulfill(status=200, body=body, headers={
        "Content-Type": ctype,
        "Access-Control-Allow-Origin": "*",
    })


SEED = [
    ev("e-seed-1", "기존 일정 하나", "2026-11-27T10:00", "2026-11-27T11:00"),
    ev("e-seed-2", "기존 일정 둘", "2026-11-28"),
]

with sync_playwright() as pw:
    browser = pw.chromium.launch(args=["--lang=ko-KR", "--disable-gpu", "--js-flags=--max-old-space-size=192"])

    # ---------------------------------------------------------------- 1. 초기 로드
    BACK.reset(SEED)
    pc_ctx = browser.new_context(service_workers="block", viewport={"width": 1440, "height": 900}, locale="ko-KR",
                                 timezone_id="Asia/Seoul")
    pc_ctx.route("https://script.google.com/**", mock_route)
    pc = new_page(pc_ctx)
    check("1_pc_initial_load", wait_until(lambda: len(titles(pc)) == 2),
          f"titles={titles(pc)}")
    check("1_pc_sync_chip", "비공개 시트 연결됨" in pc.inner_text("#syncText"),
          pc.inner_text("#syncText"))
    check("1_pc_last_pull", stored(pc, "pcal.sync.v1").get("lastPull", 0) > 0,
          str(stored(pc, "pcal.sync.v1")))

    # ---------------------------------------------------------------- 2. 휴대폰 첫 로드
    ph_ctx = browser.new_context(service_workers="block", viewport={"width": 390, "height": 844}, device_scale_factor=1,
                                 is_mobile=True, has_touch=True, locale="ko-KR",
                                 timezone_id="Asia/Seoul")
    ph_ctx.route("https://script.google.com/**", mock_route)
    ph = new_page(ph_ctx)
    check("2_phone_initial_load", wait_until(lambda: len(titles(ph)) == 2), f"titles={titles(ph)}")
    ph.screenshot(path=str(OUT / "01-phone-before.png"))

    # ---------------------------------------------------------------- 2b. 날짜 숫자 클릭 → 일간 보기 (화면)
    num = pc.locator("#monthBody .dnum").nth(9)
    dnum_day = num.get_attribute("data-dayview")
    num.click()
    pc.wait_for_timeout(400)
    check("2b_date_number_opens_day",
          pc.locator("#viewTime").is_visible() and "on" in (pc.locator('#viewTabs [data-viewtab="day"]').get_attribute("class") or ""),
          pc.evaluate("document.querySelector('#htitle').textContent"))
    check("2b_date_number_title", ("%d일" % int(dnum_day.split("-")[2])) in pc.inner_text("#htitle"), pc.inner_text("#htitle"))
    pc.keyboard.press("3")
    pc.wait_for_timeout(300)
    check("2b_key_3_month", pc.locator("#viewMonth").is_visible(), pc.inner_text("#htitle"))

    # ---------------------------------------------------------------- 2c. 개별 색·완료(흑색)가 시트·두 기기에 반영
    pc.locator("#monthBody .chip", has_text="기존 일정 하나").first.click()
    pc.wait_for_timeout(300)
    pc.click("#popEdit")
    pc.wait_for_timeout(300)
    pc.click('#mColors [data-evcolor="#d50000"]')
    pc.click("#mSave")
    check("2c_sheet_color", wait_until(lambda: BACK.events.get("e-seed-1", {}).get("color") == "#d50000"),
          str(BACK.events.get("e-seed-1")))
    foreground(ph)
    check("2c_phone_color",
          wait_until(lambda: "#d50000" in (ph.locator('#monthBody .chip[data-key^="e-seed-1@"]').first.get_attribute("style") or "")),
          ph.locator('#monthBody .chip[data-key^="e-seed-1@"]').first.get_attribute("style"))
    ph.locator('#monthBody .chip[data-key^="e-seed-1@"]').first.tap()
    ph.wait_for_timeout(300)
    ph.tap("#popDone")
    check("2c_sheet_done", wait_until(lambda: BACK.events.get("e-seed-1", {}).get("done") is True),
          str(BACK.events.get("e-seed-1")))
    foreground(pc)
    check("2c_pc_done",
          wait_until(lambda: "done" in (pc.locator('#monthBody .chip[data-key^="e-seed-1@"]').first.get_attribute("class") or "")
                     and "#3f4042" in (pc.locator('#monthBody .chip[data-key^="e-seed-1@"]').first.get_attribute("style") or "")),
          pc.locator('#monthBody .chip[data-key^="e-seed-1@"]').first.get_attribute("style"))
    ph.screenshot(path=str(OUT / "01b-phone-done.png"))
    # 완료 해제 → 시트에도 반영
    ph.tap("#popDone")
    check("2c_done_off", wait_until(lambda: BACK.events.get("e-seed-1", {}).get("done") is False),
          str(BACK.events.get("e-seed-1")))

    # ---------------------------------------------------------------- 2d. 구버전 백엔드(색상·완료 열 없음)에서 읽어도 이 기기 값 보존
    saved = dict(BACK.events.get("e-seed-1", {}))
    BACK.events["e-seed-1"] = {k: v for k, v in saved.items() if k not in ("color", "done")}
    before_pull = stored(pc, "pcal.sync.v1").get("lastPull", 0)
    pc.evaluate("window.dispatchEvent(new Event('focus'))")
    check("2d_pull_happened", wait_until(lambda: stored(pc, "pcal.sync.v1").get("lastPull", 0) > before_pull), str(stored(pc, "pcal.sync.v1")))
    kept = [e for e in stored(pc, "pcal.events.v1") if e.get("id") == "e-seed-1"]
    check("2d_local_color_kept", bool(kept) and kept[0].get("color") == "#d50000", str(kept))
    BACK.events["e-seed-1"] = saved

    # ---------------------------------------------------------------- 3. PC 수정 → 휴대폰 (핵심)
    edit_title(pc, "기존 일정 하나", "PC에서 고친 제목")
    check("3_sheet_has_pc_edit", wait_until(lambda: BACK.events.get("e-seed-1", {}).get("title") == "PC에서 고친 제목"),
          str(BACK.events.get("e-seed-1")))
    check("3_phone_still_old", "PC에서 고친 제목" not in titles(ph), f"titles={titles(ph)}")
    foreground(ph)
    check("3_phone_picks_up_edit",
          wait_until(lambda: "PC에서 고친 제목" in titles(ph)),
          f"titles={titles(ph)}")
    check("3_phone_rendered", "PC에서 고친 제목" in ph.inner_text("#monthBody"))
    check("3_no_reload_needed", ph.evaluate("performance.getEntriesByType('navigation').length") == 1)
    ph.screenshot(path=str(OUT / "02-phone-updated.png"))

    # ---------------------------------------------------------------- 4. PC 추가 → 휴대폰
    cell = pc.locator('#monthBody .cell[data-date]').nth(5)
    box = cell.bounding_box()
    pc.mouse.click(box["x"] + 14, box["y"] + box["height"] - 12)
    pc.wait_for_timeout(300)
    pc.fill("#qaTitle", "PC에서 새로 만든 일정")
    pc.keyboard.press("Enter")
    pc.wait_for_timeout(600)
    check("4_sheet_has_new", wait_until(lambda: any(e.get("title") == "PC에서 새로 만든 일정" for e in BACK.events.values())),
          str(list(BACK.events.values())))
    foreground(ph)
    check("4_phone_picks_up_new", wait_until(lambda: "PC에서 새로 만든 일정" in titles(ph)), f"titles={titles(ph)}")

    # ---------------------------------------------------------------- 5. PC 삭제 → 휴대폰
    pc.locator("#monthBody .chip", has_text="기존 일정 둘").first.click()
    pc.wait_for_timeout(250)
    pc.click("#popDel")
    pc.wait_for_timeout(250)
    pc.click("#cbOk")
    pc.wait_for_timeout(700)
    check("5_sheet_deleted", wait_until(lambda: "e-seed-2" not in BACK.events), str(list(BACK.events)))
    foreground(ph)
    check("5_phone_dropped", wait_until(lambda: "기존 일정 둘" not in titles(ph)), f"titles={titles(ph)}")

    # ---------------------------------------------------------------- 6. 휴대폰 추가 → PC
    ph.click("#fab")
    ph.wait_for_timeout(400)
    ph.fill("#mTitle", "휴대폰에서 만든 일정")
    ph.tap("#mSave")
    ph.wait_for_timeout(700)
    check("6_sheet_has_phone_event",
          wait_until(lambda: any(e.get("title") == "휴대폰에서 만든 일정" for e in BACK.events.values())),
          str(list(BACK.events.values())))
    pc.evaluate("window.dispatchEvent(new Event('focus'))")
    check("6_pc_picks_up_phone_event", wait_until(lambda: "휴대폰에서 만든 일정" in titles(pc)), f"titles={titles(pc)}")

    # ---------------------------------------------------------------- 7. 저장 실패 → 대기열 → 자동 재전송
    BACK.fail = True
    ph.click("#fab")
    ph.wait_for_timeout(300)
    ph.fill("#mTitle", "오프라인에서 만든 일정")
    ph.tap("#mSave")
    ph.wait_for_timeout(1500)
    check("7_queued_while_failing", len(stored(ph, "pcal.pending.v1")) >= 1, str(stored(ph, "pcal.pending.v1")))
    check("7_event_kept_locally", "오프라인에서 만든 일정" in titles(ph), f"titles={titles(ph)}")
    check("7_error_state", ph.evaluate("document.querySelector('#syncChip').classList.contains('error')"))
    BACK.fail = False
    foreground(ph)
    check("7_pending_flushed", wait_until(lambda: len(stored(ph, "pcal.pending.v1")) == 0, timeout=8),
          str(stored(ph, "pcal.pending.v1")))
    check("7_sheet_has_offline_event",
          wait_until(lambda: any(e.get("title") == "오프라인에서 만든 일정" for e in BACK.events.values())),
          str(list(BACK.events.values())))

    # ---------------------------------------------------------------- 8. 두 번 읽어도 중복 없음
    before = len(titles(ph))
    foreground(ph)
    ph.wait_for_timeout(1500)
    foreground(ph)
    ph.wait_for_timeout(1500)
    check("8_no_duplicates", len(titles(ph)) == before, f"{before} → {len(titles(ph))}")

    # ---------------------------------------------------------------- 9. 수동 새로고침
    pc.evaluate("window.__calls0 = 1")
    n0 = len([c for c in BACK.calls if c == "load"])
    pc.click("#syncChip")
    pc.wait_for_timeout(1200)
    n1 = len([c for c in BACK.calls if c == "load"])
    check("9_manual_refresh", n1 > n0, f"load {n0} → {n1}")
    pc.close(); pc_ctx.close(); ph.close(); ph_ctx.close()   # 이후 검증에 영향 주지 않도록 정리

    # ---------------------------------------------------------------- 10. 큰 저장은 POST
    many = [ev(f"e-bulk-{i}", f"대량 일정 {i}", "2026-12-01T09:00", "2026-12-01T10:00") for i in range(40)]
    bulk_ctx = browser.new_context(service_workers="block", viewport={"width": 1440, "height": 900}, locale="ko-KR")
    bulk_ctx.route("https://script.google.com/**", mock_route)
    bulk = new_page(bulk_ctx, "bulk", events=many)
    bulk.click("#settingsBtn")
    bulk.wait_for_timeout(300)
    bulk.click("#setUpload")
    bulk.wait_for_timeout(300)
    bulk.click("#cbOk")
    # 이 브라우저 화면(로컬 40 + 시트에 있던 4)이 시트를 통째로 대체합니다.
    check("10_saveall_replaced_sheet", wait_until(lambda: len(BACK.events) == 44, timeout=10), f"events={len(BACK.events)}")
    check("10_saveall_has_bulk", all(f"e-bulk-{i}" in BACK.events for i in range(40)), "bulk ids missing")
    check("10_saveall_used_post", "saveAll" in BACK.calls, str(BACK.calls[-5:]))
    check("10_msg", "올렸" in bulk.inner_text("#setMsg") or "올" in bulk.inner_text("#setMsg"),
          bulk.inner_text("#setMsg"))
    bulk.close(); bulk_ctx.close()

    # ---------------------------------------------------------------- 11. 시트가 비어 있어도 기기 일정은 연결만으로 자동 업로드
    BACK.reset([])
    local_only = [
        ev("e-local-1", "이 기기에만 있던 일정", "2026-11-29T09:00", "2026-11-29T10:00"),
        ev("e-local-2", "이 기기에만 있던 일정 둘", "2026-11-30"),
    ]
    keep_ctx = browser.new_context(service_workers="block", viewport={"width": 1440, "height": 900}, locale="ko-KR")
    keep_ctx.route("https://script.google.com/**", mock_route)
    keep = new_page(keep_ctx, "keep", events=local_only)
    keep.wait_for_timeout(1500)
    check("11_local_kept_when_sheet_empty", len(titles(keep)) == 2, f"titles={titles(keep)}")
    # 새로 추가한 기능: 손으로 올리지 않아도 시트로 자동 업로드 됩니다.
    check("11_auto_uploaded", wait_until(lambda: len(BACK.events) == 2, timeout=15), str(list(BACK.events)))
    check("11_auto_uploaded_ids", all(k in BACK.events for k in ("e-local-1", "e-local-2")), str(list(BACK.events)))
    check("11_pending_cleared", wait_until(lambda: len(stored(keep, "pcal.pending.v1")) == 0, timeout=10),
          str(stored(keep, "pcal.pending.v1")))
    check("11_no_duplicate", len(titles(keep)) == 2, f"titles={titles(keep)}")
    # 이미 다 올라갔으니 「이 기기에만 있는 일정 올리기」 버튼도 숨어 있습니다.
    check("11_push_local_hidden", wait_until(lambda: keep.evaluate("document.querySelector('#setPushLocal').hidden") is True, timeout=10),
          keep.evaluate("document.querySelector('#setPushLocal').hidden"))
    keep.click("#settingsBtn")
    keep.wait_for_timeout(300)
    check("11_hint_no_local_only", "이 기기에만" not in keep.inner_text("#setSyncInfo"), keep.inner_text("#setSyncInfo"))

    # ---------------------------------------------------------------- 12. #s= 링크로 연결한 기기도 기억함
    import base64
    b64 = base64.urlsafe_b64encode(MOCK_URL.encode()).decode().rstrip("=")
    link_ctx = browser.new_context(service_workers="block", viewport={"width": 390, "height": 844}, locale="ko-KR")
    link_ctx.route("https://script.google.com/**", mock_route)
    link_ctx.add_init_script("localStorage.setItem('pcal_demo_purged_v1','1');")
    link = link_ctx.new_page()
    wire_console(link, "link")
    BACK.reset([ev("e-link", "링크로 연결한 일정", "2026-11-27T13:00", "2026-11-27T14:00")])
    link.goto(f"{BASE}/index.html#s={b64}", wait_until="load")
    link.wait_for_timeout(1500)
    check("12_hash_link_loads", "링크로 연결한 일정" in titles(link), f"titles={titles(link)}")
    link.goto(f"{BASE}/index.html", wait_until="load")       # 홈 화면 앱처럼 해시 없이 다시 열기
    link.wait_for_timeout(1500)
    check("12_hash_link_remembered",
          link.evaluate("localStorage.getItem('pcal.url')") == MOCK_URL and "링크로 연결한 일정" in titles(link),
          f"url={link.evaluate('localStorage.getItem(\'pcal.url\')')} titles={titles(link)}")
    check("12_hash_link_pulls", stored(link, "pcal.sync.v1").get("lastPull", 0) > 0)
    link_ctx.close()

    # ---------------------------------------------------------------- 13. 전체 삭제는 시트에도 반영
    keep.click("#setReset")
    keep.wait_for_timeout(300)
    keep.click("#cbOk")
    check("13_reset_clears_sheet", wait_until(lambda: len(BACK.events) == 0), str(BACK.events))
    check("13_reset_clears_local", len(titles(keep)) == 0, f"titles={titles(keep)}")
    keep.close(); keep_ctx.close()

    # ---------------------------------------------------------------- 15. PC에서 캘린더 이름 수정 → 시트·휴대폰 반영, 재동기화에도 유지
    BACK.reset([ev("e-cal-1", "캘린더 테스트 일정", "2026-11-27T14:00", "2026-11-27T15:00")])
    cal_pc_ctx = browser.new_context(service_workers="block", viewport={"width": 1440, "height": 900}, locale="ko-KR",
                                    timezone_id="Asia/Seoul")
    cal_pc_ctx.route("https://script.google.com/**", mock_route)
    cal_pc = new_page(cal_pc_ctx, "calpc")
    cal_ph_ctx = browser.new_context(service_workers="block", viewport={"width": 390, "height": 844}, is_mobile=True,
                                     has_touch=True, locale="ko-KR", timezone_id="Asia/Seoul")
    cal_ph_ctx.route("https://script.google.com/**", mock_route)
    cal_ph = new_page(cal_ph_ctx, "calph")
    check("15_initial_names", cal_names(cal_pc) == ["개인", "가족"], str(cal_names(cal_pc)))

    rename_cal(cal_pc, "personal", "업무")
    check("15_sheet_renamed", wait_until(lambda: BACK.calendars[0]["name"] == "업무", timeout=20), str(BACK.calendars))
    check("15_sheet_timestamp", bool(BACK.calendars[0].get("updatedAt")), str(BACK.calendars[0]))
    check("15_pc_local_kept", cal_names(cal_pc)[0] == "업무", str(cal_names(cal_pc)))
    check("15_pc_ui_kept", "업무" in cal_pc.inner_text("#calList"), cal_pc.inner_text("#calList"))
    check("15_pending_cleared", not stored(cal_pc, "pcal.calsync.v1").get("pending"),
          str(stored(cal_pc, "pcal.calsync.v1")))

    # 1분 주기·포커스 동기화가 여러 번 돌아도 시트의 옛 이름으로 되돌아가지 않아야 합니다.
    for _ in range(3):
        cal_pc.evaluate("window.dispatchEvent(new Event('focus'))")
        cal_pc.wait_for_timeout(800)
    check("15_pc_after_repulls", cal_names(cal_pc)[0] == "업무", str(cal_names(cal_pc)))
    check("15_sheet_after_repulls", BACK.calendars[0]["name"] == "업무", str(BACK.calendars))

    foreground(cal_ph)
    check("15_phone_gets_rename", wait_until(lambda: cal_names(cal_ph)[0] == "업무", timeout=20), str(cal_names(cal_ph)))
    check("15_phone_ui", "업무" in cal_ph.inner_text("#calList"), cal_ph.inner_text("#calList"))
    cal_ph.screenshot(path=str(OUT / "03-phone-calendar-renamed.png"))

    cal_pc.reload(wait_until="load")
    cal_pc.wait_for_timeout(1600)
    check("15_pc_after_reload", cal_names(cal_pc)[0] == "업무", str(cal_names(cal_pc)))

    # ---------------------------------------------------------------- 16. 연결 전에 고쳐 둔 이름을 시트 기본값이 덮지 않는가
    BACK.reset([ev("e-pre-1", "연결 전 일정", "2026-11-28T09:00", "2026-11-28T10:00")])
    pre_ctx = browser.new_context(service_workers="block", viewport={"width": 1440, "height": 900}, locale="ko-KR",
                                 timezone_id="Asia/Seoul")
    pre_ctx.route("https://script.google.com/**", mock_route)
    pre_cals = [
        {"id": "personal", "name": "내 업무", "color": "#039be5", "visible": True, "order": 0},
        {"id": "family", "name": "가족", "color": "#33b679", "visible": True, "order": 1},
    ]
    seed_raw(pre_ctx, [ev("e-pre-1", "연결 전 일정", "2026-11-28T09:00", "2026-11-28T10:00")], pre_cals, url=None)
    pre = pre_ctx.new_page()
    PUMP[0] = pre
    wire_console(pre, "pre")
    pre.goto(f"{BASE}/index.html", wait_until="load")
    pre.wait_for_timeout(1000)
    check("16_local_name_before_connect", cal_names(pre)[0] == "내 업무", str(cal_names(pre)))
    check("16_no_pull_before_connect", stored(pre, "pcal.sync.v1").get("lastPull", 0) == 0,
          str(stored(pre, "pcal.sync.v1")))
    pre.click("#settingsBtn")
    pre.wait_for_timeout(300)
    pre.fill("#setUrl", MOCK_URL)
    pre.click("#setConnect")
    check("16_sheet_adopts_local_name", wait_until(lambda: BACK.calendars[0]["name"] == "내 업무", timeout=25),
          str(BACK.calendars))
    check("16_local_kept_after_connect", cal_names(pre)[0] == "내 업무", str(cal_names(pre)))
    check("16_sheet_events_kept", "e-pre-1" in BACK.events, str(list(BACK.events)))

    # ---------------------------------------------------------------- 17. 오프라인 이름 변경 → 대기열 → 복구 후 반영
    sheet_before = BACK.calendars[0]["name"]
    BACK.fail = True
    rename_cal(cal_pc, "personal", "오프라인 이름")
    check("17_local_kept", cal_names(cal_pc)[0] == "오프라인 이름", str(cal_names(cal_pc)))
    check("17_pending", bool(stored(cal_pc, "pcal.calsync.v1").get("pending")),
          str(stored(cal_pc, "pcal.calsync.v1")))
    check("17_error_state", cal_pc.evaluate("document.querySelector('#syncChip').classList.contains('error')"))
    check("17_sheet_kept_old", BACK.calendars[0]["name"] == sheet_before,
          f"{sheet_before} → {BACK.calendars[0]['name']}")
    cal_pc.evaluate("window.dispatchEvent(new Event('focus'))")
    cal_pc.wait_for_timeout(1600)
    check("17_not_reverted_while_pending", cal_names(cal_pc)[0] == "오프라인 이름", str(cal_names(cal_pc)))
    BACK.fail = False
    foreground(cal_pc)
    check("17_flushed_to_sheet", wait_until(lambda: BACK.calendars[0]["name"] == "오프라인 이름", timeout=25),
          str(BACK.calendars))
    check("17_pending_cleared", wait_until(lambda: not stored(cal_pc, "pcal.calsync.v1").get("pending"), timeout=25),
          str(stored(cal_pc, "pcal.calsync.v1")))
    foreground(cal_ph)
    check("17_phone_updated", wait_until(lambda: cal_names(cal_ph)[0] == "오프라인 이름", timeout=20), str(cal_names(cal_ph)))

    # ---------------------------------------------------------------- 18. 캘린더 삭제 → 시트·다른 기기, 되살아나지 않음
    open_cal_editor(cal_pc, "family")
    cal_pc.click("#cDelete")
    cal_pc.wait_for_timeout(300)
    cal_pc.click("#cbOk")
    cal_pc.wait_for_timeout(900)
    check("18_sheet_removed", wait_until(lambda: all(c["id"] != "family" for c in BACK.calendars), timeout=20),
          str(BACK.calendars))
    check("18_pc_removed", "family" not in cal_ids(cal_pc), str(cal_ids(cal_pc)))
    foreground(cal_ph)
    check("18_phone_removed", wait_until(lambda: "family" not in cal_ids(cal_ph), timeout=20), str(cal_ids(cal_ph)))
    for _ in range(2):
        cal_pc.evaluate("window.dispatchEvent(new Event('focus'))")
        cal_pc.wait_for_timeout(900)
        foreground(cal_ph)
    check("18_no_resurrect", all(c["id"] != "family" for c in BACK.calendars), str(BACK.calendars))
    check("18_phone_no_resurrect", "family" not in cal_ids(cal_ph), str(cal_ids(cal_ph)))

    # ---------------------------------------------------------------- 19. 캘린더 추가 → 다른 기기에 나타남
    cal_pc.evaluate("document.querySelector('[data-addcal]').click()")
    cal_pc.wait_for_timeout(300)
    cal_pc.fill("#cName", "아이들")
    cal_pc.click("#cSave")
    check("19_sheet_added", wait_until(lambda: any(c["name"] == "아이들" for c in BACK.calendars), timeout=20),
          str(BACK.calendars))
    foreground(cal_ph)
    check("19_phone_added", wait_until(lambda: "아이들" in cal_names(cal_ph), timeout=20), str(cal_names(cal_ph)))
    cal_pc.screenshot(path=str(OUT / "04-pc-calendar-list.png"))

    # ---------------------------------------------------------------- 20. 육아 일정 동기화 (PC → 휴대폰, 프로필, 오프라인)
    cal_pc.close(); cal_pc_ctx.close(); cal_ph.close(); cal_ph_ctx.close()
    BACK.reset([])
    TODAY = __import__("datetime").date.today().isoformat()

    k_ph_ctx = browser.new_context(service_workers="block", viewport={"width": 390, "height": 844}, is_mobile=True,
                                   has_touch=True, locale="ko-KR", timezone_id="Asia/Seoul")
    k_ph_ctx.route("https://script.google.com/**", mock_route)
    kph = new_page(k_ph_ctx, "kidph")
    k_pc_ctx = browser.new_context(service_workers="block", viewport={"width": 1440, "height": 900}, locale="ko-KR",
                                   timezone_id="Asia/Seoul")
    k_pc_ctx.route("https://script.google.com/**", mock_route)
    kpc = new_page(k_pc_ctx, "kidpc")
    check("20_profile_pushed_on_first_load",
          wait_until(lambda: len(BACK.kids_children) == 2, timeout=15), str(BACK.kids_children))
    check("20_default_names", [c["name"] for c in BACK.kids_children] == ["아이 1", "아이 2"],
          str(BACK.kids_children))

    kid_add(kpc, "태권도", TODAY)
    check("20_sheet_has_kid_event",
          wait_until(lambda: any(e.get("title") == "태권도" for e in BACK.kids_events.values()), timeout=15),
          str(list(BACK.kids_events.values())))
    check("20_phone_before_pull", "태권도" not in kid_titles(kph), f"titles={kid_titles(kph)}")
    foreground(kph)
    check("20_phone_picks_up_kid_event", wait_until(lambda: "태권도" in kid_titles(kph), timeout=15),
          f"titles={kid_titles(kph)}")
    kph.click('#tabBar [data-tab="kidHome"]')
    kph.wait_for_timeout(400)
    check("20_phone_renders_kid_home", "태권도" in kph.inner_text("#kidHome"), kph.inner_text("#kidHome")[:200])
    kph.screenshot(path=str(OUT / "05-phone-kids-home.png"))

    # 아이 이름 변경 → 시트 → 휴대폰
    kpc.click("#settingsBtn")
    kpc.wait_for_timeout(300)
    kpc.fill('#setKids [data-kidname="kid1"]', "첫째")
    kpc.locator('#setKids [data-kidname="kid1"]').press("Tab")
    kpc.wait_for_timeout(500)
    kpc.click("#setClose")
    check("20_sheet_renamed",
          wait_until(lambda: any(c["name"] == "첫째" for c in BACK.kids_children), timeout=20),
          str(BACK.kids_children))
    foreground(kph)
    check("20_phone_gets_kid_name", wait_until(lambda: "첫째" in kid_names(kph), timeout=20), str(kid_names(kph)))
    check("20_phone_kidsbar_name", wait_until(lambda: "첫째" in kph.inner_text("#kidsBar"), timeout=10),
          kph.inner_text("#kidsBar"))
    # 다시 동기화해도 이름이 되돌아가지 않는다
    for _ in range(2):
        kpc.evaluate("window.dispatchEvent(new Event('focus'))")
        kpc.wait_for_timeout(900)
        foreground(kph)
    check("20_name_not_reverted", kid_names(kpc)[0] == "첫째" and "첫째" in kid_names(kph),
          f"pc={kid_names(kpc)} ph={kid_names(kph)}")

    # 휴대폰에서 삭제 → 시트·PC
    kph.locator("#kidHome .kitem", has_text="태권도").first.click()
    kph.wait_for_timeout(400)
    kph.tap("#kDel")
    kph.wait_for_timeout(350)
    kph.tap("#cbOk")
    check("20_sheet_kid_deleted",
          wait_until(lambda: not any(e.get("title") == "태권도" for e in BACK.kids_events.values()), timeout=15),
          str(list(BACK.kids_events.values())))
    kpc.evaluate("window.dispatchEvent(new Event('focus'))")
    check("20_pc_dropped_kid_event", wait_until(lambda: "태권도" not in kid_titles(kpc), timeout=15),
          f"titles={kid_titles(kpc)}")

    # 오프라인 육아 변경 → 대기열 → 복구 후 자동 전송
    BACK.fail = True
    kid_add(kpc, "오프라인 육아", TODAY, "09:00", "09:30")
    check("20_kid_queued_offline", wait_until(lambda: len(kid_pending(kpc)) >= 1, timeout=10), str(kid_pending(kpc)))
    check("20_kid_kept_local", "오프라인 육아" in kid_titles(kpc), f"titles={kid_titles(kpc)}")
    BACK.fail = False
    kpc.evaluate("window.dispatchEvent(new Event('focus'))")
    check("20_kid_pending_flushed", wait_until(lambda: len(kid_pending(kpc)) == 0, timeout=15), str(kid_pending(kpc)))
    check("20_sheet_has_offline_kid",
          wait_until(lambda: any(e.get("title") == "오프라인 육아" for e in BACK.kids_events.values()), timeout=15),
          str(list(BACK.kids_events.values())))
    foreground(kph)
    check("20_phone_gets_offline_kid", wait_until(lambda: "오프라인 육아" in kid_titles(kph), timeout=15),
          f"titles={kid_titles(kph)}")
    kpc.screenshot(path=str(OUT / "06-pc-kids-home.png"))
    kpc.close(); k_pc_ctx.close(); kph.close(); k_ph_ctx.close()

    # ---------------------------------------------------------------- 21. 구버전(v1.1.0) 백엔드 — 색·완료·반복 규칙을 메모 태그로 동기화
    BACK.reset([
        ev("e-old-1", "옛 시트 일정 하나", "2026-11-27T10:00", "2026-11-27T11:00"),
        ev("e-old-2", "옛 시트 반복 일정", "2026-11-26T09:00", "2026-11-26T09:30",
           rrule="FREQ=WEEKLY;BYDAY=MO,TU,TH"),
        ev("e-old-3", "옛 시트 보통 일정", "2026-11-29"),
        ev("e-old-4", "옛 시트 보통 일정 둘", "2026-11-30T14:00", "2026-11-30T15:00"),
    ], version="1.1.0")
    old_pc_ctx = browser.new_context(service_workers="block", viewport={"width": 1440, "height": 900},
                                     locale="ko-KR", timezone_id="Asia/Seoul")
    old_pc_ctx.route("https://script.google.com/**", mock_route)
    old_pc = new_page(old_pc_ctx, "oldpc")
    old_ph_ctx = browser.new_context(service_workers="block", viewport={"width": 390, "height": 844}, is_mobile=True,
                                     has_touch=True, locale="ko-KR", timezone_id="Asia/Seoul")
    old_ph_ctx.route("https://script.google.com/**", mock_route)
    old_ph = new_page(old_ph_ctx, "oldph")
    check("21_old_backend_loaded", wait_until(lambda: len(titles(old_pc)) == 4, timeout=15), f"titles={titles(old_pc)}")
    check("21_old_backend_version", stored(old_pc, "pcal.sync.v1").get("backend") == "1.1.0",
          str(stored(old_pc, "pcal.sync.v1")))

    def memo_of(i):
        return str(BACK.events.get(i, {}).get("memo", ""))

    def has_tag(i):
        return "[[pcal:" in memo_of(i)

    # PC에서 색 고르기 → 구버전 시트에는 『색상』 열이 없어 메모 태그로 실려야 합니다
    old_pc.locator('#monthBody .chip[data-key^="e-old-1@"]').first.click()
    old_pc.wait_for_timeout(300)
    old_pc.click("#popEdit")
    old_pc.wait_for_timeout(300)
    old_pc.click('#mColors [data-evcolor="#d50000"]')
    old_pc.click("#mSave")
    check("21_old_sheet_no_color_column", wait_until(lambda: "color" not in BACK.events.get("e-old-1", {})),
          str(BACK.events.get("e-old-1")))
    check("21_old_sheet_memo_tag", wait_until(lambda: has_tag("e-old-1") and "color=#d50000" in memo_of("e-old-1")),
          memo_of("e-old-1"))
    foreground(old_ph)
    check("21_old_phone_color",
          wait_until(lambda: "#d50000" in (old_ph.locator('#monthBody .chip[data-key^="e-old-1@"]').first.get_attribute("style") or "")),
          old_ph.locator('#monthBody .chip[data-key^="e-old-1@"]').first.get_attribute("style"))
    check("21_old_phone_memo_clean", "pcal" not in old_ph.evaluate(
        "(JSON.parse(localStorage.getItem('pcal.events.v1')||'[]').find(e=>e.id==='e-old-1')||{}).memo||''"),
        old_ph.evaluate("(JSON.parse(localStorage.getItem('pcal.events.v1')||'[]').find(e=>e.id==='e-old-1')||{}).memo||''"))

    # 완료(흑색)도 태그로 — PC에서 완료 표시
    old_pc.locator('#monthBody .chip[data-key^="e-old-3@"]').first.click()
    old_pc.wait_for_timeout(250)
    old_pc.click("#popDone")
    old_pc.wait_for_timeout(500)
    check("21_old_sheet_done_tag", wait_until(lambda: "done=1" in memo_of("e-old-3")), memo_of("e-old-3"))
    foreground(old_ph)
    check("21_old_phone_done",
          wait_until(lambda: "done" in (old_ph.locator('#monthBody .chip[data-key^="e-old-3@"]').first.get_attribute("class") or "")
                     and "#3f4042" in (old_ph.locator('#monthBody .chip[data-key^="e-old-3@"]').first.get_attribute("style") or "")),
          old_ph.locator('#monthBody .chip[data-key^="e-old-3@"]').first.get_attribute("style"))
    check("21_plain_event_no_tag", memo_of("e-old-4") == "", memo_of("e-old-4"))

    # 반복 규칙(요일 다중 + 종료일)도 태그로 — PC에서 월·화·목 + 2026-12-17까지
    old_pc.locator('#monthBody .chip[data-key^="e-old-2@"]').first.click()
    old_pc.wait_for_timeout(250)
    old_pc.click("#popEdit")
    old_pc.wait_for_timeout(300)
    old_pc.select_option("#mRepeat", "custom")
    old_pc.wait_for_timeout(200)
    for d in ("MO", "TU", "TH"):
        btn = old_pc.locator('#mDow [data-kd="%s"]' % d)
        if "on" not in (btn.get_attribute("class") or ""):
            btn.click()
    old_pc.select_option("#mEndType", "until")
    old_pc.fill("#mUntil", "2026-12-17")
    old_pc.wait_for_timeout(150)
    old_pc.click("#mSave")
    check("21_old_sheet_rrule_tag",
          wait_until(lambda: "rrule=" in memo_of("e-old-2") and "20261217" in memo_of("e-old-2")),
          memo_of("e-old-2"))
    foreground(old_ph)
    check("21_old_phone_rrule",
          wait_until(lambda: "FREQ=WEEKLY" in str(([e for e in stored(old_ph, 'pcal.events.v1') if e.get('id') == 'e-old-2'] or [{}])[0].get('rrule'))
                     and "20261217" in str(([e for e in stored(old_ph, 'pcal.events.v1') if e.get('id') == 'e-old-2'] or [{}])[0].get('rrule'))),
          str([e for e in stored(old_ph, 'pcal.events.v1') if e.get('id') == 'e-old-2']))
    # 그 규칙대로 폰 화면에도 월·화·목 회차가 보입니다 — 12월에서 확인(12/1 화, 12/3 목 …)
    old_ph.click("#nextBtn")
    old_ph.wait_for_timeout(500)
    check("21_old_phone_rrule_1210", old_ph.locator('#monthBody .chip[data-key="e-old-2@2026-12-03"]').count() == 1,
          str(old_ph.locator('#monthBody .chip[data-key^="e-old-2@"]').count()))
    check("21_old_phone_not_plain_weekly", old_ph.locator('#monthBody .chip[data-key="e-old-2@2026-12-02"]').count() == 0,
          "수요일 회차가 생기면 안 됩니다")

    # ---------------------------------------------------------------- 22. 반복 일정 수정·삭제 범위(이 일정만 / 이 일정 및 향후 / 모든 일정)
    # (a) PC에서 '이 일정만' 삭제 → 시트 태그(exdates) → 폰에서도 그 회차만 사라짐
    old_pc.click("#nextBtn")
    old_pc.wait_for_timeout(500)
    old_pc.locator('#monthBody .chip[data-key="e-old-2@2026-12-01"]').first.click()
    old_pc.wait_for_timeout(250)
    old_pc.click("#popDel")
    old_pc.wait_for_timeout(300)
    check("22_scope_dialog_shown", old_pc.is_visible("#scope"), "scope dialog")
    old_pc.click('#scopeFoot [data-scope="one"]')
    check("22_one_memo_exdates", wait_until(lambda: "exdates=" in memo_of("e-old-2")), memo_of("e-old-2"))
    foreground(old_ph)
    check("22_one_phone_dropped",
          wait_until(lambda: old_ph.locator('#monthBody .chip[data-key="e-old-2@2026-12-01"]').count() == 0),
          str(old_ph.locator('#monthBody .chip[data-key="e-old-2@2026-12-01"]').count()))
    check("22_one_phone_keeps_others", old_ph.locator('#monthBody .chip[data-key="e-old-2@2026-12-03"]').count() == 1,
          str(old_ph.locator('#monthBody .chip[data-key^="e-old-2@"]').count()))

    # (b) PC에서 '이 일정 및 향후' 수정 → 원본은 12/7까지로 잘리고, 12/8부터 새 시리즈
    old_pc.locator('#monthBody .chip[data-key="e-old-2@2026-12-08"]').first.click()
    old_pc.wait_for_timeout(250)
    old_pc.click("#popEdit")
    old_pc.wait_for_timeout(300)
    old_pc.fill("#mTitle", "옛 시트 반복 일정(이후)")
    old_pc.click("#mSave")
    old_pc.wait_for_timeout(300)
    old_pc.click('#scopeFoot [data-scope="following"]')
    old_pc.wait_for_timeout(700)
    rows = list(BACK.events.values())
    check("22_following_two_sheets",
          wait_until(lambda: len([e for e in BACK.events.values() if "옛 시트 반복 일정" in e.get("title", "")]) == 2),
          str([(e.get("title"), e.get("start")) for e in rows]))
    check("22_following_cut_until",
          wait_until(lambda: "20261207" in memo_of("e-old-2")), memo_of("e-old-2"))
    foreground(old_ph)
    check("22_following_phone_new",
          wait_until(lambda: "옛 시트 반복 일정(이후)" in titles(old_ph)), f"titles={titles(old_ph)}")
    check("22_following_phone_cut",
          wait_until(lambda: "UNTIL=20261207" in str(([e for e in stored(old_ph, 'pcal.events.v1') if e.get('id') == 'e-old-2'] or [{}])[0].get('rrule'))),
          str([e for e in stored(old_ph, 'pcal.events.v1') if e.get('id') == 'e-old-2']))
    old_pc.screenshot(path=str(OUT / "07-pc-old-backend.png"))
    old_ph.screenshot(path=str(OUT / "08-phone-old-backend.png"))
    old_pc.close(); old_pc_ctx.close(); old_ph.close(); old_ph_ctx.close()

    # ---------------------------------------------------------------- 23. 육아 반복 일정 — 요일 다중·종료일, '이 일정만' 수정
    BACK.reset([], version="1.2.0")
    TODAY2 = __import__("datetime").date.today().isoformat()
    k2_pc_ctx = browser.new_context(service_workers="block", viewport={"width": 1440, "height": 900}, locale="ko-KR",
                                    timezone_id="Asia/Seoul")
    k2_pc_ctx.route("https://script.google.com/**", mock_route)
    k2pc = new_page(k2_pc_ctx, "k2pc")
    k2_ph_ctx = browser.new_context(service_workers="block", viewport={"width": 390, "height": 844}, is_mobile=True,
                                    has_touch=True, locale="ko-KR", timezone_id="Asia/Seoul")
    k2_ph_ctx.route("https://script.google.com/**", mock_route)
    k2ph = new_page(k2_ph_ctx, "k2ph")
    k2pc.click('#tabBar [data-tab="kidHome"]')
    k2pc.wait_for_timeout(300)
    k2pc.click("#kidAddBtn")
    k2pc.wait_for_timeout(350)
    k2pc.fill("#kTitle", "육아 반복")
    k2pc.fill("#kDate", TODAY2)
    k2pc.fill("#kStart", "16:00")
    k2pc.fill("#kEnd", "17:00")
    k2pc.select_option("#kRep", "weekly")
    k2pc.wait_for_timeout(250)
    wanted = ("MO", "TU", "TH")
    for d in wanted:
        b = k2pc.locator('#kDow [data-kd="%s"]' % d)
        if "on" not in (b.get_attribute("class") or ""):
            b.click()
    for d in ("SU", "WE", "FR", "SA"):
        b = k2pc.locator('#kDow [data-kd="%s"]' % d)
        if "on" in (b.get_attribute("class") or ""):
            b.click()
    k2pc.select_option("#kEndType", "until")
    k2pc.fill("#kUntil", "2026-12-31")
    k2pc.click("#kSave")
    k2pc.wait_for_timeout(700)
    kid_rules = [e.get("rrule", "") for e in BACK.kids_events.values()]
    check("23_kid_rrule_multi_dow",
          wait_until(lambda: any("BYDAY=MO,TU,TH" in (e.get("rrule") or "") and "UNTIL=20261231" in (e.get("rrule") or "")
                                 for e in BACK.kids_events.values())),
          str(kid_rules))
    foreground(k2ph)
    check("23_phone_kid_rrule",
          wait_until(lambda: any("BYDAY=MO,TU,TH" in (e.get("rrule") or "")
                                 for e in stored(k2ph, "pcal.kids.events.v1"))),
          str([e.get("rrule") for e in stored(k2ph, "pcal.kids.events.v1")]))
    k2ph.click('#tabBar [data-tab="kidWeek"]')
    k2ph.wait_for_timeout(500)
    # 오늘이 무슨 요일이든 월·화·목 회차가 있는 다음 주로 이동해 확인합니다(날짜에 따라 비는 일 방지).
    if k2ph.locator("#kidWeek .kblk[data-kev]").count() == 0:
        k2ph.click('#kidWeek [data-kweek="1"]')
        k2ph.wait_for_timeout(400)
    kid_blocks = k2ph.locator("#kidWeek .kblk[data-kev]").count()
    check("23_phone_kid_week_blocks", kid_blocks >= 1, str(kid_blocks))
    k2ph.screenshot(path=str(OUT / "09-phone-kids-rrule.png"))
    k2pc.close(); k2_pc_ctx.close(); k2ph.close(); k2_ph_ctx.close()

    # ------------------------------------------- 24. 시트에 이미 일정이 있어도, 기기에만 있던 신규 일정은 자동 병합
    BACK.reset([ev("e-auto-1", "시트에 있던 일정", "2026-11-27T09:00", "2026-11-27T10:00")])
    auto_ctx = browser.new_context(service_workers="block", viewport={"width": 1440, "height": 900}, locale="ko-KR",
                                   timezone_id="Asia/Seoul")
    auto_ctx.route("https://script.google.com/**", mock_route)
    seed_extra(auto_ctx, known=["e-auto-1"])
    auto = new_page(auto_ctx, "autodev", events=[
        ev("e-auto-1", "시트에 있던 일정", "2026-11-27T09:00", "2026-11-27T10:00"),
        ev("e-auto-2", "기기에만 있던 일정", "2026-11-29T09:00", "2026-11-29T10:00"),
    ])
    check("24_sheet_gets_local_only", wait_until(lambda: "e-auto-2" in BACK.events, timeout=15), str(list(BACK.events)))
    check("24_sheet_kept_remote", "e-auto-1" in BACK.events, str(list(BACK.events)))
    check("24_local_kept", "기기에만 있던 일정" in titles(auto), f"titles={titles(auto)}")
    check("24_pending_cleared", wait_until(lambda: len(stored(auto, "pcal.pending.v1")) == 0, timeout=10),
          str(stored(auto, "pcal.pending.v1")))
    check("24_no_duplicate", len(titles(auto)) == 2, f"titles={titles(auto)}")

    # 다른 기기에서 지운 일정(시트에서 본 적 있는 ID)은 되살리지 않습니다.
    BACK.events.pop("e-auto-1", None)
    auto.reload(wait_until="load")
    auto.wait_for_timeout(1600)
    check("24_deleted_elsewhere_not_resurrected", "e-auto-1" not in BACK.events, str(list(BACK.events)))
    check("24_local_dropped", "시트에 있던 일정" not in titles(auto), f"titles={titles(auto)}")
    check("24_other_kept", "기기에만 있던 일정" in titles(auto), f"titles={titles(auto)}")
    check("24_no_resurrect_after_repull",
          not wait_until(lambda: "e-auto-1" in BACK.events, timeout=3), str(list(BACK.events)))
    auto.close(); auto_ctx.close()

    # ------------------------------------------- 25. 육아: 시트가 비어 있어도 기기의 아이 이름·육아 일정이 자동으로 올라감
    BACK.reset([])
    k3_ctx = browser.new_context(service_workers="block", viewport={"width": 390, "height": 844}, is_mobile=True,
                                 has_touch=True, locale="ko-KR", timezone_id="Asia/Seoul")
    k3_ctx.route("https://script.google.com/**", mock_route)
    seed_extra(k3_ctx, kid_events=[kid_event("k-local-1", "피아노 학원", TODAY2)],
               kid_children=kid_profile(["첫째", "둘째"]))
    k3 = new_page(k3_ctx, "kidauto")
    check("25_kid_event_uploaded",
          wait_until(lambda: "k-local-1" in BACK.kids_events, timeout=15), str(list(BACK.kids_events)))
    check("25_profile_uploaded",
          wait_until(lambda: [c["name"] for c in BACK.kids_children] == ["첫째", "둘째"], timeout=15),
          str(BACK.kids_children))
    check("25_kid_pending_cleared", wait_until(lambda: len(kid_pending(k3)) == 0, timeout=10), str(kid_pending(k3)))
    check("25_phone_keeps_name", "첫째" in kid_names(k3), str(kid_names(k3)))
    k3.close(); k3_ctx.close()

    # ------------------------------------------- 26. 「이 브라우저 일정 시트로 올리기」가 육아 일정·아이 이름까지 올림
    BACK.reset([ev("e-up-1", "시트에만 있던 일정", "2026-12-05T09:00", "2026-12-05T10:00")],
               kids={"children": [], "events": [kid_event("k-sheet-1", "시트에만 있던 육아", "2026-12-06")]})
    BACK.kids_fail = True                     # 자동 동기화가 먼저 올리지 못하게 해 둡니다
    up_ctx = browser.new_context(service_workers="block", viewport={"width": 1440, "height": 900}, locale="ko-KR",
                                 timezone_id="Asia/Seoul")
    up_ctx.route("https://script.google.com/**", mock_route)
    up = new_page(up_ctx, "uploader", events=[ev("e-up-local", "브라우저에만 있던 일정", "2026-12-07T09:00", "2026-12-07T10:00")])
    kid_add(up, "올리기 테스트", TODAY2)
    check("26_kid_not_yet_on_sheet", "올리기 테스트" not in [e.get("title") for e in BACK.kids_events.values()],
          str(list(BACK.kids_events.values())))
    up.click("#settingsBtn")
    up.wait_for_timeout(300)
    up.fill('#setKids [data-kidname="kid1"]', "첫째")
    up.locator('#setKids [data-kidname="kid1"]').press("Tab")
    up.wait_for_timeout(400)
    BACK.kids_fail = False
    up.click("#setUpload")
    up.wait_for_timeout(300)
    up.click("#cbOk")
    check("26_events_replaced", wait_until(lambda: "e-up-local" in BACK.events and "e-up-1" in BACK.events, timeout=15),
          str(list(BACK.events)))
    check("26_kid_event_uploaded",
          wait_until(lambda: "올리기 테스트" in [e.get("title") for e in BACK.kids_events.values()], timeout=15),
          str(list(BACK.kids_events.values())))
    check("26_kid_sheet_only_kept", "k-sheet-1" in BACK.kids_events, str(list(BACK.kids_events)))
    check("26_profile_uploaded",
          wait_until(lambda: any(c.get("name") == "첫째" for c in BACK.kids_children), timeout=15),
          str(BACK.kids_children))
    check("26_msg", "올렸" in up.inner_text("#setMsg"), up.inner_text("#setMsg"))
    up.close(); up_ctx.close()

    # ---------------------------------------------------------------- 13. 콘솔 오류
    check("14_no_console_errors", not errors, json.dumps(errors[:5], ensure_ascii=False))

    browser.close()

report["errors"] = errors
report["failures"] = failures
print(json.dumps(report, ensure_ascii=False, indent=2))
with open(OUT / "sync-report.json", "w") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
if failures:
    raise SystemExit(1)
