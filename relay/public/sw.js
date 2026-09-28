// Shows web pushes sent by the PC ({title, body, url, tag}) and opens the
// matching chat when the notification is tapped. No caching: the page is
// useless offline anyway, and a stale cached page would hide relay fixes.

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

// Only this page may be opened from a notification, whatever the payload says.
function safeUrl(raw) {
  try {
    const url = new URL(raw, self.location.origin);
    if (url.origin === self.location.origin && url.pathname === '/p') return '/p' + url.hash;
  } catch {}
  return '/p';
}

self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data ? event.data.text() : '' };
  }
  const options = {
    body: typeof data.body === 'string' ? data.body : '',
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    data: { url: safeUrl(data.url || '/p') },
  };
  if (typeof data.tag === 'string') options.tag = data.tag;
  const title = typeof data.title === 'string' && data.title ? data.title : 'Gamachine';
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = safeUrl(event.notification.data && event.notification.data.url);
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if (new URL(client.url).origin === self.location.origin && 'focus' in client) {
          client.postMessage({ type: 'navigate', url });
          return client.focus();
        }
      }
      return self.clients.openWindow ? self.clients.openWindow(url) : undefined;
    }),
  );
});
