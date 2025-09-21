// patients/static/patients/notifications.js
(function () {
  const FEED_URL = "/api/notifications/";
  const STATUS_URL = "/api/notifications/status/";
  const POLL_MS = 30000;

  // Build ack URL
  const ackUrl = (id) => `/notifications/${id}/ack/`;

  // CSRF helper for Django POSTs
  function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(";").shift();
  }

  // Ensure DOM containers exist (creates them if missing)
  function ensureContainers() {
    // Toast
    let toast = document.getElementById("toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "toast";
      toast.className = "toast";
      toast.setAttribute("role", "status");
      toast.setAttribute("aria-live", "polite");
      toast.style.position = "fixed";
      toast.style.right = "16px";
      toast.style.top = "16px";
      toast.style.zIndex = "10002";
      document.body.appendChild(toast);
    }

    // Backdrop
    let backdrop = document.getElementById("modal-backdrop");
    if (!backdrop) {
      backdrop = document.createElement("div");
      backdrop.id = "modal-backdrop";
      Object.assign(backdrop.style, {
        display: "none",
        position: "fixed",
        inset: "0",
        background: "rgba(0,0,0,0.45)",
        zIndex: "10000",
      });
      document.body.appendChild(backdrop);
    }

    // Modal
    let modal = document.getElementById("critical-modal");
    let modalBody = document.getElementById("critical-modal-body");
    let ackBtn = document.getElementById("critical-ack-btn");
    if (!modal) {
      modal = document.createElement("div");
      modal.id = "critical-modal";
      modal.setAttribute("aria-hidden", "true");
      modal.setAttribute("role", "dialog");
      modal.setAttribute("aria-modal", "true");
      Object.assign(modal.style, {
        display: "none",
        position: "fixed",
        zIndex: "10001",
        top: "50%",
        left: "50%",
        transform: "translate(-50%,-50%)",
        background: "#fff",
        padding: "16px 20px",
        maxWidth: "520px",
        width: "92%",
        borderRadius: "6px",
        boxShadow: "0 10px 30px rgba(0,0,0,0.25)",
      });

      const h3 = document.createElement("h3");
      h3.textContent = "Critical Notification";
      modalBody = document.createElement("div");
      modalBody.id = "critical-modal-body";
      modalBody.style.marginBottom = "14px";
      const footer = document.createElement("div");
      footer.style.textAlign = "right";
      ackBtn = document.createElement("button");
      ackBtn.type = "button";
      ackBtn.id = "critical-ack-btn";
      ackBtn.className = "button";
      ackBtn.textContent = "Acknowledge";
      footer.appendChild(ackBtn);
      modal.appendChild(h3);
      modal.appendChild(modalBody);
      modal.appendChild(footer);
      document.body.appendChild(modal);
    }
    if (!modalBody) modalBody = document.getElementById("critical-modal-body");
    if (!ackBtn) ackBtn = document.getElementById("critical-ack-btn");

    return { toast, backdrop, modal, modalBody, ackBtn };
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  // Badge updater (supports multiple placements)
  function updateBadge(count) {
    const els = [];
    // Header badge in site base
    const header = document.getElementById("notifications-badge");
    if (header) els.push(header);

    // Any admin/header pills using this class
    document.querySelectorAll(".notif-badge").forEach((e) => els.push(e));

    els.forEach((el) => {
        el.textContent = count;
        if (el.dataset) el.dataset.count = count;
        if (el.classList) el.classList.toggle("hidden", Number(count) === 0);
    });
    }

  async function ack(id) {
    try {
      await fetch(ackUrl(id), {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRFToken": getCookie("csrftoken") },
      });
    } catch (_e) {
      // non-blocking
    }
  }

  // Persistent toast with visible × (click toast or × = dismiss + ack)
  function showToast(msg, level = "info", id = null) {
    const { toast } = ensureContainers();
    const icons = { info: "ℹ️", warning: "⚠️", critical: "⛔" };
    const icon = icons[level] || icons.info;

    toast.innerHTML =
      `<span class="icon">${icon}</span>` +
      `<span class="toast__msg">${escapeHtml(msg)}</span>` +
      `<button type="button" class="toast__close" aria-label="Dismiss notification" title="Dismiss">×</button>`;
      
    toast.className = "toast";
    toast.classList.add(level, "show");

    const closeBtn = toast.querySelector(".toast__close");

    const onDismiss = async () => {
      toast.classList.remove("show");
      if (id) await ack(id);
      toast.onclick = null;
      if (closeBtn) closeBtn.removeEventListener("click", onDismiss);
    };

    // Both the × and clicking the toast dismiss
    if (closeBtn) closeBtn.addEventListener("click", onDismiss);
    toast.onclick = onDismiss;
  }

  // Blocking critical modal (ESC or button = ack)
  function showCriticalModal(msg, id = null) {
    const { backdrop, modal, modalBody, ackBtn } = ensureContainers();
    modalBody.textContent = msg;
    backdrop.style.display = "block";
    modal.style.display = "block";
    modal.setAttribute("aria-hidden", "false");

    const onAck = async () => {
      modal.setAttribute("aria-hidden", "true");
      modal.style.display = "none";
      backdrop.style.display = "none";
      if (id) await ack(id);
      ackBtn.removeEventListener("click", onAck);
      document.removeEventListener("keydown", onKey);
    };

    const onKey = (ev) => {
      if (ev.key === "Escape") onAck();
    };

    ackBtn.addEventListener("click", onAck);
    document.addEventListener("keydown", onKey);
  }

  // Polling with cursor + dedupe
  const seenIds = new Set();
  let cursor = parseInt(localStorage.getItem("notif:lastSeenId") || "0", 10) || 0;

  async function poll() {
    try {
        const url = new URL(FEED_URL, window.location.origin);
        if (cursor) url.searchParams.set("since_id", String(cursor));
        const res = await fetch(url.toString(), { credentials: "same-origin" });
        if (!res.ok) return;

        const data = await res.json();

        // Handle items from the feed (toasts/modals)
        const items = data.items || [];
        for (const it of items) {
        if (!it || typeof it.id === "undefined") continue;
        if (seenIds.has(it.id)) continue;
        seenIds.add(it.id);

        const level = String(it.level || "info").toLowerCase();
        const msg = it.message || "New notification";

        if (level === "critical") {
            showCriticalModal(msg, it.id);
        } else {
            showToast(msg, level, it.id);
        }
        }

        // Advance cursor using latest_id from the feed
        if (typeof data.latest_id === "number" && data.latest_id > (cursor || 0)) {
        cursor = data.latest_id;
        localStorage.setItem("notif:lastSeenId", String(cursor));
        }

        // Refresh the HEADER BADGE from the status endpoint (UNREAD semantics)
        try {
        const sr = await fetch(STATUS_URL, { credentials: "same-origin" });
        if (sr.ok) {
            const sj = await sr.json();
            updateBadge(sj.unread_count || 0);
        }
        } catch (_e) {
        // ignore transient errors
        }
    } catch (_e) {
        // ignore transient errors
    }
  }

  function start() {
    // quick first sync, then periodic, and on tab focus
    setTimeout(poll, 1500);
    setInterval(poll, POLL_MS);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) poll();
    });
  }

  document.addEventListener("DOMContentLoaded", start);

  // Expose for manual testing
  window.showToast = showToast;
  window.showCriticalModal = showCriticalModal;
})();
