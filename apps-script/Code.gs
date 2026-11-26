/**
 * 내 캘린더 — 비공개 구글 시트 백엔드 (Google Apps Script) v1.2.0
 * =============================================================
 * 구글 캘린더 스타일 개인 캘린더 + 육아(아이 일정·준비물·시간표)의 저장소입니다.
 *  · 일정·육아 일정은 이 스프레드시트(비공개)에만 저장됩니다.
 *  · 「📤 구글 캘린더로 보내기」를 누른 일정만 내 구글 캘린더의 **기본 캘린더**로 복사됩니다.
 *
 * 설치 (한 번만, 약 3분)
 *  1. 새 스프레드시트를 만들고 이름을 「[비공개] 내 캘린더」로 정합니다.
 *  2. 메뉴: 확장 프로그램 → Apps Script.
 *  3. 기본 Code.gs 내용을 지우고 이 파일 내용을 그대로 붙여 넣고 저장(Ctrl+S).
 *  4. (권장) 프로젝트 설정 → 시간대가 Asia/Seoul 인지 확인합니다.
 *     · 스프레드시트 설정의 시간대를 그대로 따르므로, 시트를 한국 시간으로 만들어 두면 됩니다.
 *  5. 배포 → 새 배포 → 유형(⚙) = 웹 앱
 *       · 설명: 내 캘린더 백엔드
 *       · 다음 사용자로 실행: 나
 *       · 액세스 권한: 모든 사용자   ← 캘린더 페이지에서 부르려면 반드시 이 값
 *     → 배포 → 액세스 승인(내 구글 계정) → 웹 앱 URL(.../exec) 복사
 *  6. 캘린더 페이지 ⚙ 설정에 그 URL을 붙여 넣고 [연결]을 누릅니다.
 *     · 처음 연결할 때 구글 계정 권한(스프레드시트 + 캘린더)을 한 번 승인합니다.
 *     · "Google hasn't verified this app" 경고가 나오면 고급 → (안전하지 않음)으로 이동 → 허용.
 *
 * 코드를 고친 뒤에는 반드시 배포 → 배포 관리 → 수정(연필) → 버전: 새 버전 → 배포.
 *
 * 시트 구성 (처음 호출할 때 자동으로 만듭니다)
 *  · 일정   : ID | 제목 | 시작 | 종료 | 종일 | 반복 | 캘린더ID | 장소 | 메모 | 구글전송ID | 수정시각 | 색상 | 완료
 *             시작/종료 형식  종일=Y → `2026-11-25` / 시간 일정 → `2026-11-25T15:00`
 *             색상=일정 하나만의 색(`#3f4042` 등, 비어 있으면 캘린더 색) / 완료=Y·N(완료는 흑색 체크로 표시)
 *             ※ v1.2.0 은 기존 시트의 앞 11개 열을 그대로 두고 '색상'·'완료' 열만 끝에 덧붙입니다.
 *               앱을 새로 고쳐도 새 열이 자동으로 생기지만, 두 기기 동기화에 색·완료를 쓰려면
 *               이 Code.gs 를 붙여 넣고 [배포 → 배포 관리 → 수정 → 새 버전]으로 한 번 다시 배포해야 합니다.
 *               (재배포 전에는 앱이 색·완료를 그 기기에만 보관합니다.)
 *  · 캘린더 : ID | 이름 | 색상 | 표시여부 | 순서 | 수정시각
 *             수정시각은 그 캘린더(이름·색상·표시여부·순서)를 마지막으로 고친 시각입니다.
 *             앱은 이 값으로 '시트와 이 기기 중 더 최근 것'을 골라 씁니다(이름이 되돌아가는 것 방지).
 *  · 설정   : 항목 | 값 | 비고
 *  · 육아일정 : ID | 제목 | 아이 | 종류 | 날짜 | 마지막날 | 종일 | 시작 | 끝 | 장소 | 메모 | 준비물 | 반복 | 제외날짜 | 수정시각
 *             아이=`kid1,kid2` / 종류=`academy` 등 / 준비물=JSON / 반복=`FREQ=WEEKLY;BYDAY=MO,WE` /
 *             제외날짜=`2026-11-26,2026-12-03` — 기존 `일정`·`캘린더`·`설정` 탭은 예전 그대로입니다.
 *  · 육아프로필 : ID | 이름 | 색상 | 순서 | 수정시각
 *             아이 이름은 여기에만 저장됩니다(기본값 `아이 1`·`아이 2`, 앱 설정에서 변경).
 *
 * API (브라우저는 JSONP GET, 또는 text/plain POST 로 부릅니다)
 *  ?action=ping                      연결 확인 → {ok, version, now, sheet, timezone}
 *  ?action=load                      전체 읽기 → {ok, events:[...], calendars:[...], settings:{...}}
 *  ?action=saveEvent  {event}        일정 한 건 저장(없으면 추가) — 구글전송ID는 시트 값을 지키지 않습니다(아래 참고)
 *  ?action=deleteEvent {id}          일정 한 건 삭제
 *  ?action=saveCalendars {calendars} 캘린더 목록 전체 저장(이름·색상·표시여부·순서·수정시각)
 *  ?action=saveAll    {events, calendars}
 *                                    이 브라우저의 일정·캘린더를 시트에 통째로 올립니다(최초 이전용).
 *                                    events 가 비어 있고 시트에 일정이 있으면 거부합니다(clear:true 로만 허용).
 *  ?action=saveKidsEvent {event}     육아 일정 한 건 저장(없으면 추가)
 *  ?action=deleteKidsEvent {id}      육아 일정 한 건 삭제
 *  ?action=saveKidsProfile {children} 아이 프로필(이름·색상·순서·수정시각) 전체 저장
 *  ?action=load                      응답에 kids:{children:[...], events:[...]} 도 들어 있습니다.
 *  ?action=exportToGoogleCalendar {event} | {id}
 *                                    내 구글 캘린더 기본 캘린더에 일정을 만들고 eventId를 시트에 기록
 *                                    일정 색은 구글 색 ID로 옮기고, 완료·흑색 일정은 8(그래파이트)로 보냅니다.
 *  ?callback=함수이름                 JSONP 로 감싸서 응답
 *
 * 안전장치
 *  · 쓰기는 LockService 로 한 번에 하나씩 처리합니다.
 *  · saveEvent 는 시트에 이미 구글전송ID가 있으면 덮어쓰지 않습니다(중복 전송 방지).
 *  · exportToGoogleCalendar 는 이미 전송된 일정이면 새로 만들지 않고 기존 eventId 를 돌려줍니다.
 *
 * 개인정보: 이 스크립트는 사용자 본인의 스프레드시트와 본인의 캘린더만 다룹니다.
 *           웹 앱 주소(.../exec)를 아는 사람은 이 시트의 일정을 읽고 쓸 수 있으니 주소는 비공개로 두세요.
 *           (필요하면 배포 접근 권한을 "나"로 바꿔 본인만 쓰는 방식도 가능하지만,
 *            그러면 브라우저에서 JSONP 로 부를 수 없습니다.)
 */

