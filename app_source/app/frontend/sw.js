// Minimal service worker for PWA installability and background execution
const CACHE_NAME = 'zhitou-v1.6.0';

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  // Let network requests pass through directly
  event.respondWith(fetch(event.request).catch(() => {
    // offline fallback if needed
  }));
});
