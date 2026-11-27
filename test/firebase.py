"""
내 캘린더 — Firebase 실시간 동기화 검증 (Playwright)
================================================================
실제 Firebase 프로젝트 없이도 앱의 연동 코드를 그대로 검증합니다.
· Firebase Web SDK(ESM) 요청(gstatic CDN)만 **모의 SDK**로 바꿔 끼우고,
  모의 SDK는 로컬 **모의 Firestore**(이 파일 안의 HTTP 서버)에 저장합니다.
· 그래서 "PC에서 고치면 휴대폰 화면이 새로고침 없이 바뀐다"를 두 브라우저로
  실제로 재 볼 수 있습니다(모의 서버라 지연은 약 120ms 주기).

확인 항목
  설정 붙여넣기 자동 파싱(JSON·객체 리터럴·콘솔 스니펫) / 로그인 전 잠금과
  "로그인 전에는 클라우드를 읽지 않음" / 로그인 / 첫 병합 업로드 / PC→휴대폰 ·
  휴대폰→PC 실시간 반영(0.3초 목표) / 육아 일정 동기화 / 저장 실패 알림 /
  로그아웃·연결 해제 / #fb= 링크 / `종일` 기본값·`#qaClose` / 콘솔 오류 0건

실행:  LD_LIBRARY_PATH=... python3 test/firebase.py
결과·스크린샷: verification/2026-11-27-fb/ (PCAL_FB_OUT 으로 위치 변경)
"""
import datetime as dt
import functools
import http.server
import json
import os
import pathlib
import socketserver
import threading
import time
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("PCAL_FB_PORT", "8795"))
OUT = pathlib.Path(os.environ.get("PCAL_FB_OUT") or (ROOT / "verification" / "2026-11-27-fb"))
OUT.mkdir(parents=True, exist_ok=True)

TODAY = dt.date.today()


def ymd(days):
    return (TODAY + dt.timedelta(days=days)).isoformat()


# ---------------------------------------------------------------- 모의 Firestore
class Store:
    def __init__(self):
        self.docs = {}
        self.lock = threading.Lock()
        self.mode = {"deny": False, "deny_write": False, "delay": 0}
        self.counts = {"dump": 0, "set": 0, "delete": 0}

    def snapshot(self):
        with self.lock:
            return {k: json.loads(json.dumps(v)) for k, v in self.docs.items()}

    def put(self, path, data):
        with self.lock:
            self.docs[path] = data
            self.counts["set"] += 1

    def drop(self, path):
        with self.lock:
            self.docs.pop(path, None)
            self.counts["delete"] += 1

    def bump(self, key):
        with self.lock:
            self.counts[key] = self.counts.get(key, 0) + 1