/** 스프레드시트 ID. 시트에 붙인(컨테이너 바인딩) 스크립트라면 비워 두세요. */
var SHEET_ID = '';

var BACKEND_VERSION = '1.2.0';

var TABS = { events: '일정', calendars: '캘린더', settings: '설정', kidsEvents: '육아일정', kidsProfile: '육아프로필' };

var HEADERS = {
  events: ['ID', '제목', '시작', '종료', '종일', '반복', '캘린더ID', '장소', '메모', '구글전송ID', '수정시각', '색상', '완료'],
  calendars: ['ID', '이름', '색상', '표시여부', '순서', '수정시각'],
  settings: ['항목', '값', '비고'],
  kidsEvents: ['ID', '제목', '아이', '종류', '날짜', '마지막날', '종일', '시작', '끝', '장소', '메모', '준비물', '반복', '제외날짜', '수정시각'],
  kidsProfile: ['ID', '이름', '색상', '순서', '수정시각']
};

var DEFAULT_CALENDARS = [
  { id: 'personal', name: '개인', color: '#039be5', visible: true, order: 0 },
  { id: 'family', name: '가족', color: '#33b679', visible: true, order: 1 },
  { id: 'invest', name: '투자', color: '#8e24aa', visible: true, order: 2 },
  { id: 'memo', name: '메모', color: '#f4511e', visible: true, order: 3 }
];

var MAX_EVENTS = 3000;
var MAX_KIDS_EVENTS = 3000;
var MAX_KIDS = 4;

/* ------------------------------------------------------------------ 라우팅 */

function doGet(e) {
  var p = (e && e.parameter) || {};
  var cb = p.callback || '';
  var action = p.action || 'ping';
  var req = {};
  if (p.d) {
    try {
      req = JSON.parse(p.d);
    } catch (err) {
      return out_({ ok: false, error: '요청 내용(d)을 읽지 못했습니다.' }, cb);
    }
  }
  Object.keys(p).forEach(function (k) {
    if (k === 'd' || k === 'callback' || k === 'action') return;
    if (!(k in req)) req[k] = p[k];
  });
  return handle_(action, req, cb);
}

function doPost(e) {
  var body = {};
  try {
    if (e && e.postData && e.postData.contents) body = JSON.parse(e.postData.contents);
  } catch (err) {
    return out_({ ok: false, error: 'POST 본문을 읽지 못했습니다.' }, '');
  }
  return handle_(body.action || 'ping', body, body.callback || '');
}

