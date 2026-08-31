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
// Greys out start-time options that have already passed, when the chosen date is today.
// This is a UX nicety only — the server re-validates and is the real guard against
// booking a slot that has already passed.

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

  // Get the duration select element - we need it to calculate end time
  const durationSelectId = cselectId.replace('start', 'duration');
  const durationRoot = document.getElementById(durationSelectId);

  function refresh() {
    const isToday = dateInput.value === todayISO();
    if (!isToday) {
      // If not today, enable all time slots
      const options = Array.from(root.querySelectorAll(".cselect__option"));
      options.forEach((opt) => {
        opt.classList.remove("is-disabled");
        opt.setAttribute("aria-disabled", "false");
      });
      return;
    }

    const nowHHMM = currentHHMM();
    const options = Array.from(root.querySelectorAll(".cselect__option"));
    
    // Get the current duration in minutes (default to 60 if not found)
    let durationMinutes = 60;
    if (durationRoot) {
      const selectedDuration = durationRoot.querySelector(".cselect__option.is-selected");
      if (selectedDuration) {
        durationMinutes = parseInt(selectedDuration.dataset.value) || 60;
      }
    }

    options.forEach((opt) => {
      const startHHMM = opt.dataset.value;
      // Calculate end time by adding duration
      const [startH, startM] = startHHMM.split(':').map(Number);
      const startMinutes = startH * 60 + startM;
      const endMinutes = startMinutes + durationMinutes;
      const endH = Math.floor(endMinutes / 60);
      const endM = endMinutes % 60;
      const endHHMM = String(endH).padStart(2, '0') + ':' + String(endM).padStart(2, '0');
      
      // Disable if the END time has passed
      const isPast = endHHMM < nowHHMM;
      opt.classList.toggle("is-disabled", isPast);
      opt.setAttribute("aria-disabled", isPast ? "true" : "false");
    });

    const selected = options.find((o) => o.classList.contains("is-selected"));
    if (selected && selected.classList.contains("is-disabled")) {
      const nextValid = options.find((o) => !o.classList.contains("is-disabled"));
      if (nextValid) setCselectValue(root, nextValid.dataset.value);
    }
  }

  dateInput.addEventListener("change", refresh);
  // Also refresh when duration changes
  if (durationRoot) {
    const durationOptions = durationRoot.querySelectorAll(".cselect__option");
    durationOptions.forEach(opt => {
      opt.addEventListener("click", () => setTimeout(refresh, 100));
    });
  }
  pastTimeRefreshers[cselectId] = refresh;
  refresh();
}

setupPastTimeRestriction("add-date", "add-start-select");
setupPastTimeRestriction("edit-date", "edit-start-select");