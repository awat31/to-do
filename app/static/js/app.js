/* Shared front-end behaviour: CSRF fetch helper, autosave, confirm dialogs. */
(function () {
  "use strict";

  const csrfToken = () => {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : "";
  };

  window.postJSON = function (url, payload, options = {}) {
    return fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
      },
      credentials: "same-origin",
      keepalive: Boolean(options.keepalive),
      body: JSON.stringify(payload),
    }).then((resp) => {
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      return resp.json();
    });
  };

  // Forms that submit themselves when a control changes (the old inline
  // onchange handlers were removed so a strict script-src CSP can apply).
  document.querySelectorAll(".js-autosubmit").forEach((control) => {
    control.addEventListener("change", () => {
      if (control.form) control.form.submit();
    });
  });

  document.querySelectorAll(".js-task-filters").forEach((form) => {
    const search = form.querySelector('input[name="q"]');
    let timer = null;
    if (search) {
      search.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(() => form.requestSubmit(), 300);
      });
    }
    form.querySelectorAll("select").forEach((select) => {
      select.addEventListener("change", () => form.requestSubmit());
    });
  });

  const subtaskInput = document.getElementById("add-subtask");
  if (subtaskInput && window.location.hash === "#add-subtask") {
    subtaskInput.focus();
  }

  const projectGrid = document.querySelector(".project-grid[data-reorder-url]");
  if (projectGrid) {
    const status = document.querySelector(".project-reorder-status");
    let dragged = null;
    let previousOrder = [];
    let savingOrder = false;
    let saveAgain = false;

    const cards = () => Array.from(projectGrid.querySelectorAll(".project-card"));
    const saveOrder = () => {
      if (savingOrder) {
        saveAgain = true;
        return;
      }

      savingOrder = true;
      saveAgain = false;
      const projectIds = cards().map((card) => Number(card.dataset.projectId));
      fetch(projectGrid.dataset.reorderUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken(),
        },
        credentials: "same-origin",
        body: JSON.stringify({ project_ids: projectIds }),
      })
        .then((response) => {
          if (!response.ok) throw new Error("HTTP " + response.status);
          return response.json();
        })
        .then((data) => {
          if (!data.ok) throw new Error("Project order was not saved.");
          if (!saveAgain && status) status.textContent = "Project order saved.";
        })
        .catch((error) => {
          if (!saveAgain) {
            previousOrder.forEach((card) => projectGrid.appendChild(card));
            if (status) status.textContent = "Could not save project order. Please try again.";
          }
          console.error(error);
        })
        .finally(() => {
          savingOrder = false;
          if (saveAgain) saveOrder();
        });
    };

    projectGrid.addEventListener("dragstart", (event) => {
      const card = event.target.closest(".project-card");
      if (!card) return;
      dragged = card;
      previousOrder = cards();
      card.classList.add("is-dragging");
      event.dataTransfer.effectAllowed = "move";
    });
    projectGrid.addEventListener("dragover", (event) => {
      if (!dragged) return;
      const target = event.target.closest(".project-card");
      if (!target || target === dragged) return;
      event.preventDefault();
      const bounds = target.getBoundingClientRect();
      const draggedBounds = dragged.getBoundingClientRect();
      const sameRow = Math.abs(draggedBounds.top - bounds.top) < bounds.height / 2;
      const after = sameRow
        ? event.clientX > bounds.left + bounds.width / 2
        : event.clientY > bounds.top + bounds.height / 2;
      projectGrid.insertBefore(dragged, after ? target.nextSibling : target);
      cards().forEach((card) => card.classList.toggle("is-drop-target", card === target));
    });
    projectGrid.addEventListener("drop", (event) => {
      if (!dragged) return;
      event.preventDefault();
      saveOrder();
    });
    projectGrid.addEventListener("dragend", () => {
      if (dragged) dragged.classList.remove("is-dragging");
      cards().forEach((card) => card.classList.remove("is-drop-target"));
      dragged = null;
    });
  }

  // Debounced, serialised autosave for note editors. The final edit is sent
  // with sendBeacon/keepalive when the page is hidden, and transient failures
  // are retried with exponential backoff.
  function autosave(textareaId, statusId, urlFactory) {
    const ta = document.getElementById(textareaId);
    const status = document.getElementById(statusId);
    if (!ta || !status) return;

    let timer = null;
    let retryTimer = null;
    let retryDelay = 2000;
    let lastSaved = ta.value;
    let inflight = null;
    let statusTimer = null;

    const setStatus = (text, clearAfter = 0) => {
      status.textContent = text;
      if (statusTimer) clearTimeout(statusTimer);
      if (clearAfter) {
        statusTimer = setTimeout(() => (status.textContent = ""), clearAfter);
      }
    };

    const payloadFor = (value) => ({ notes: value, csrf_token: csrfToken() });

    const send = (value, { unload = false } = {}) => {
      // sendBeacon cannot set the X-CSRFToken header, so the beacon uses a
      // FormData body with the token as a field (the notes endpoints accept
      // both JSON and form bodies). Larger saves fall back to keepalive fetch.
      if (unload && navigator.sendBeacon && value.length < 60000) {
        const form = new FormData();
        form.append("notes", value);
        form.append("csrf_token", csrfToken());
        if (navigator.sendBeacon(urlFactory(), form)) return Promise.resolve();
      }
      return window.postJSON(urlFactory(), payloadFor(value), { keepalive: unload });
    };

    const save = () => {
      const value = ta.value;
      if (value === lastSaved) return;
      if (inflight) return; // the completion handler will pick up newer text

      setStatus("Saving…");
      inflight = send(value);
      inflight
        .then(() => {
          lastSaved = value;
          retryDelay = 2000;
          if (ta.value === value) {
            setStatus("Saved ✓", 1800);
          } else {
            setStatus("Saving…");
            schedule(0);
          }
        })
        .catch(() => {
          setStatus("Save failed — retrying…");
          clearTimeout(retryTimer);
          retryTimer = setTimeout(save, retryDelay);
          retryDelay = Math.min(retryDelay * 2, 30000);
        })
        .finally(() => {
          inflight = null;
          if (ta.value !== lastSaved) schedule(0);
        });
    };

    const schedule = (delay = 800) => {
      clearTimeout(timer);
      timer = setTimeout(save, delay);
    };

    ta.addEventListener("input", () => {
      setStatus("Unsaved…");
      clearTimeout(retryTimer);
      schedule();
    });

    const flush = () => {
      if (ta.value !== lastSaved) {
        // fire-and-forget; browsers may abort a normal fetch on unload
        send(ta.value, { unload: true }).catch(() => {});
      }
    };
    window.addEventListener("pagehide", flush);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") flush();
    });
    window.addEventListener("beforeunload", flush);
  }

  autosave("project-notes", "project-notes-status", () =>
    window.location.pathname + "/notes"
  );
  autosave(
    "task-notes",
    "task-notes-status",
    () => window.location.pathname + "/notes"
  );

  // Task-name typeahead for the project "add task" box: filters as you type,
  // shows all tasks when empty; picking an entry assigns that existing task
  // to the project instead of creating a duplicate.
  function escapeHtml(text) {
    const d = document.createElement("div");
    d.textContent = text;
    return d.innerHTML;
  }

  document.querySelectorAll("form.task-search").forEach((form) => {
    const input = form.querySelector('input[type="text"], input:not([type="hidden"])');
    const listEl = form.querySelector(".typeahead");
    const hidden = form.querySelector('input[name="existing_task_id"]');
    if (!input || !listEl || !hidden) return;

    let items = [];
    let highlighted = -1;
    let timer = null;
    let controller = null;

    const close = () => {
      listEl.hidden = true;
      highlighted = -1;
      input.setAttribute("aria-expanded", "false");
    };

    const render = () => {
      listEl.innerHTML = "";
      if (!items.length) {
        listEl.innerHTML = '<div class="typeahead-empty">No matching tasks</div>';
      } else {
        items.forEach((item, i) => {
          const b = document.createElement("button");
          b.type = "button";
          b.setAttribute("role", "option");
          b.className = "typeahead-item" + (i === highlighted ? " is-active" : "")
            + (item.done ? " is-done" : "");
          b.innerHTML =
            "<span>" + escapeHtml(item.title) + "</span>" +
            (item.project ? "<em>" + escapeHtml(item.project) + "</em>" : "");
          b.addEventListener("mousedown", (e) => {
            e.preventDefault(); // beat the input's blur
            pick(item);
          });
          listEl.appendChild(b);
        });
      }
      listEl.hidden = false;
      input.setAttribute("aria-expanded", "true");
    };

    const pick = (item) => {
      hidden.value = item.id;
      input.value = item.title;
      close();
      form.submit();
    };

    const load = () => {
      if (controller) controller.abort();
      controller = new AbortController();
      fetch("/tasks/search?q=" + encodeURIComponent(input.value.trim()), {
        credentials: "same-origin",
        signal: controller.signal,
      })
        .then((r) => (r.ok ? r.json() : Promise.reject()))
        .then((data) => {
          items = data.results || [];
          highlighted = -1;
          render();
        })
        .catch((err) => {
          if (err && err.name !== "AbortError") close();
        });
    };

    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    listEl.setAttribute("role", "listbox");

    input.addEventListener("input", () => {
      hidden.value = "";
      clearTimeout(timer);
      timer = setTimeout(load, 150);
    });
    input.addEventListener("focus", load);
    input.addEventListener("keydown", (e) => {
      if (listEl.hidden) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        highlighted = Math.min(highlighted + 1, items.length - 1);
        render();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        highlighted = Math.max(highlighted - 1, 0);
        render();
      } else if (e.key === "Escape") {
        close();
      } else if (e.key === "Enter" && highlighted >= 0) {
        e.preventDefault();
        pick(items[highlighted]);
      }
    });
    input.addEventListener("blur", () => setTimeout(close, 200));
  });

  // Confirmation prompts for destructive buttons.
  document.addEventListener(
    "submit",
    (e) => {
      const btn = e.submitter || document.activeElement;
      const msg = btn && btn.getAttribute && btn.getAttribute("data-confirm");
      if (msg && !window.confirm(msg)) e.preventDefault();
    },
    true
  );

  // "Jump to…" popup on the calendar toolbar.
  const jumpBtn = document.getElementById("jump-toggle");
  const jumpPop = document.getElementById("jump-pop");
  if (jumpBtn && jumpPop) {
    const setOpen = (open) => {
      jumpPop.hidden = !open;
      jumpBtn.setAttribute("aria-expanded", String(open));
      if (open) {
        const dateInput = jumpPop.querySelector('input[type="date"]');
        if (dateInput && dateInput.showPicker) {
          try {
            dateInput.showPicker();
          } catch (err) {
            dateInput.focus();
          }
        } else if (dateInput) {
          dateInput.focus();
        }
      }
    };
    jumpBtn.addEventListener("click", () => setOpen(jumpPop.hidden));
    document.addEventListener("click", (e) => {
      if (
        !jumpPop.hidden &&
        !jumpPop.contains(e.target) &&
        !jumpBtn.contains(e.target)
      ) {
        setOpen(false);
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") setOpen(false);
    });
  }

  // Installable PWA: register the service worker only where the browser
  // supports it (it also requires HTTPS or localhost).
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js").catch(() => {
        /* offline support is a progressive enhancement */
      });
    });
  }
})();