function handle_(action, req, cb) {
  var lock = null;
  try {
    if (action !== 'ping' && action !== 'load' && action !== 'status') {
      lock = LockService.getScriptLock();
      lock.waitLock(20000);
    }
    setup_();
    switch (action) {
      case 'ping': return out_(ping_(), cb);
      case 'status': return out_(ping_(), cb);
      case 'load': return out_(load_(), cb);
      case 'saveEvent': return out_(saveEvent_(req), cb);
      case 'deleteEvent': return out_(deleteEvent_(req), cb);
      case 'saveCalendars': return out_(saveCalendars_(req), cb);
      case 'saveAll': return out_(saveAll_(req), cb);
      case 'saveKidsEvent': return out_(saveKidsEvent_(req), cb);
      case 'deleteKidsEvent': return out_(deleteKidsEvent_(req), cb);
      case 'saveKidsProfile': return out_(saveKidsProfile_(req), cb);
      case 'exportToGoogleCalendar': return out_(exportToGoogle_(req), cb);
      default: return out_({ ok: false, error: '알 수 없는 요청: ' + action }, cb);
    }
  } catch (err) {
    return out_({ ok: false, error: String((err && err.message) || err) }, cb);
  } finally {
    if (lock) { try { lock.releaseLock(); } catch (e2) {} }
  }
}

/** callback이 있으면 JSONP(스크립트)로, 없으면 JSON으로 응답합니다. */
function out_(obj, cb) {
  var body = JSON.stringify(obj);
  if (cb && /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(cb)) {
    return ContentService
      .createTextOutput(cb + '(' + body + ');')
      .setMimeType(ContentService.MimeType.JAVASCRIPT);
  }
  return ContentService
    .createTextOutput(body)
    .setMimeType(ContentService.MimeType.JSON);
}

/* ------------------------------------------------------------------ 시트 준비 */

function ss_() {
  if (SHEET_ID) return SpreadsheetApp.openById(SHEET_ID);
  var active = SpreadsheetApp.getActiveSpreadsheet();
  if (!active) throw new Error('스프레드시트를 찾지 못했습니다. SHEET_ID를 채워 주세요.');
  return active;
}

function sheet_(name, headers) {
  var ss = ss_();
  var sh = ss.getSheetByName(name);
  if (!sh) sh = ss.insertSheet(name);
  if (sh.getLastRow() === 0) {
    sh.getRange(1, 1, 1, headers.length).setValues([headers]).setFontWeight('bold').setBackground('#f1f3f4');
    sh.setFrozenRows(1);
  } else {
    ensureColumns_(sh, headers);
  }
  return sh;
}

/** 기존 시트에 없던 열(예: 색상·완료)을 지금 열 끝에 덧붙입니다. 있던 열과 그 순서는 건드리지 않습니다. */
function ensureColumns_(sh, headers) {
  var last = Math.max(1, sh.getLastColumn());
  var row = sh.getRange(1, 1, 1, Math.max(last, headers.length)).getValues()[0];
  var have = {};
  for (var i = 0; i < row.length; i++) {
    var k = String(row[i] || '').trim();
    if (k) have[k] = true;
  }
  var missing = [];
  for (var j = 0; j < headers.length; j++) if (!have[headers[j]]) missing.push(headers[j]);
  if (!missing.length) return sh;
  sh.getRange(1, last + 1, 1, missing.length).setValues([missing]).setFontWeight('bold').setBackground('#f1f3f4');
  if (sh.getFrozenRows() === 0) sh.setFrozenRows(1);
  return sh;
}

/** 1행 머리말을 읽어 '열 이름 → 0부터 시작하는 열 번호' 지도를 만듭니다. 없는 열은 -1. */
function headerMap_(sh, headers) {
  var width = Math.max(1, sh.getLastColumn(), headers.length);
  var row = sh.getRange(1, 1, 1, width).getValues()[0];
  var map = {};
  for (var i = 0; i < row.length; i++) {
    var k = String(row[i] || '').trim();
    if (k) map[k] = i;
  }
  for (var j = 0; j < headers.length; j++) if (!(headers[j] in map)) map[headers[j]] = -1;
  return map;
}

function cell_(r, map, name) {
  var i = map[name];
  return (i >= 0 && i < r.length) ? r[i] : '';
}
function truthy_(v) {
  return v === true || String(v || '').toUpperCase() === 'Y' || String(v || '').toUpperCase() === 'TRUE';
}

function setup_() {
  sheet_(TABS.events, HEADERS.events);
  var csh = sheet_(TABS.calendars, HEADERS.calendars);
  if (csh.getLastRow() < 2) writeCalendars_(csh, DEFAULT_CALENDARS);
  var ssh = sheet_(TABS.settings, HEADERS.settings);
  if (ssh.getLastRow() < 2) {
    ssh.getRange(2, 1, 3, 2).setValues([
      ['앱이름', '내 캘린더'],
      ['백엔드버전', BACKEND_VERSION],
      ['시간대', Session.getScriptTimeZone()]
    ]);
  }
  /* 육아(아이 일정·프로필) 탭 — 비어 있으면 만들기만 하고 아무 값도 넣지 않습니다. */
  sheet_(TABS.kidsEvents, HEADERS.kidsEvents);
  sheet_(TABS.kidsProfile, HEADERS.kidsProfile);
}

