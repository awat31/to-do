/* Shared front-end behaviour: CSRF fetch helper, autosave, confirm dialogs. */
(function () {
  "use strict";

  const csrfToken = () =>
    document.querySelector('meta[name="csrf-token"]').content;

  window.postJSON = function (url, payload) {
    return fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
      },
      body: JSON.stringify(payload),
    }).then((resp) => {
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      return resp.json();
    });
  };

  // Debounced autosave for note editors.
  function autosave(textareaId, statusId, urlFactory) {
    const ta = document.getElementById(textareaId);
    const status = document.getElementById(statusId);
    if (!ta || !status) return;
    let timer = null;
    let last = ta.value;

    const save = () => {
      const value = ta.value;
      status.textContent = "Saving…";
      postJSON(urlFactory(), { notes: value })
        .then(() => {
          last = value;
          status.textContent = "Saved ✓";
          setTimeout(() => (status.textContent = ""), 1800);
        })
        .catch(() => (status.textContent = "Save failed — retrying soon"));
    };

    ta.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        if (ta.value !== last) save();
      }, 800);
    });
    // Flush on navigation away from the page.
    window.addEventListener("beforeunload", () => {
      if (ta.value !== last) save();
    });
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

    const close = () => {
      listEl.hidden = true;
      highlighted = -1;
    };

    const render = () => {
      listEl.innerHTML = "";
      if (!items.length) {
        listEl.innerHTML = '<div class="typeahead-empty">No matching tasks</div>';
      } else {
        items.forEach((item, i) => {
          const b = document.createElement("button");
          b.type = "button";
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
    };

    const pick = (item) => {
      hidden.value = item.id;
      input.value = item.title;
      close();
      form.submit();
    };

    const load = () => {
      fetch("/tasks/search?q=" + encodeURIComponent(input.value.trim()))
        .then((r) => (r.ok ? r.json() : Promise.reject()))
        .then((data) => {
          items = data.results || [];
          highlighted = -1;
          render();
        })
        .catch(close);
    };

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
        if (dateInput) dateInput.showPicker ? dateInput.showPicker() : dateInput.focus();
      }
    };
    jumpBtn.addEventListener("click", () =>
      setOpen(jumpPop.hidden)
    );
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
})();
