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
  7. 콘솔 오류 0건

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
    {"id": "personal", "name": "개인", "color": "#039be5", "visible": True, "order": 0},
    {"id": "family", "name": "가족", "color": "#33b679", "visible": True, "order": 1},
]


def ev(id, title, start, end=None, allday=False, updated=None):
    return {
        "id": id, "title": title, "start": start, "end": end or start,
        "allDay": allday, "repeat": "none", "calId": "personal", "location": "",
        "memo": "", "googleId": "", "updatedAt": updated or "2026-11-25T00:00:00.000Z",
    }


# ---------------------------------------------------------------- mock 백엔드
class Backend:
    def __init__(self):
        self.events = {}
        self.calendars = [dict(c) for c in CALS]
        self.fail = False
        self.calls = []
        self.lock = threading.Lock()

    def reset(self, events):
        self.events = {e["id"]: dict(e) for e in events}
        self.calendars = [dict(c) for c in CALS]
        self.fail = False
        self.calls = []

    def load(self):
        return {"ok": True, "version": "1.0.0", "events": list(self.events.values()),
                "calendars": self.calendars, "settings": {}}

    def do(self, action, data):
        with self.lock:
            self.calls.append(action)
            if self.fail and action not in ("ping", "load"):
                return {"ok": False, "error": "모의 백엔드 저장 실패"}
            if action in ("ping", "status"):
                return {"ok": True, "version": "1.0.0", "now": "2026-11-26T00:00:00.000Z",
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
                self.events[e["id"]] = e
                return {"ok": True, "id": e["id"], "googleId": e["googleId"]}
            if action == "deleteEvent":
                self.events.pop(data.get("id"), None)
                return {"ok": True, "id": data.get("id")}
            if action == "saveCalendars":
                self.calendars = data.get("calendars") or self.calendars
                return {"ok": True, "count": len(self.calendars)}
            if action == "saveAll":
                events = data.get("events")
                if not isinstance(events, list):
                    return {"ok": False, "error": "일정 목록이 없습니다."}
                if not events and self.events and data.get("clear") is not True:
                    return {"ok": False, "error": "올릴 일정이 없어 지우지 않았습니다."}
                self.events = {e["id"]: dict(e) for e in events if e.get("id")}
                return {"ok": True, "events": len(self.events)}
            if action == "exportToGoogleCalendar":
                return {"ok": True, "googleId": "gid-mock", "calendar": "기본"}
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


def wait_until(fn, timeout=6.0, step=0.15):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
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


def new_page(ctx, tag="main", events=None):
    seed(ctx, events or [])
    page = ctx.new_page()
    wire_console(page, tag)
    page.goto(f"{BASE}/index.html", wait_until="load")
    page.wait_for_timeout(1200)
    return page


def mock_route(route):
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
    pc_ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ko-KR",
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
    ph_ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1,
                                 is_mobile=True, has_touch=True, locale="ko-KR",
                                 timezone_id="Asia/Seoul")
    ph_ctx.route("https://script.google.com/**", mock_route)
    ph = new_page(ph_ctx)
    check("2_phone_initial_load", wait_until(lambda: len(titles(ph)) == 2), f"titles={titles(ph)}")
    ph.screenshot(path=str(OUT / "01-phone-before.png"))

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

    # ---------------------------------------------------------------- 10. 큰 저장은 POST
    many = [ev(f"e-bulk-{i}", f"대량 일정 {i}", "2026-12-01T09:00", "2026-12-01T10:00") for i in range(40)]
    bulk_ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ko-KR")
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

    # ---------------------------------------------------------------- 11. 시트가 빈 경우 이 기기 일정 보존
    BACK.reset([])
    local_only = [
        ev("e-local-1", "이 기기에만 있던 일정", "2026-11-29T09:00", "2026-11-29T10:00"),
        ev("e-local-2", "이 기기에만 있던 일정 둘", "2026-11-30"),
    ]
    keep_ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="ko-KR")
    keep_ctx.route("https://script.google.com/**", mock_route)
    keep = new_page(keep_ctx, "keep", events=local_only)
    keep.wait_for_timeout(1500)
    check("11_local_kept_when_sheet_empty", len(titles(keep)) == 2, f"titles={titles(keep)}")
    check("11_sheet_not_overwritten", len(BACK.events) == 0, str(BACK.events))
    check("11_hint_shown", "비어" in keep.inner_text("#setSyncInfo") or "이 기기에만" in keep.inner_text("#setSyncInfo"),
          keep.inner_text("#setSyncInfo"))
    # 이 기기에만 있는 일정 올리기
    keep.click("#settingsBtn")
    keep.wait_for_timeout(300)
    keep.click("#setPushLocal")
    keep.wait_for_timeout(1000)
    check("11_push_local_only", wait_until(lambda: len(BACK.events) == 2), str(BACK.events))

    # ---------------------------------------------------------------- 12. 전체 삭제는 시트에도 반영
    keep.click("#setReset")
    keep.wait_for_timeout(300)
    keep.click("#cbOk")
    check("12_reset_clears_sheet", wait_until(lambda: len(BACK.events) == 0), str(BACK.events))
    check("12_reset_clears_local", len(titles(keep)) == 0, f"titles={titles(keep)}")

    # ---------------------------------------------------------------- 13. 콘솔 오류
    check("13_no_console_errors", not errors, json.dumps(errors[:5], ensure_ascii=False))

    browser.close()

report["errors"] = errors
report["failures"] = failures
print(json.dumps(report, ensure_ascii=False, indent=2))
with open(OUT / "sync-report.json", "w") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
if failures:
    raise SystemExit(1)
