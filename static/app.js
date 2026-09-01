// Close dialogs when clicking the backdrop
document.querySelectorAll("dialog.dialog").forEach((dialog) => {
  dialog.addEventListener("click", (e) => {
    const rect = dialog.getBoundingClientRect();
    const inside =
      rect.top <= e.clientY &&
      e.clientY <= rect.top + rect.height &&
      rect.left <= e.clientX &&
      e.clientX <= rect.left + rect.width;
    if (!inside) dialog.close();
  });
});

// ---------- Custom dropdown listbox (replaces native <select> for full styling control) ----------

function closeAllCselects(except) {
  document.querySelectorAll(".cselect").forEach((root) => {
    if (root !== except) {
      root.querySelector(".cselect__list").hidden = true;
      root.querySelector(".cselect__trigger").setAttribute("aria-expanded", "false");
      root.removeAttribute("data-open");
    }
  });
}

function setCselectValue(root, value) {
  if (!root) return;
  const hidden = root.querySelector('input[type="hidden"]');
  const valueEl = root.querySelector(".cselect__value");
  const options = Array.from(root.querySelectorAll(".cselect__option"));
  const match = options.find((o) => o.dataset.value === String(value));
  if (!match) return;

  hidden.value = match.dataset.value;
  valueEl.textContent = match.textContent;
  options.forEach((o) => o.classList.remove("is-selected"));
  match.classList.add("is-selected");
}

function initCselect(root) {
  const trigger = root.querySelector(".cselect__trigger");
  const valueEl = root.querySelector(".cselect__value");
  const hidden = root.querySelector('input[type="hidden"]');
  const list = root.querySelector(".cselect__list");
  const options = Array.from(root.querySelectorAll(".cselect__option"));

  function open() {
    closeAllCselects(root);
    list.hidden = false;
    root.setAttribute("data-open", "true");
    trigger.setAttribute("aria-expanded", "true");
    const current = options.find((o) => o.classList.contains("is-selected")) || options[0];
    if (current) current.focus();
  }

  function close(focusTrigger) {
    list.hidden = true;
    root.removeAttribute("data-open");
    trigger.setAttribute("aria-expanded", "false");
    if (focusTrigger) trigger.focus();
  }

  function choose(opt) {
    if (opt.classList.contains("is-disabled")) return;
    hidden.value = opt.dataset.value;
    valueEl.textContent = opt.textContent;
    options.forEach((o) => o.classList.remove("is-selected"));
    opt.classList.add("is-selected");
    close(true);
  }

  trigger.addEventListener("click", (e) => {
    e.stopPropagation();
    list.hidden ? open() : close(false);
  });

  trigger.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      open();
    }
  });

  options.forEach((opt, idx) => {
    opt.addEventListener("click", (e) => {
      e.stopPropagation();
      choose(opt);
    });

    opt.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        (options[idx + 1] || options[0]).focus();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        (options[idx - 1] || options[options.length - 1]).focus();
      } else if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        choose(opt);
      } else if (e.key === "Escape") {
        e.preventDefault();
        close(true);
      } else if (e.key === "Tab") {
        close(false);
      }
    });
  });
}

document.querySelectorAll(".cselect").forEach(initCselect);
document.addEventListener("click", () => closeAllCselects(null));

// ---------- Past-time restriction ----------
// Crosses out start-time options whose start time has already passed when the
// chosen date is today. Runs on date change AND on a live 30-second tick so
// the list stays accurate while the dialog is open.
// Server-side re-validates as the real guard — this is purely UX.

const pastTimeRefreshers = {};

function todayISO() {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function currentHHMM() {
  const now = new Date();
  return String(now.getHours()).padStart(2, "0") + ":" + String(now.getMinutes()).padStart(2, "0");
}

function setupPastTimeRestriction(dateInputId, cselectId) {
  const dateInput = document.getElementById(dateInputId);
  const root = document.getElementById(cselectId);
  if (!dateInput || !root) return;

  const durationSelectId = cselectId.replace("start", "duration");
  const durationRoot = document.getElementById(durationSelectId);

  function refresh() {
    const isToday = dateInput.value === todayISO();
    const options = Array.from(root.querySelectorAll(".cselect__option"));

    if (!isToday) {
      // Future date — all slots are valid
      options.forEach((opt) => {
        opt.classList.remove("is-disabled");
        opt.setAttribute("aria-disabled", "false");
      });
      return;
    }

    const nowHHMM = currentHHMM();

    options.forEach((opt) => {
      // A slot is past if its START time has already passed
      const isPast = opt.dataset.value <= nowHHMM;
      opt.classList.toggle("is-disabled", isPast);
      opt.setAttribute("aria-disabled", isPast ? "true" : "false");
    });

    // If the currently selected option just became disabled, jump to the
    // first available slot (or leave the trigger showing "—" if all gone)
    const selected = options.find((o) => o.classList.contains("is-selected"));
    if (selected && selected.classList.contains("is-disabled")) {
      const nextValid = options.find((o) => !o.classList.contains("is-disabled"));
      if (nextValid) {
        setCselectValue(root, nextValid.dataset.value);
      } else {
        // All slots passed — clear the trigger text to signal nothing is available
        const valueEl = root.querySelector(".cselect__value");
        const hidden  = root.querySelector('input[type="hidden"]');
        if (valueEl) valueEl.textContent = "No slots available";
        if (hidden)  hidden.value = "";
        options.forEach((o) => o.classList.remove("is-selected"));
      }
    }
  }

  dateInput.addEventListener("change", refresh);

  // Refresh when duration changes (duration select is irrelevant now, but
  // keep the hook so cost preview and other listeners stay consistent)
  if (durationRoot) {
    durationRoot.querySelectorAll(".cselect__option").forEach((opt) => {
      opt.addEventListener("click", () => setTimeout(refresh, 50));
    });
  }

  pastTimeRefreshers[cselectId] = refresh;
  refresh();
}

setupPastTimeRestriction("add-date", "add-start-select");
setupPastTimeRestriction("edit-date", "edit-start-select");

// Live clock: re-check every 30 seconds so slots cross out in real time
// while the booking dialog is open
setInterval(() => {
  Object.values(pastTimeRefreshers).forEach((fn) => fn());
}, 30_000);