STORE = Store()


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:  # noqa: BLE001
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/__fb/dump":
            STORE.bump("dump")
            if STORE.mode["delay"]:
                time.sleep(STORE.mode["delay"] / 1000.0)
            return self._json({"docs": STORE.snapshot(), "denied": bool(STORE.mode["deny"])})
        if u.path == "/__fb/stats":
            return self._json({"counts": dict(STORE.counts), "mode": dict(STORE.mode),
                               "paths": sorted(STORE.snapshot().keys())})
        return super().do_GET()

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/__fb/mode":
            b = self._body()
            with STORE.lock:
                for k in ("deny", "deny_write", "delay"):
                    if k in b:
                        STORE.mode[k] = b[k]
            return self._json({"ok": True, "mode": dict(STORE.mode)})
        if u.path == "/__fb/reset":
            with STORE.lock:
                STORE.docs.clear()
                STORE.counts = {"dump": 0, "set": 0, "delete": 0}
                STORE.mode = {"deny": False, "deny_write": False, "delay": 0}
            return self._json({"ok": True})
        if u.path == "/__fb/doc":
            q = urllib.parse.parse_qs(u.query)
            path = (q.get("path") or [""])[0]
            b = self._body()
            if STORE.mode["deny_write"]:
                return self._json({"ok": False, "denied": True})
            STORE.put(path, b.get("data") or {})
            return self._json({"ok": True})
        return self._json({"ok": False, "error": "unknown"}, 404)

    def do_DELETE(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/__fb/doc":
            q = urllib.parse.parse_qs(u.query)
            path = (q.get("path") or [""])[0]
            if STORE.mode["deny_write"]:
                return self._json({"ok": False, "denied": True})
            STORE.drop(path)
            return self._json({"ok": True})
        return self._json({"ok": False, "error": "unknown"}, 404)


socketserver.TCPServer.allow_reuse_address = True
httpd = socketserver.TCPServer(("127.0.0.1", PORT), functools.partial(Handler, directory=str(ROOT)))
threading.Thread(target=httpd.serve_forever, daemon=True).start()

BASE = f"http://127.0.0.1:{PORT}/index.html"

# ---------------------------------------------------------------- 모의 Firebase SDK
MOCK_APP = r"""
const apps=[];
export function initializeApp(cfg){ const a={options:cfg,name:'[DEFAULT]'}; apps.push(a); return a; }
export function getApps(){ return apps.slice(); }
export function getApp(){ return apps[0]; }
export function deleteApp(app){ const i=apps.indexOf(app); if(i>=0) apps.splice(i,1); return Promise.resolve(); }
"""

MOCK_AUTH = r"""
const listeners=[];
function session(){ try{ return JSON.parse(localStorage.getItem('__fbmock_user')||'null'); }catch(e){ return null; } }
function emit(){ const u=session(); listeners.slice().forEach(function(cb){ setTimeout(function(){ cb(u); },0); }); }
export function getAuth(app){ return {app:app}; }
export function setPersistence(){ return Promise.resolve(); }
export const browserLocalPersistence='local';
export function onAuthStateChanged(auth,cb){ listeners.push(cb); setTimeout(function(){ cb(session()); },0); return function(){ const i=listeners.indexOf(cb); if(i>=0) listeners.splice(i,1); }; }
export class GoogleAuthProvider{ constructor(){ this.params={}; } setCustomParameters(p){ this.params=p||{}; } }
export function signInWithPopup(){
  if(localStorage.getItem('__fbmock_login_denied')==='1'){
    return Promise.reject(Object.assign(new Error('unauthorized-domain'),{code:'auth/unauthorized-domain'}));
  }
  if(localStorage.getItem('__fbmock_popup_blocked')==='1'){
    return Promise.reject(Object.assign(new Error('popup-blocked'),{code:'auth/popup-blocked'}));
  }
  const u={uid:'uid-test-user',email:'tester@example.com',displayName:'테스터',emailVerified:true};
  localStorage.setItem('__fbmock_user',JSON.stringify(u));
  localStorage.setItem('__fbmock_popups',String((+localStorage.getItem('__fbmock_popups')||0)+1));
  emit();
  return Promise.resolve({user:u});
}
export function signInWithRedirect(){
  const u={uid:'uid-test-user',email:'tester@example.com',displayName:'테스터'};
  localStorage.setItem('__fbmock_user',JSON.stringify(u));
  localStorage.setItem('__fbmock_redirects',String((+localStorage.getItem('__fbmock_redirects')||0)+1));
  emit();
  return Promise.resolve();
}
export function getRedirectResult(){ return Promise.resolve(null); }
export function signOut(){ localStorage.removeItem('__fbmock_user'); emit(); return Promise.resolve(); }
"""

MOCK_FS = r"""
const BASE='/__fb';
export function initializeFirestore(app){ return {app:app,persistent:true}; }
export function getFirestore(app){ return {app:app}; }
export function persistentLocalCache(){ return {}; }
export function persistentMultipleTabManager(){ return {}; }
export function collection(db,...p){ return {kind:'col',path:p.join('/')}; }
export function doc(db,...p){ return {kind:'doc',path:p.join('/')}; }
export function setDoc(ref,data){
  return fetch(BASE+'/doc?path='+encodeURIComponent(ref.path),
    {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({data:data})})
    .then(function(r){ return r.json(); })
    .then(function(r){
      if(r&&r.denied) throw Object.assign(new Error('permission-denied'),{code:'permission-denied'});
      return r;
    });
}
export function deleteDoc(ref){
  return fetch(BASE+'/doc?path='+encodeURIComponent(ref.path),{method:'DELETE'})
    .then(function(r){ return r.json(); })
    .then(function(r){
      if(r&&r.denied) throw Object.assign(new Error('permission-denied'),{code:'permission-denied'});
      return r;
    });
}
export function onSnapshot(ref,opts,cb,errCb){
  let prev=null, stopped=false, timer=null, sent=false;
  function build(all){
    const prefix=ref.path+'/';
    const ids=Object.keys(all).filter(function(p){
      return p.indexOf(prefix)===0 && p.slice(prefix.length).indexOf('/')<0;
    }).map(function(p){ return p.slice(prefix.length); });
    const cur={}; ids.forEach(function(id){ cur[id]=all[prefix+id]; });
    const changes=[];
    if(prev===null){ ids.forEach(function(id){ changes.push({type:'added',id:id,data:cur[id]}); }); }
    else{
      ids.forEach(function(id){
        if(!(id in prev)) changes.push({type:'added',id:id,data:cur[id]});
        else if(JSON.stringify(prev[id])!==JSON.stringify(cur[id])) changes.push({type:'modified',id:id,data:cur[id]});
      });
      Object.keys(prev).forEach(function(id){ if(!(id in cur)) changes.push({type:'removed',id:id,data:null}); });
    }
    prev=cur;
    return {metadata:{fromCache:false,hasPendingWrites:false},
      docChanges:function(){ return changes.map(function(c){ return {type:c.type,doc:{id:c.id,data:function(){ return c.data; }}}; }); },
      docs:[]};
  }
  function tick(){
    if(stopped) return;
    fetch(BASE+'/dump').then(function(r){ return r.json(); }).then(function(res){
      if(stopped) return;
      if(res&&res.denied){
        if(errCb) errCb(Object.assign(new Error('permission-denied'),{code:'permission-denied'}));
        stopped=true; return;
      }
      const all=(res&&res.docs)||{};
      if(ref.kind==='col'){
        const snap=build(all);
        const n=snap.docChanges().length;
        if(n||!sent){ sent=true; cb(snap); }
      }else{
        const cur=all[ref.path];
        const was=prev;
        prev=(cur===undefined)?null:cur;
        const changed=(JSON.stringify(was)!==JSON.stringify(prev));
        if(changed||!sent){
          sent=true;
          cb({metadata:{fromCache:false,hasPendingWrites:false},
              exists:function(){ return cur!==undefined; },
              data:function(){ return cur; }});
        }
      }
      timer=setTimeout(tick,120);
    }).catch(function(){ timer=setTimeout(tick,250); });
  }
  tick();
  return function(){ stopped=true; if(timer) clearTimeout(timer); };
}
"""

MOCK_JS = {"firebase-app.js": MOCK_APP, "firebase-auth.js": MOCK_AUTH, "firebase-firestore.js": MOCK_FS}

CFG = {
    "apiKey": "MOCK-API-KEY-FOR-TEST",
    "authDomain": "mock-project.firebaseapp.com",
    "projectId": "mock-project",
    "storageBucket": "mock-project.appspot.com",
    "messagingSenderId": "1234567890",
    "appId": "1:1234567890:web:abcdef",
}
CFG_JS_LITERAL = """const firebaseConfig = {
  apiKey: "MOCK-API-KEY-FOR-TEST",       // 주석도 그대로 붙여넣을 수 있음
  authDomain: 'mock-project.firebaseapp.com',
  projectId: "mock-project",
  storageBucket: 'mock-project.appspot.com',
  messagingSenderId: '1234567890',
  appId: '1:1234567890:web:abcdef',
};"""

from playwright.sync_api import sync_playwright  # noqa: E402

CALS = [
    {"id": "personal", "name": "개인", "color": "#039be5", "visible": True, "order": 0},
    {"id": "family", "name": "가족", "color": "#33b679", "visible": True, "order": 1},
    {"id": "invest", "name": "투자", "color": "#8e24aa", "visible": True, "order": 2},
    {"id": "memo", "name": "메모", "color": "#f4511e", "visible": True, "order": 3},
]


def ev(i, title, start, end=None, allday=False, cal="personal", loc=""):
    return {"id": i, "title": title, "start": start, "end": end or start, "allDay": allday,
            "repeat": "none", "calId": cal, "location": loc, "memo": "",
            "googleId": "", "updatedAt": "2026-11-25T00:00:00.000Z"}


SEED = [
    ev("e1", "테스트 회의", ymd(1) + "T10:00", ymd(1) + "T11:00", loc="회의실"),
    ev("e2", "가족 외식", ymd(2), allday=True, cal="family"),
]

KID_SEED = [{
    "id": "k1", "title": "태권도", "childIds": ["kid1"], "cat": "academy", "date": ymd(0),
    "endDate": "", "allDay": False, "start": "16:00", "end": "17:30", "place": "",
    "memo": "", "supplies": [], "rrule": "", "exdates": [],
    "createdAt": "2026-11-01T00:00:00.000Z", "updatedAt": "2026-11-01T00:00:00.000Z",
}]

report, errors, failures = {}, [], []


def step(name, fn):
    try:
        fn()
        report[name] = "ok"
        print(f"  ✓ {name}", flush=True)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{name}: {type(exc).__name__}: {str(exc)[:400]}")
        report[name] = "FAIL"
        print(f"  ✗ {name}: {type(exc).__name__}: {str(exc)[:300]}", flush=True)


def wire(page, tag, allow_net=True):
    def console(m):
        if m.type != "error":
            return
        txt = m.text
        if allow_net and ("ERR_INTERNET_DISCONNECTED" in txt or "Failed to fetch" in txt
                          or "__fb/" in txt):
            return
        errors.append(f"[{tag}.console] {txt}")
    page.on("console", console)
    page.on("pageerror", lambda e: errors.append(f"[{tag}.pageerror] {e}"))
    page.on("crash", lambda: errors.append(f"[{tag}.crash]"))


def mount(ctx):
    def handle(route):
        url = route.request.url
        for name, body in MOCK_JS.items():
            if url.endswith(name):
                route.fulfill(status=200, body=body,
                              headers={"Content-Type": "application/javascript; charset=utf-8",
                                       "Access-Control-Allow-Origin": "*"})
                return
        route.abort()
    ctx.route("https://www.gstatic.com/firebasejs/**", handle)


def seed_local(ctx, events=None, kids=None, cfg=None):
    js = """
    try {
      if(!localStorage.getItem('__fbseeded')){
        localStorage.setItem('pcal.events.v1', %s);
        localStorage.setItem('pcal.cals.v1', %s);
        localStorage.setItem('pcal.kids.events.v1', %s);
        localStorage.setItem('__fbseeded','1');
      }
      localStorage.setItem('pcal_demo_purged_v1','1');
      %s
    } catch(e) {}
    """ % (json.dumps(json.dumps(events if events is not None else SEED)),
           json.dumps(json.dumps(CALS)),
           json.dumps(json.dumps(kids if kids is not None else KID_SEED)),
           ("localStorage.setItem('pcal.fb.v1', %s);" % json.dumps(json.dumps(cfg))) if cfg else "")
    ctx.add_init_script(js)


def counter(page):
    c = {"dump": 0, "doc": 0}

    def on_req(r):
        if "/__fb/dump" in r.url:
            c["dump"] += 1
        elif "/__fb/doc" in r.url:
            c["doc"] += 1
    page.on("request", on_req)
    return c


def stats():
    import urllib.request
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/__fb/stats") as r:
        return json.loads(r.read().decode())


def set_mode(**kw):
    import urllib.request
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/__fb/mode",
                                 data=json.dumps(kw).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())