/** 편집기에서 직접 실행해 시트를 준비할 때 사용합니다. */
function setup() {
  setup_();
  return ping_();
}

function ping_() {
  var ss = ss_();
  return {
    ok: true,
    version: BACKEND_VERSION,
    now: new Date().toISOString(),
    sheet: ss.getName(),
    timezone: ss.getSpreadsheetTimeZone() || Session.getScriptTimeZone(),
    defaultCalendar: (function () {
      try { return CalendarApp.getDefaultCalendar().getName(); } catch (e) { return ''; }
    })()
  };
}

/* ------------------------------------------------------------------ 읽기 */

function load_() {
  var ss = ss_();
  var out = {
    ok: true,
    version: BACKEND_VERSION,
    events: readEvents_(ss),
    calendars: readCalendars_(ss),
    settings: readSettings_(ss),
    kids: { children: readKidsProfile_(ss), events: readKidsEvents_(ss) }
  };
  if (!out.calendars.length) out.calendars = DEFAULT_CALENDARS;
  return out;
}

function readEvents_(ss) {
  var sh = ss.getSheetByName(TABS.events);
  var out = [];
  if (!sh || sh.getLastRow() < 2) return out;
  ensureColumns_(sh, HEADERS.events);          // 색상·완료 열이 없던 시트도 읽기 전에 채워 둡니다
  var map = headerMap_(sh, HEADERS.events);
  var width = Math.max(1, sh.getLastColumn());
  var last = sh.getLastRow();
  var rows = sh.getRange(2, 1, last - 1, width).getValues();
  rows.forEach(function (r) {
    var id = String(cell_(r, map, 'ID') || '').trim();
    if (!id) return;
    out.push({
      id: id,
      title: String(cell_(r, map, '제목') || ''),
      start: toText_(cell_(r, map, '시작')),
      end: toText_(cell_(r, map, '종료')),
      allDay: truthy_(cell_(r, map, '종일')),
      repeat: String(cell_(r, map, '반복') || 'none') || 'none',
      calId: String(cell_(r, map, '캘린더ID') || ''),
      location: String(cell_(r, map, '장소') || ''),
      memo: String(cell_(r, map, '메모') || ''),
      googleId: String(cell_(r, map, '구글전송ID') || ''),
      updatedAt: toText_(cell_(r, map, '수정시각')),
      color: String(cell_(r, map, '색상') || ''),
      done: truthy_(cell_(r, map, '완료'))
    });
  });
  return out;
}

function readCalendars_(ss) {
  var sh = ss.getSheetByName(TABS.calendars);
  var out = [];
  if (!sh || sh.getLastRow() < 2) return out;
  var rows = sh.getRange(2, 1, sh.getLastRow() - 1, HEADERS.calendars.length).getValues();
  rows.forEach(function (r, i) {
    var id = String(r[0] || '').trim();
    if (!id) return;
    out.push({
      id: id,
      name: String(r[1] || '캘린더'),
      color: String(r[2] || '#039be5'),
      visible: String(r[3] || 'Y').toUpperCase() !== 'N',
      order: Number(r[4] || i),
      updatedAt: toIso_(r[5])
    });
  });
  out.sort(function (a, b) { return a.order - b.order; });
  return out;
}

function readSettings_(ss) {
  var sh = ss.getSheetByName(TABS.settings);
  var out = {};
  if (!sh || sh.getLastRow() < 2) return out;
  var rows = sh.getRange(2, 1, sh.getLastRow() - 1, 2).getValues();
  rows.forEach(function (r) {
    var k = String(r[0] || '').trim();
    if (k) out[k] = String(r[1] == null ? '' : r[1]);
  });
  return out;
}

/* ------------------------------------------------------------------ 일정 저장/삭제 */

function saveEvent_(req) {
  var ev = req.event || req;
  if (!ev || !ev.id) return { ok: false, error: '일정 ID가 없습니다.' };
  var sh = sheet_(TABS.events, HEADERS.events);
  var map = headerMap_(sh, HEADERS.events);
  var width = eventWidth_(map);
  var row = findRow_(sh, ev.id);

  var keepGoogleId = '';
  if (row > 0 && map['구글전송ID'] >= 0) keepGoogleId = String(sh.getRange(row, map['구글전송ID'] + 1).getValue() || '');
  var googleId = String(ev.googleId || keepGoogleId || '');

  var values = eventRow_(ev, map, width, googleId);
  if (row > 0) {
    sh.getRange(row, 1, 1, width).setValues([values]);
  } else {
    if (sh.getLastRow() >= MAX_EVENTS + 1) return { ok: false, error: '일정이 너무 많습니다(최대 ' + MAX_EVENTS + '개).' };
    sh.getRange(sh.getLastRow() + 1, 1, 1, width).setValues([values]);
  }
  return { ok: true, id: String(ev.id), googleId: googleId };
}

