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
})();
