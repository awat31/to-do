(function () {
  "use strict";

  const STORAGE_KEY = "bright-tasks-v1";

  const input       = document.getElementById("task-input");
  const prioritySel = document.getElementById("priority-select");
  const addBtn      = document.getElementById("add-btn");
  const list        = document.getElementById("task-list");
  const countEl     = document.getElementById("count");
  const doneCountEl = document.getElementById("done-count");
  const clearBtn    = document.getElementById("clear-btn");
  const progressBar = document.getElementById("progress-bar");
  const filterBtns  = Array.from(document.querySelectorAll(".filter-btn"));

  let tasks = load();
  let filter = "all";

  function load() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      const parsed = raw ? JSON.parse(raw) : null;
      return Array.isArray(parsed) ? parsed : seed();
    } catch {
      return seed();
    }
  }

  function seed() {
    // A few starter tasks so the app doesn't look empty on first run.
    return [
      { id: 1, text: "Welcome! Click the circle to complete me.", priority: "medium", done: false },
      { id: 2, text: "Try adding your own task →",            priority: "low",    done: false },
      { id: 3, text: "This one is high priority",             priority: "high",   done: true  },
    ];
  }

  function save() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks));
  }

  function render() {
    list.innerHTML = "";

    const visible = tasks.filter(function (t) {
      if (filter === "active") return !t.done;
      if (filter === "done")   return  t.done;
      return true;
    });

    if (visible.length === 0) {
      const li = document.createElement("li");
      li.className = "empty";
      li.innerHTML = '<span class="big">🎉</span>' +
        (filter === "done" ? "Nothing completed yet." :
         filter === "active" ? "All caught up — nice!" : "No tasks yet. Add one above!");
      list.appendChild(li);
    }

    visible.forEach(function (task) {
      const li = document.createElement("li");
      li.className = "task " + task.priority + (task.done ? " done" : "");
      li.dataset.id = task.id;

      const check = document.createElement("input");
      check.type = "checkbox";
      check.className = "check";
      check.checked = task.done;
      check.setAttribute("aria-label", "Mark task complete");
      check.addEventListener("change", function () {
        task.done = check.checked;
        save();
        render();
      });

      const text = document.createElement("span");
      text.className = "task-text";
      text.textContent = task.text;

      const tag = document.createElement("span");
      tag.className = "priority-tag tag-" + task.priority;
      tag.textContent = task.priority;

      const del = document.createElement("button");
      del.className = "delete-btn";
      del.textContent = "✕";
      del.title = "Delete task";
      del.setAttribute("aria-label", "Delete task");
      del.addEventListener("click", function () {
        tasks = tasks.filter(function (t) { return t.id !== task.id; });
        save();
        render();
      });

      li.appendChild(check);
      li.appendChild(text);
      li.appendChild(tag);
      li.appendChild(del);
      list.appendChild(li);
    });

    const done = tasks.filter(function (t) { return t.done; }).length;
    const total = tasks.length;

    countEl.textContent = total === 0 ? "" : visible.length + " shown";
    doneCountEl.textContent = total === 0 ? "" : done + " of " + total + " done";
    progressBar.style.width = total === 0 ? "0%" : Math.round((done / total) * 100) + "%";

    clearBtn.style.visibility = done > 0 ? "visible" : "hidden";
  }

  function addTask() {
    const text = input.value.trim();
    if (!text) {
      input.focus();
      return;
    }
    tasks.unshift({
      id: Date.now() + Math.random().toString(16).slice(2),
      text: text,
      priority: prioritySel.value,
      done: false,
    });
    input.value = "";
    save();
    render();
    input.focus();
  }

  addBtn.addEventListener("click", addTask);
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter") addTask();
  });

  prioritySel.addEventListener("keydown", function (e) {
    if (e.key === "Enter") addTask();
  });

  filterBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      filter = btn.dataset.filter;
      filterBtns.forEach(function (b) { b.classList.toggle("active", b === btn); });
      render();
    });
  });

  clearBtn.addEventListener("click", function () {
    tasks = tasks.filter(function (t) { return !t.done; });
    save();
    render();
  });

  render();
})();
