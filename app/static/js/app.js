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

  // Drag-and-drop sibling reordering on task trees.
  const dragRows = document.querySelectorAll(".js-drag-row");
  if (dragRows.length) {
    let dragged = null;

    const clearDrop = () => {
      document.querySelectorAll(".drop-target").forEach((row) =>
        row.classList.remove("drop-target")
      );
    };

    dragRows.forEach((row) => {
      row.addEventListener("dragstart", (e) => {
        dragged = row;
        row.classList.add("dragging");
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", row.dataset.taskId);
      });
      row.addEventListener("dragend", () => {
        row.classList.remove("dragging");
        clearDrop();
        dragged = null;
      });
      row.addEventListener("dragover", (e) => {
        if (!dragged || dragged === row) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        clearDrop();
        row.classList.add("drop-target");
      });
      row.addEventListener("dragleave", () => row.classList.remove("drop-target"));
      row.addEventListener("drop", (e) => {
        e.preventDefault();
        if (!dragged || dragged === row) return;
        const rect = row.getBoundingClientRect();
        const position = e.clientY > rect.top + rect.height / 2 ? "after" : "before";
        const url = "/tasks/" + encodeURIComponent(dragged.dataset.taskId) + "/reorder";
        window
          .postJSON(url, { target_id: Number(row.dataset.taskId), position: position })
          .then((data) => {
            if (data && data.ok) window.location.reload();
          })
          .catch(() => {
            clearDrop();
            window.alert("That move could not be completed.");
          });
      });
    });
  }

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