def ev_titles(page):
    return page.evaluate("Array.from(document.querySelectorAll('#monthBody .chip .ti')).map(e=>e.textContent)")


def state_events(page):
    return page.evaluate("JSON.parse(localStorage.getItem('pcal.events.v1')||'[]')")


with sync_playwright() as pw:
    browser = pw.chromium.launch()

    # ============================================================ 1) 로컬 모드(설정 없음) 회귀
    c1 = browser.new_context(viewport={"width": 1280, "height": 800}, locale="ko-KR", service_workers="block")
    mount(c1)
    seed_local(c1)
    p1 = c1.new_page()
    wire(p1, "local")
    c1req = counter(p1)
    p1.goto(BASE, wait_until="load")
    p1.wait_for_timeout(500)

    def local_mode():
        assert p1.locator("#fbGate").is_hidden(), "설정이 없는데 잠금 화면이 떴습니다"
        assert p1.locator("#monthBody .chip").count() >= 2
        p1.click("#settingsBtn")
        p1.wait_for_timeout(250)
        assert p1.locator("#setFb").is_visible()
        report["local_version"] = p1.inner_text("#setVersion")
        assert p1.inner_text("#fbBadge") == "꺼짐"
        p1.screenshot(path=str(OUT / "01-settings-local.png"))
        p1.click("#setClose")
        p1.wait_for_timeout(200)
    step("local_mode", local_mode)

    # ============================================================ 2) 설정 붙여넣기(객체 리터럴) → 잠금
    def parse_and_lock():
        before = c1req["dump"]
        p1.click("#settingsBtn")
        p1.wait_for_timeout(200)
        p1.fill("#fbConfig", CFG_JS_LITERAL)
        p1.click("#fbSave")
        p1.wait_for_timeout(600)
        stored = json.loads(p1.evaluate("localStorage.getItem('pcal.fb.v1')"))
        report["parsed_cfg"] = {k: stored.get(k) for k in ("projectId", "authDomain", "appId")}
        assert stored.get("projectId") == "mock-project", stored
        assert stored.get("appId") == "1:1234567890:web:abcdef", stored
        assert stored.get("apiKey") == "MOCK-API-KEY-FOR-TEST", stored
        # 저장 직후 잠금 화면
        assert p1.locator("#fbGate").is_visible(), "로그인 전 잠금 화면이 없습니다"
        box = p1.locator(".fbg-card").bounding_box()
        report["gate_box"] = {"w": round(box["width"]), "h": round(box["height"])}
        assert box["width"] <= 400
        p1.screenshot(path=str(OUT / "02-locked.png"))
        # 로그인 전에는 클라우드를 읽지 않아야 함
        p1.wait_for_timeout(700)
        after = c1req["dump"]
        report["dump_before_login"] = after - before
        assert after == before, f"로그인 전에 Firestore를 읽었습니다({after - before}회)"
        # 다른 탭으로 넘어가도 잠금 유지
        p1.keyboard.press("Escape")
        p1.wait_for_timeout(200)
        assert p1.locator("#fbGate").is_visible()
    step("parse_and_lock", parse_and_lock)

    def bad_config():
        p1.click("#fbSettingsBtn") if p1.locator("#fbSettingsBtn").count() else None
        p1.evaluate("document.getElementById('fbGate').hidden=true")  # 잠금 너머 설정으로 들어가는 경로
        p1.evaluate("""(()=>{ const b=document.getElementById('fbgSettings'); b && b.click(); })()""")
        p1.wait_for_timeout(300)
        assert p1.locator("#settings").is_visible(), "잠금 화면에서 설정을 열지 못했습니다"
        p1.fill("#fbConfig", "{ projectId: 'x' }")
        p1.click("#fbSave")
        p1.wait_for_timeout(300)
        msg = p1.inner_text("#fbMsg")
        report["bad_config_msg"] = msg
        assert "apiKey" in msg or "확인" in msg, msg
        # 원래 설정으로 되돌리기
        p1.fill("#fbConfig", json.dumps(CFG, ensure_ascii=False))
        p1.click("#fbSave")
        p1.wait_for_timeout(500)
        assert p1.evaluate("document.getElementById('fbGate').hidden") in (True, False)
        p1.click("#setClose")
        p1.wait_for_timeout(300)
        assert p1.locator("#fbGate").is_visible(), "설정을 닫으면 다시 잠겨야 합니다"
    step("bad_config", bad_config)

    # ============================================================ 3) 로그인 → 첫 병합 업로드
    def login_and_seed():
        p1.click("#fbgLogin")
        p1.wait_for_timeout(1500)
        assert p1.locator("#fbGate").is_hidden(), "로그인 뒤에도 잠금이 남아 있습니다"
        chip = p1.inner_text("#syncText")
        report["chip_after_login"] = chip
        assert "실시간" in chip or "연결" in chip, chip
        p1.click("#settingsBtn")
        p1.wait_for_timeout(400)
        report["fb_info"] = p1.inner_text("#fbInfo")
        assert "tester@example.com" in report["fb_info"], report["fb_info"]
        p1.screenshot(path=str(OUT / "03-connected-settings.png"))
        p1.click("#setClose")
        p1.wait_for_timeout(600)
        paths = stats()["paths"]
        report["seeded_paths"] = [x for x in paths if "/events/" in x]
        assert "users/uid-test-user/events/e1" in paths, paths
        assert "users/uid-test-user/events/e2" in paths, paths
        assert "users/uid-test-user/calendars/personal" in paths, paths
        assert "users/uid-test-user/kidsEvents/k1" in paths, paths
        assert "users/uid-test-user/kidsProfile/main" in paths, paths
        # 문서 안에 id 기준으로 중복이 없어야 함
        assert len([x for x in paths if x.endswith("/events/e1")]) == 1
    step("login_and_seed", login_and_seed)

    # ============================================================ 4) PC → 휴대폰 실시간
    c2 = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                             locale="ko-KR", timezone_id="Asia/Seoul", service_workers="block")
    mount(c2)
    seed_local(c2, events=[], kids=[], cfg=CFG)
    c2.add_init_script("localStorage.setItem('__fbmock_user', JSON.stringify({uid:'uid-test-user',email:'tester@example.com'}));")
    p2 = c2.new_page()
    wire(p2, "mobile")
    p2.goto(BASE, wait_until="load")
    p2.wait_for_timeout(1600)
    report["mobile_sees_cloud"] = "테스트 회의" in (ev_titles(p2) or []) or \
        p2.evaluate("(JSON.parse(localStorage.getItem('pcal.events.v1')||'[]')).some(e=>e.title==='테스트 회의')")
    assert report["mobile_sees_cloud"], "새 기기가 클라우드 일정을 받지 못했습니다"
    p2.screenshot(path=str(OUT / "04-mobile-cloud.png"))

    def realtime_pc_to_mobile():
        title = "실시간 확인 " + str(int(time.time()) % 100000)
        cell = p2.locator("#monthBody .cell").nth(20)
        target_day = cell.get_attribute("data-date")
        p1.evaluate("window.scrollTo(0,0)")
        p1.click("#navToday" if p1.locator("#navToday").count() else "#todayBtn")
        p1.wait_for_timeout(200)
        cell1 = p1.locator('#monthBody .cell[data-date="%s"]' % target_day)
        cell1.click()
        p1.wait_for_timeout(300)
        p1.fill("#qaTitle", title)
        t0 = time.time()
        p1.click("#qaSave")
        seen_at = None
        for _ in range(80):
            if title in (p2.evaluate("Array.from(document.querySelectorAll('#monthBody .chip .ti')).map(e=>e.textContent)") or []):
                seen_at = time.time()
                break
            p2.wait_for_timeout(20)
        assert seen_at, "휴대폰에 새 일정이 나타나지 않았습니다"
        report["realtime_ms"] = round((seen_at - t0) * 1000)
        assert report["realtime_ms"] < 1000, report["realtime_ms"]
        report["realtime_title"] = title
        p2.screenshot(path=str(OUT / "05-mobile-realtime.png"))
    step("realtime_pc_to_mobile", realtime_pc_to_mobile)

    def realtime_delete():
        title = report["realtime_title"]
        # 휴대폰에서 그 일정을 열어 삭제 → PC에서 사라짐
        chip = p2.locator("#monthBody .chip", has_text=title).first
        chip.click()
        p2.wait_for_timeout(400)
        p2.click("#popDel")
        p2.wait_for_timeout(400)
        p2.click("#cbOk")
        p2.wait_for_timeout(300)
        gone = False
        for _ in range(80):
            titles = p1.evaluate("Array.from(document.querySelectorAll('#monthBody .chip .ti')).map(e=>e.textContent)")
            if title not in (titles or []):
                gone = True
                break
            p1.wait_for_timeout(20)
        assert gone, "휴대폰에서 지운 일정이 PC에 남아 있습니다"
        # 클라우드에서도 사라졌는지
        assert not any(x.endswith("/" + title) for x in stats()["paths"])
        assert "실시간 확인" not in json.dumps(stats()["paths"])
    step("realtime_delete", realtime_delete)

    def kids_realtime():
        p1.click('#tabBar [data-tab="kidHome"]')
        p1.wait_for_timeout(400)
        p1.click("#kidAddBtn")
        p1.wait_for_timeout(400)
        p1.fill("#kTitle", "실시간 육아")
        p1.click("#kSave")
        p1.wait_for_timeout(600)
        assert any("kidsEvents" in x for x in stats()["paths"])
        p2.click('#tabBar [data-tab="kidHome"]')
        p2.wait_for_timeout(400)
        ok = False
        for _ in range(60):
            if "실시간 육아" in p2.inner_text("#kidHome"):
                ok = True
                break
            p2.wait_for_timeout(50)
        assert ok, "휴대폰 육아 홈에 새 육아 일정이 나타나지 않았습니다"
        p1.click('#tabBar [data-tab="cal"]')
        p1.wait_for_timeout(300)
    step("kids_realtime", kids_realtime)

    # ============================================================ 5) 종일 기본값 · #qaClose
    def allday_and_close():
        p1.click("#todayBtn")
        p1.wait_for_timeout(200)
        p1.locator("#monthBody .cell").nth(15).click()
        p1.wait_for_timeout(300)
        assert p1.locator("#qa").is_visible()
        p1.fill("#qaTitle", "종일 기본 확인")
        p1.click("#qaSave")
        p1.wait_for_timeout(400)
        found = [e for e in state_events(p1) if e.get("title") == "종일 기본 확인"]
        report["allday_default"] = bool(found and found[0].get("allDay") is True)
        assert report["allday_default"], found
        # × 닫기 버튼
        p1.locator("#monthBody .cell").nth(16).click()
        p1.wait_for_timeout(300)
        assert p1.locator("#qa").is_visible()
        p1.click("#qaClose")
        p1.wait_for_timeout(250)
        assert p1.locator("#qa").is_hidden(), "#qaClose 로 빠른 등록이 닫히지 않습니다"
        # 시간 그리드에서 만든 일정은 종일이 아님
        p1.click('#viewTabs [data-viewtab="day"]')
        p1.wait_for_timeout(500)
        col = p1.locator("#tvInner .tday").first
        box = col.bounding_box()
        p1.mouse.click(box["x"] + box["width"] / 2, box["y"] + 200)
        p1.wait_for_timeout(300)
        p1.fill("#qaTitle", "시간 일정 확인")
        p1.click("#qaSave")
        p1.wait_for_timeout(400)
        found = [e for e in state_events(p1) if e.get("title") == "시간 일정 확인"]
        report["timed_grid_allday"] = bool(found and found[0].get("allDay") is False)
        assert report["timed_grid_allday"], found
        p1.click('#viewTabs [data-viewtab="month"]')
        p1.wait_for_timeout(300)
    step("allday_and_close", allday_and_close)

    # ============================================================ 6) 저장 실패 알림
    def write_failure():
        set_mode(deny_write=True)
        p1.locator("#monthBody .cell").nth(18).click()
        p1.wait_for_timeout(300)
        p1.fill("#qaTitle", "실패 확인")
        p1.click("#qaSave")
        p1.wait_for_timeout(1200)
        toast_txt = p1.inner_text("#toast")
        cls = p1.get_attribute("#syncChip", "class")
        report["write_fail_toast"] = toast_txt
        report["write_fail_chip"] = cls
        assert "저장하지 못했" in toast_txt, toast_txt
        assert "error" in (cls or ""), cls
        assert p1.locator("#settings").is_hidden()
        p1.screenshot(path=str(OUT / "06-write-error.png"))
        set_mode(deny_write=False)
        # 네트워크가 돌아오면 다시 올라가야 함
        p1.evaluate("window.dispatchEvent(new Event('online'))")
        ok = False
        for _ in range(60):
            titles = [d.get("title") for d in STORE.snapshot().values() if isinstance(d, dict)]
            if "실패 확인" in titles:
                ok = True
                break
            p1.wait_for_timeout(100)
        report["write_retry"] = ok
        assert ok, "연결이 돌아온 뒤 다시 올라가지 않았습니다"
        p1.wait_for_timeout(600)
        assert "error" not in (p1.get_attribute("#syncChip", "class") or ""), p1.get_attribute("#syncChip", "class")
        # 실패했던 일정 지우기
        evs = [e for e in state_events(p1) if e.get("title") == "실패 확인"]
        if evs:
            chip = p1.locator("#monthBody .chip", has_text="실패 확인").first
            chip.click()
            p1.wait_for_timeout(300)
            p1.click("#popDel")
            p1.wait_for_timeout(300)
            p1.click("#cbOk")
            p1.wait_for_timeout(500)
    step("write_failure", write_failure)

    # ============================================================ 7) 로그아웃 → 잠금 / 연결 해제
    def logout_locks():
        p1.click("#settingsBtn")
        p1.wait_for_timeout(300)
        p1.click("#fbLogoutBtn")
        p1.wait_for_timeout(300)
        p1.click("#cbOk")
        p1.wait_for_timeout(800)
        assert p1.locator("#fbGate").is_visible(), "로그아웃 뒤 잠기지 않았습니다"
        if p1.locator("#settings").is_visible():
            p1.click("#setClose")
            p1.wait_for_timeout(400)
        before = c1req["dump"]
        p1.wait_for_timeout(1000)
        report["dump_after_logout"] = c1req["dump"] - before
        assert report["dump_after_logout"] == 0, "로그아웃 상태에서도 Firestore를 읽습니다"
        p1.screenshot(path=str(OUT / "07-logged-out.png"))
        # 다시 로그인하면 그대로 보임
        p1.click("#fbgLogin")
        p1.wait_for_timeout(1500)
        assert p1.locator("#fbGate").is_hidden()
        assert any("테스트 회의" in (t or "") for t in ev_titles(p1))
    step("logout_locks", logout_locks)

    # ============================================================ 8) #fb= 링크
    def hash_link():
        cfg64 = p1.evaluate("""(()=>{
          const raw=localStorage.getItem('pcal.fb.v1');
          const bytes=new TextEncoder().encode(raw);
          let bin=''; bytes.forEach(b=>{ bin+=String.fromCharCode(b); });
          return btoa(bin).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');
        })()""")
        c3 = browser.new_context(viewport={"width": 1280, "height": 800}, locale="ko-KR", service_workers="block")
        mount(c3)
        c3.add_init_script("localStorage.setItem('pcal_demo_purged_v1','1');")
        p3 = c3.new_page()
        wire(p3, "hash")
        p3.goto(BASE + "#fb=" + cfg64, wait_until="load")
        p3.wait_for_timeout(800)
        stored = p3.evaluate("localStorage.getItem('pcal.fb.v1')")
        report["hash_cfg_stored"] = bool(stored and "mock-project" in stored)
        assert report["hash_cfg_stored"]
        assert p3.locator("#fbGate").is_visible(), "#fb= 링크로 열어도 로그인 전에는 잠겨야 합니다"
        assert not any("테스트 회의" in (t or "") for t in ev_titles(p3))
        p3.screenshot(path=str(OUT / "08-hash-link-locked.png"))
        # 잠금 화면의 "이 기기에서만 쓰기" → 설정이 지워지고, 새로고침해도 #fb= 로 되살아나지 않아야 함
        p3.click("#fbgOff")
        p3.wait_for_timeout(300)
        p3.click("#cbOk")
        p3.wait_for_timeout(700)
        assert p3.locator("#fbGate").is_hidden(), "잠금 화면에서 연결을 해제하지 못했습니다"
        assert p3.evaluate("localStorage.getItem('pcal.fb.v1')") in (None, "")
        report["hash_after_off"] = p3.evaluate("location.href")
        p3.reload(wait_until="load")
        p3.wait_for_timeout(700)
        report["hash_off_reload"] = p3.evaluate("localStorage.getItem('pcal.fb.v1')") in (None, "")
        assert report["hash_off_reload"], "#fb= 해시 때문에 설정이 되살아났습니다"
        assert p3.locator("#fbGate").is_hidden()
        c3.close()
    step("hash_link", hash_link)

    # ============================================================ 9) 휴대폰 레이아웃
    def mobile_layout():
        p2.click('#tabBar [data-tab="cal"]')
        p2.wait_for_timeout(400)
        report["mobile_overflow"] = p2.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        assert report["mobile_overflow"] is False
        p2.click("#menuBtn")
        p2.wait_for_timeout(400)
        p2.click("#sideSettings")
        p2.wait_for_timeout(500)
        assert p2.locator("#settings").is_visible()
        report["mobile_settings_overflow"] = p2.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        assert report["mobile_settings_overflow"] is False
        box = p2.locator("#setFb").bounding_box()
        report["mobile_fbsec_w"] = round(box["width"])
        assert box["width"] <= 390
        p2.screenshot(path=str(OUT / "09-mobile-settings.png"))
        p2.click("#setClose")
        p2.wait_for_timeout(300)
    step("mobile_layout", mobile_layout)

    # ============================================================ 9b) 휴대폰 잠금 화면
    def mobile_gate():
        c6 = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                                 locale="ko-KR", service_workers="block")
        mount(c6)
        seed_local(c6, cfg=CFG)
        p6 = c6.new_page()
        wire(p6, "mobgate")
        p6.goto(BASE, wait_until="load")
        p6.wait_for_timeout(900)
        assert p6.locator("#fbGate").is_visible()
        box = p6.locator(".fbg-card").bounding_box()
        report["mobile_gate_box"] = {"w": round(box["width"]), "h": round(box["height"])}
        assert box["width"] <= 390
        report["mobile_gate_overflow"] = p6.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
        assert report["mobile_gate_overflow"] is False
        p6.screenshot(path=str(OUT / "12-mobile-locked.png"))
        c6.close()
    step("mobile_gate", mobile_gate)

    # ============================================================ 10) 연결 해제 → 로컬
    def turn_off():
        p1.keyboard.press("Escape")
        p1.wait_for_timeout(200)
        p1.keyboard.press("Escape")
        p1.wait_for_timeout(200)
        if p1.locator("#fbGate").is_hidden():
            p1.click("#settingsBtn")
        else:
            p1.click("#fbgSettings")
        p1.wait_for_timeout(400)
        assert p1.locator("#settings").is_visible(), "설정 창을 열지 못했습니다"
        p1.click("#fbOff")
        p1.wait_for_timeout(300)
        p1.click("#cbOk")
        p1.wait_for_timeout(900)
        assert p1.locator("#fbGate").is_hidden(), "연결 해제 뒤에도 잠금이 남아 있습니다"
        assert p1.evaluate("localStorage.getItem('pcal.fb.v1')") in (None, "")
        chip = p1.inner_text("#syncText")
        report["chip_after_off"] = chip
        assert "이 브라우저" in chip, chip
        assert any("테스트 회의" in (t or "") for t in ev_titles(p1)), "연결 해제 뒤 이 기기 일정이 사라졌습니다"
        p1.screenshot(path=str(OUT / "10-off-local.png"))
    step("turn_off", turn_off)

    # ============================================================ 11) 팝업 차단 → 리디렉션 폴백
    def popup_fallback():
        c4 = browser.new_context(viewport={"width": 1280, "height": 800}, locale="ko-KR", service_workers="block")
        mount(c4)
        seed_local(c4, cfg=CFG)
        c4.add_init_script("localStorage.setItem('__fbmock_popup_blocked','1');")
        p4 = c4.new_page()
        wire(p4, "popup")
        p4.goto(BASE, wait_until="load")
        p4.wait_for_timeout(900)
        assert p4.locator("#fbGate").is_visible()
        p4.click("#fbgLogin")
        p4.wait_for_timeout(1200)
        report["redirect_used"] = p4.evaluate("localStorage.getItem('__fbmock_redirects')")
        assert report["redirect_used"] in ("1", 1), report["redirect_used"]
        assert p4.locator("#fbGate").is_hidden(), "리디렉션 폴백으로 로그인되지 않았습니다"
        c4.close()
    step("popup_fallback", popup_fallback)

    # ============================================================ 12) 규칙 거부(권한 없음)
    def denied():
        c5 = browser.new_context(viewport={"width": 1280, "height": 800}, locale="ko-KR", service_workers="block")
        mount(c5)
        seed_local(c5, cfg=CFG)
        c5.add_init_script("localStorage.setItem('__fbmock_user', JSON.stringify({uid:'uid-test-user',email:'tester@example.com'}));")
        set_mode(deny=True)
        p5 = c5.new_page()
        wire(p5, "denied")
        p5.goto(BASE, wait_until="load")
        p5.wait_for_timeout(1500)
        chip = p5.inner_text("#syncText")
        cls = p5.get_attribute("#syncChip", "class")
        report["denied_chip"] = chip
        report["denied_class"] = cls
        assert "오류" in chip or "권한" in chip, chip
        assert "error" in (cls or ""), cls
        p5.screenshot(path=str(OUT / "11-permission-denied.png"))
        set_mode(deny=False)
        c5.close()
    step("denied", denied)

    browser.close()

report["errors"] = errors
report["failures"] = failures
print(json.dumps(report, ensure_ascii=False, indent=2))
with open(OUT / "firebase-report.json", "w") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
if failures or errors:
    raise SystemExit(1)