/** 일정 한 건을 머리말 순서에 맞춰 한 줄로 만듭니다(기존 열·순서를 그대로 따릅니다). */
function eventRow_(ev, map, width, googleId) {
  var values = new Array(width);
  for (var i = 0; i < width; i++) values[i] = '';
  function put(name, v) { var i = map[name]; if (i >= 0 && i < width) values[i] = v; }
  put('ID', String(ev.id || ''));
  put('제목', String(ev.title || ''));
  put('시작', String(ev.start || ''));
  put('종료', String(ev.end || ev.start || ''));
  put('종일', ev.allDay ? 'Y' : 'N');
  put('반복', String(ev.repeat || 'none'));
  put('캘린더ID', String(ev.calId || ''));
  put('장소', String(ev.location || ''));
  put('메모', String(ev.memo || ''));
  put('구글전송ID', String(googleId || ev.googleId || ''));
  put('수정시각', String(ev.updatedAt || new Date().toISOString()));
  put('색상', String(ev.color || ''));
  put('완료', ev.done ? 'Y' : 'N');
  return values;
}
function eventWidth_(map) {
  var w = 1;
  for (var i = 0; i < HEADERS.events.length; i++) {
    var idx = map[HEADERS.events[i]];
    if (idx >= 0) w = Math.max(w, idx + 1);
  }
  return w;
}

function deleteEvent_(req) {
  var id = String(req.id || (req.event && req.event.id) || '');
  if (!id) return { ok: false, error: '삭제할 일정 ID가 없습니다.' };
  var sh = sheet_(TABS.events, HEADERS.events);
  var row = findRow_(sh, id);
  if (row > 0) sh.deleteRow(row);
  return { ok: true, id: id, deleted: row > 0 };
}

function saveCalendars_(req) {
  var list = req.calendars;
  if (!Array.isArray(list)) return { ok: false, error: '캘린더 목록이 없습니다.' };
  if (!list.length) return { ok: false, error: '캘린더가 하나도 없어 저장하지 않았습니다.' };
  var ss = ss_();
  var sh = sheet_(TABS.calendars, HEADERS.calendars);
  // 수정시각을 보내지 않는 예전 앱이, 시트에 있는 더 최신 캘린더 이름을 지우지 않도록 막습니다.
  if (!list.some(function (c) { return calMs_(c) > 0; })) {
    var current = readCalendars_(ss);
    if (current.some(function (c) { return calMs_(c) > 0; })) {
      return {
        ok: false,
        error: '이 앱은 캘린더 수정시각을 보내지 않는 예전 버전입니다. 새 버전으로 새로고침한 뒤 다시 시도해 주세요.'
      };
    }
  }
  writeCalendars_(sh, list);
  return { ok: true, count: Math.min(list.length, 60) };
}

function calMs_(c) {
  if (!c || !c.updatedAt) return 0;
  var t = new Date(c.updatedAt).getTime();
  return isNaN(t) ? 0 : t;
}

/** 셀 값을 ISO 문자열로 돌려줍니다(비어 있으면 ''). */
function toIso_(v) {
  if (v === null || v === undefined || v === '') return '';
  if (Object.prototype.toString.call(v) === '[object Date]') return new Date(v.getTime()).toISOString();
  var s = String(v).trim();
  var t = new Date(s).getTime();
  return isNaN(t) ? '' : new Date(t).toISOString();
}

function writeCalendars_(sh, list) {
  if (sh.getLastRow() > 1) sh.getRange(2, 1, sh.getLastRow() - 1, HEADERS.calendars.length).clearContent();
  var rows = (list || []).slice(0, 60).map(function (c, i) {
    return [
      String(c.id || ('c' + i)),
      String(c.name || '캘린더'),
      String(c.color || '#039be5'),
      c.visible === false ? 'N' : 'Y',
      Number(c.order == null ? i : c.order),
      calMs_(c) ? new Date(calMs_(c)).toISOString() : ''
    ];
  });
  if (rows.length) sh.getRange(2, 1, rows.length, HEADERS.calendars.length).setValues(rows);
}

/**
 * 이 브라우저의 일정·캘린더를 시트에 통째로 올립니다(최초 이전·복구용).
 * 일정이 비어 있는 요청으로 기존 데이터를 지우지 않습니다(clear:true 일 때만 허용).
 */
