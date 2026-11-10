/* ==========================================================================
   Service Worker - فقط برای اینکه کروم اجازه‌ی نصب بدهد.
   هیچ caching اضافه‌ای انجام نمی‌دهد؛ همه‌ی درخواست‌ها از شبکه می‌آیند.
   ========================================================================== */
self.addEventListener('install', function (event) {
  // بلافاصله نسخه‌ی جدید را فعال کن
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  // کنترل همه‌ی تب‌های باز را در دست بگیر
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', function (event) {
  // pass-through: هر درخواست را همان‌طور که هست به شبکه بفرست
  event.respondWith(fetch(event.request));
});