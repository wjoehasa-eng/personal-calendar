const CACHE_NAME = 'personal-calendar-v29';
const ASSETS = ['./', './index.html', './manifest.webmanifest', './icon-192.png', './icon-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE_NAME).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

/* 앱 파일은 항상 서버에 최신 여부를 먼저 확인합니다(no-cache → 바뀐 게 없으면 304, 데이터 거의 없음).
   그래서 PC에서 고쳐 올린 새 버전이 휴대폰에도 바로 적용되고, 오프라인일 때만 캐시를 씁니다.
   시트(Apps Script)·Firebase(Firestore·로그인) 요청은 캐시하지 않고 그대로 통과시킵니다. */
self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  if (req.url.includes('script.google.com') || req.url.includes('googleusercontent.com')) return;
  /* Firebase SDK(gstatic)·로그인·Firestore 통신은 서비스 워커가 손대지 않습니다. */
  if (/gstatic\.com|firebaseio\.com|googleapis\.com|firebaseapp\.com|accounts\.google\.com/.test(req.url)) return;
  e.respondWith((async () => {
    try {
      const res = await fetch(req, { cache: 'no-cache' });
      if (res && res.status === 200 && res.type === 'basic') {
        const copy = res.clone();
        caches.open(CACHE_NAME).then((c) => c.put(req, copy));
      }
      return res;
    } catch (err) {
      const hit = await caches.match(req);
      if (hit) return hit;
      throw err;
    }
  })());
});