function saveAll_(req) {
  var events = req.events;
  var calendars = req.calendars;
  if (!Array.isArray(events)) return { ok: false, error: '일정 목록이 없습니다.' };
  if (events.length > MAX_EVENTS) return { ok: false, error: '한 번에 올릴 수 있는 일정은 ' + MAX_EVENTS + '개까지입니다.' };

  var sh = sheet_(TABS.events, HEADERS.events);
  var map = headerMap_(sh, HEADERS.events);
  var width = eventWidth_(map);
  var current = Math.max(0, sh.getLastRow() - 1);
  if (!events.length && current > 0 && req.clear !== true) {
    return { ok: false, error: '올릴 일정이 없어 기존 ' + current + '개를 지우지 않았습니다. 정말 비우려면 clear:true 로 보내세요.' };
  }

  var rows = events.map(function (ev) {
    return eventRow_(ev, map, width, ev.googleId || '');
  }).filter(function (r) { return !!r[0]; });

  if (sh.getLastRow() > 1) sh.getRange(2, 1, sh.getLastRow() - 1, width).clearContent();
  if (rows.length) sh.getRange(2, 1, rows.length, width).setValues(rows);

  if (Array.isArray(calendars) && calendars.length) {
    writeCalendars_(sheet_(TABS.calendars, HEADERS.calendars), calendars);
  }
  return { ok: true, events: rows.length };
}

function findRow_(sh, id) {
  if (!sh || sh.getLastRow() < 2) return 0;
  var ids = sh.getRange(2, 1, sh.getLastRow() - 1, 1).getValues();
  for (var i = 0; i < ids.length; i++) {
    if (String(ids[i][0]) === String(id)) return i + 2;
  }
  return 0;
}

/* ------------------------------------------------------------------ 육아: 읽기/저장 */

function readKidsProfile_(ss) {
  var sh = ss.getSheetByName(TABS.kidsProfile);
  var out = [];
  if (!sh || sh.getLastRow() < 2) return out;
  var rows = sh.getRange(2, 1, sh.getLastRow() - 1, HEADERS.kidsProfile.length).getValues();
  rows.forEach(function (r, i) {
    var id = String(r[0] || '').trim();
    if (!id) return;
    out.push({
      id: id,
      name: String(r[1] || ('아이 ' + (i + 1))),
      color: String(r[2] || '#3B82F6'),
      order: Number(r[3] == null ? i : r[3]),
      updatedAt: toIso_(r[4])
    });
  });
  out.sort(function (a, b) { return a.order - b.order; });
  return out.slice(0, MAX_KIDS);
}

function readKidsEvents_(ss) {
  var sh = ss.getSheetByName(TABS.kidsEvents);
  var out = [];
  if (!sh || sh.getLastRow() < 2) return out;
  var rows = sh.getRange(2, 1, sh.getLastRow() - 1, HEADERS.kidsEvents.length).getValues();
  rows.forEach(function (r) {
    var id = String(r[0] || '').trim();
    if (!id) return;
    out.push({
      id: id,
      title: String(r[1] || ''),
      childIds: String(r[2] || '').split(',').map(function (s) { return s.trim(); }).filter(function (s) { return !!s; }),
      cat: String(r[3] || 'etc'),
      date: toText_(r[4]),
      endDate: toText_(r[5]),
      allDay: String(r[6] || '').toUpperCase() === 'Y',
      start: toTime_(r[7]),
      end: toTime_(r[8]),
      place: String(r[9] || ''),
      memo: String(r[10] || ''),
      supplies: parseSupplies_(r[11]),
      rrule: String(r[12] || ''),
      exdates: String(r[13] || '').split(',').map(function (s) { return s.trim(); }).filter(function (s) { return !!s; }),
      updatedAt: toIso_(r[14])
    });
  });
  return out;
}

/** 시트가 시간을 Date 로 바꾸어 저장했을 때도 'HH:mm' 으로 돌려줍니다. */
function toTime_(v) {
  if (v === null || v === undefined || v === '') return '';
  if (Object.prototype.toString.call(v) === '[object Date]') {
    var tz = Session.getScriptTimeZone() || 'Asia/Seoul';
    return Utilities.formatDate(v, tz, 'HH:mm');
  }
  var s = String(v).trim();
  var m = s.match(/(\d{1,2}):(\d{2})/);
  if (m) return ('0' + m[1]).slice(-2) + ':' + m[2];
  return '';
}

function parseSupplies_(v) {
  if (v === null || v === undefined || v === '') return [];
  var list = null;
  try { list = JSON.parse(String(v)); } catch (e) { list = null; }
  if (!Array.isArray(list)) return [];
  return list.map(function (x) {
    return {
      id: String((x && x.id) || ''),
      text: String((x && x.text) || ''),
      done: (x && x.done && typeof x.done === 'object') ? x.done : {}
    };
  }).filter(function (x) { return !!x.text; });
}

