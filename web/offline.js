/* Offline support for the dashboard and extension view: installs the service worker
   (which precaches the whole site) and shows a badge when the network is gone. */
(function () {
  "use strict";
  if ("serviceWorker" in navigator && location.protocol.startsWith("http")) {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
  function badge() {
    let b = document.getElementById("offline-badge");
    if (navigator.onLine) { if (b) b.remove(); return; }
    if (!b) {
      b = document.createElement("div");
      b.id = "offline-badge";
      b.textContent = "📴 Offline: running from this device's cache";
      b.style.cssText = "position:fixed;bottom:14px;right:14px;z-index:50;background:#232321;color:#fff;"
        + "border:1px solid #fab219;border-radius:10px;padding:6px 12px;font:12px system-ui";
      document.body.append(b);
    }
  }
  window.addEventListener("online", badge);
  window.addEventListener("offline", badge);
  window.addEventListener("load", badge);
})();
