const CACHE_NAME = 'sang-v4';
const ASSETS = [
  './',
  './index.html',
  './universe.json',
  './manifest.json',
  'https://cdn.tailwindcss.com',
  './icon-192.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS).catch(() => {});
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  const requestUrl = new URL(event.request.url);
  const sameOrigin = requestUrl.origin === self.location.origin;

  // Số liệu CafeF (gọi thẳng hoặc qua Worker): luôn lấy mới từ mạng, không cache.
  if (requestUrl.hostname === 'apiweb.cafef.vn' || requestUrl.hostname.endsWith('.workers.dev')) return;

  const isAppShell = sameOrigin &&
    (requestUrl.pathname.endsWith('/') || requestUrl.pathname.endsWith('/index.html'));
  const isData = sameOrigin && requestUrl.pathname.includes('/data/');
  const isUniverse = sameOrigin && requestUrl.pathname.endsWith('/universe.json');

  // HTML và data/*.json: ưu tiên mạng để cập nhật mới không bị cache cũ che; cache chỉ để dùng offline.
  if (isAppShell || isData || isUniverse) {
    event.respondWith(
      fetch(event.request, { cache: 'no-cache' }).then((response) => {
        if (response && response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return response;
      }).catch(() => caches.match(event.request))
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then((cached) => {
      return cached || fetch(event.request).then((response) => {
        if (response && response.status === 200) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return response;
      }).catch(() => cached);
    })
  );
});