function saveKidsEvent_(req) {
  var ev = req.event || req;
  if (!ev || !ev.id) return { ok: false, error: '육아 일정 ID가 없습니다.' };
  var sh = sheet_(TABS.kidsEvents, HEADERS.kidsEvents);
  var row = findRow_(sh, ev.id);
  var values = [[
    String(ev.id),
    String(ev.title || ''),
    (Array.isArray(ev.childIds) ? ev.childIds : []).map(String).join(','),
    String(ev.cat || 'etc'),
    String(ev.date || ''),
    String(ev.endDate || ''),
    ev.allDay ? 'Y' : 'N',
    String(ev.start || ''),
    String(ev.end || ''),
    String(ev.place || ''),
    String(ev.memo || '').slice(0, 5000),
    JSON.stringify(Array.isArray(ev.supplies) ? ev.supplies : []),
    String(ev.rrule || ''),
    (Array.isArray(ev.exdates) ? ev.exdates : []).map(String).join(','),
    String(ev.updatedAt || new Date().toISOString())
  ]];
  if (row > 0) {
    sh.getRange(row, 1, 1, values[0].length).setValues(values);
  } else {
    if (sh.getLastRow() >= MAX_KIDS_EVENTS + 1) return { ok: false, error: '육아 일정이 너무 많습니다(최대 ' + MAX_KIDS_EVENTS + '개).' };
    sh.getRange(sh.getLastRow() + 1, 1, 1, values[0].length).setValues(values);
  }
  return { ok: true, id: String(ev.id) };
}

function deleteKidsEvent_(req) {
  var id = String(req.id || (req.event && req.event.id) || '');
  if (!id) return { ok: false, error: '삭제할 육아 일정 ID가 없습니다.' };
  var sh = sheet_(TABS.kidsEvents, HEADERS.kidsEvents);
  var row = findRow_(sh, id);
  if (row > 0) sh.deleteRow(row);
  return { ok: true, id: id, deleted: row > 0 };
}

function saveKidsProfile_(req) {
  var list = req.children;
  if (!Array.isArray(list)) return { ok: false, error: '아이 프로필 목록이 없습니다.' };
  if (!list.length) return { ok: false, error: '아이가 하나도 없어 저장하지 않았습니다.' };
  var ss = ss_();
  var sh = sheet_(TABS.kidsProfile, HEADERS.kidsProfile);
  /* 수정시각을 보내지 않는 옛 앱이 시트에 있는 더 최신 이름을 지우지 않도록 막습니다. */
  if (!list.some(function (c) { return calMs_(c) > 0; })) {
    var current = readKidsProfile_(ss);
    if (current.some(function (c) { return calMs_(c) > 0; })) {
      return { ok: false, error: '이 앱은 아이 프로필 수정시각을 보내지 않는 예전 버전입니다. 새 버전으로 새로고침한 뒤 다시 시도해 주세요.' };
    }
  }
  if (sh.getLastRow() > 1) sh.getRange(2, 1, sh.getLastRow() - 1, HEADERS.kidsProfile.length).clearContent();
  var rows = list.slice(0, MAX_KIDS).map(function (c, i) {
    return [
      String(c.id || ('kid' + (i + 1))),
      String(c.name || ('아이 ' + (i + 1))).slice(0, 20),
      String(c.color || '#3B82F6'),
      Number(c.order == null ? i : c.order),
      calMs_(c) ? new Date(calMs_(c)).toISOString() : ''
    ];
  });
  if (rows.length) sh.getRange(2, 1, rows.length, HEADERS.kidsProfile.length).setValues(rows);
  return { ok: true, count: rows.length };
}

/* ------------------------------------------------------------------ 구글 캘린더로 보내기 */

/**
 * 일정 한 건을 내 구글 캘린더의 기본 캘린더에 만듭니다.
 * 이미 보낸 일정(시트에 구글전송ID가 있음)이면 새로 만들지 않고 기존 ID를 돌려줍니다.
 *
 * 반복 일정은 CalendarApp이 반복 규칙을 만들지 못하므로 **첫 회차 하루만** 복사하고,
 * 설명에 반복이라는 사실을 적어 둡니다. (알림이 필요한 회차만 보내는 용도)
 */
