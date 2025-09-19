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

  // Persistent toast: click message = navigate; × = ack-only
  function showToast(msg, level = "info", id = null) {
    const { toast } = ensureContainers();
    const icons = { info: "ℹ️", warning: "⚠️", critical: "⛔" };
    const icon = icons[level] || icons.info;

    // Build HTML
    toast.innerHTML =
      `<span class="icon">${icon}</span>` +
      `<span class="toast__msg">${escapeHtml(msg)}</span>` +
      `<button type="button" class="toast__close" aria-label="Dismiss notification" title="Dismiss">×</button>`;

    // Base classes
    toast.className = "toast";
    toast.classList.add(level, "show");

    // Keep id on element
    if (id != null) toast.dataset.id = String(id);

    // Remove old handlers by cloning (clean slate)
    const newToast = toast.cloneNode(true);
    toast.parentNode.replaceChild(newToast, toast);

    // Elements on the NEW node
    const close = newToast.querySelector(".toast__close");
    const msgEl = newToast.querySelector(".toast__msg");

    // × click = ack-only (no navigation)
    const onClose = async (ev) => {
      ev.stopPropagation();
      newToast.classList.remove("show");
      if (id) await ack(id);
    };
    if (close) close.addEventListener("click", onClose);

    // Message click = navigate to unread (no ack)
    const onMsgClick = (ev) => {
      ev.stopPropagation();
      ev.preventDefault();
      window.location.assign("/notifications/?show=unread");
    };
    if (msgEl) msgEl.addEventListener("click", onMsgClick);
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