function exportToGoogle_(req) {
  var ev = req.event || {};
  var id = String(ev.id || req.id || '');
  if (!id) return { ok: false, error: '보낼 일정 ID가 없습니다.' };

  var sh = sheet_(TABS.events, HEADERS.events);
  var map = headerMap_(sh, HEADERS.events);
  var row = findRow_(sh, id);

  // 시트에 기록된 값이 우선입니다(클라이언트가 오래된 사본을 들고 있어도 중복 전송 방지).
  if (row > 0) {
    var recorded = map['구글전송ID'] >= 0 ? String(sh.getRange(row, map['구글전송ID'] + 1).getValue() || '') : '';
    if (recorded) return { ok: true, already: true, googleId: recorded };
  }
  if (ev.googleId) return { ok: true, already: true, googleId: String(ev.googleId) };

  var title = String(ev.title || '(제목 없음)');
  var repeat = String(ev.repeat || 'none');
  var desc = String(ev.memo || '');
  /* 완료한 일정은 구글 캘린더에서도 흑색(그래파이트)으로 보이게 하고 설명에 표시를 남깁니다. */
  if (ev.done) desc = (desc ? desc + '\n\n' : '') + '✓ 완료한 일정 (이 앱에서 완료로 체크됨)';
  if (repeat !== 'none') {
    desc = (desc ? desc + '\n\n' : '') +
      '🔁 원본은 ' + repeatLabel_(repeat) + ' 반복 일정입니다. 이 복사본은 첫 회차만 들어갔습니다.';
  }
  var options = { description: desc, location: String(ev.location || '') };

  var cal = CalendarApp.getDefaultCalendar();
  if (!cal) return { ok: false, error: '기본 캘린더를 찾지 못했습니다.' };

  var created;
  if (ev.allDay) {
    var s = parseDate_(ev.start);
    var e = parseDate_(ev.end || ev.start);
    if (e.getTime() < s.getTime()) e = s;
    if (s.getTime() === e.getTime()) {
      created = cal.createAllDayEvent(title, s, options);
    } else {
      // CalendarApp 의 endDate 는 배타적입니다 → 하루 더합니다.
      created = cal.createAllDayEvent(title, s, addDays_(e, 1), options);
    }
  } else {
    var st = parseDateTime_(ev.start);
    var en = parseDateTime_(ev.end || ev.start);
    if (en.getTime() <= st.getTime()) en = new Date(st.getTime() + 60 * 60 * 1000);
    created = cal.createEvent(title, st, en, options);
  }

  var gid = created.getId();
  var gcolor = googleColorId_(ev);
  if (gcolor) { try { created.setColor(gcolor); } catch (e) {} }

  // 시트에도 기록해 두 번째 전송을 막습니다.
  if (row > 0) {
    if (map['구글전송ID'] >= 0) sh.getRange(row, map['구글전송ID'] + 1).setValue(gid);
    if (map['수정시각'] >= 0 && !String(ev.updatedAt || '')) sh.getRange(row, map['수정시각'] + 1).setValue(new Date().toISOString());
  }
  return {
    ok: true,
    googleId: gid,
    title: title,
    start: String(ev.start || ''),
    end: String(ev.end || ''),
    allDay: !!ev.allDay,
    colorId: gcolor,
    calendar: (function () { try { return cal.getName(); } catch (e) { return ''; } })(),
    repeated: repeat !== 'none'
  };
}

/** 이 앱의 일정 색 → 구글 캘린더 색 ID. 완료·흑색은 8(그래파이트). */
function googleColorId_(ev) {
  if (ev && ev.done) return '8';
  var c = String((ev && ev.color) || '').toLowerCase();
  var table = {
    '#7986cb': '1', '#33b679': '2', '#8e24aa': '3', '#e67c73': '4', '#f6bf26': '5',
    '#f4511e': '6', '#039be5': '7', '#3f4042': '8', '#616161': '8', '#3f51b5': '9',
    '#0b8043': '10', '#d50000': '11'
  };
  return table[c] || '';
}

function repeatLabel_(r) {
  if (r === 'weekly') return '매주';
  if (r === 'monthly') return '매월';
  if (r === 'yearly') return '매년';
  return r;
}

/* ------------------------------------------------------------------ 날짜 도우미 */

function parseDate_(s) {
  var m = String(s || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) throw new Error('날짜 형식이 올바르지 않습니다: ' + s);
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 0, 0, 0, 0);
}

function parseDateTime_(s) {
  var m = String(s || '').match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/);
  if (!m) throw new Error('날짜·시간 형식이 올바르지 않습니다: ' + s);
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4] || 0), Number(m[5] || 0), 0, 0);
}

function addDays_(d, n) {
  var x = new Date(d.getTime());
  x.setDate(x.getDate() + n);
  return x;
}

/** 셀 값을 'YYYY-MM-DD' 또는 'YYYY-MM-DDTHH:mm' 문자열로 돌려줍니다. */
function toText_(v) {
  if (v === null || v === undefined || v === '') return '';
  if (Object.prototype.toString.call(v) === '[object Date]') {
    var tz = Session.getScriptTimeZone() || 'Asia/Seoul';
    var d = Utilities.formatDate(v, tz, 'yyyy-MM-dd');
    var hm = Utilities.formatDate(v, tz, 'HH:mm');
    if (hm === '00:00') return d;
    return d + 'T' + hm;
  }
  return String(v).trim();
}
