// Folder-path inputs suggest extensions as you type: <input data-suggest="owner/repo"> searches that repository,
// <input data-suggest-from="select-name"> the repository picked in that select of the same form. The list is drawn by
// the page (not a <datalist>, which browsers filter and place differently, and some not at all inside a dialog):
// arrows move, Enter or a click picks, Escape closes.
document.querySelectorAll("input[data-suggest], input[data-suggest-from]").forEach((input, i) => {
  const wrap = document.createElement("span");
  wrap.className = "suggest";
  input.replaceWith(wrap);
  const list = document.createElement("ul");
  list.className = "suggest-list";
  list.id = `suggest-${i}`;
  list.setAttribute("role", "listbox");
  list.hidden = true;
  wrap.append(input, list);
  Object.assign(input, { autocomplete: "off" });
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-controls", list.id);
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-expanded", "false");

  let timer, asked = "", active = -1;
  const items = () => [...list.children];
  const show = (open) => { list.hidden = !open; input.setAttribute("aria-expanded", String(open)); };
  const highlight = (n) => {
    active = n;
    items().forEach((li, k) => li.setAttribute("aria-selected", String(k === n)));
    if (n >= 0) { input.setAttribute("aria-activedescendant", items()[n].id); items()[n].scrollIntoView({ block: "nearest" }); }
    else input.removeAttribute("aria-activedescendant");
  };
  const pick = (li) => { input.value = li.dataset.value; show(false); input.focus(); };

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = input.value.trim();
      const from = input.dataset.suggestFrom && input.form?.elements[input.dataset.suggestFrom];
      const origin = from ? from.value : input.dataset.suggest;
      if (q.length < 2 || !origin) return show(false);
      const key = asked = `${origin} ${q}`;
      try {
        const r = await fetch(`/api/v1/${origin}/extensions?q=${encodeURIComponent(q)}&limit=10`);
        if (!r.ok || key !== asked) return;
        const found = (await r.json()).items;
        list.replaceChildren(...found.map((e, k) => {
          const li = document.createElement("li");
          Object.assign(li, { id: `${list.id}-${k}` });
          li.setAttribute("role", "option");
          li.dataset.value = e.path;
          const name = document.createElement("span");
          name.textContent = e.name + (e.lang ? ` (${e.lang})` : "");
          const path = document.createElement("span");
          path.className = "suggest-path";
          path.textContent = e.path;
          li.append(name, path);
          li.addEventListener("mousedown", (ev) => { ev.preventDefault(); pick(li); });
          return li;
        }));
        if (!found.length) {
          const li = document.createElement("li");
          li.className = "suggest-none";
          li.textContent = `No extension matches “${q}” in ${origin}`;
          list.append(li);
        }
        highlight(-1);
        show(true);
      } catch { /* offline or the API refused: typing a path still works */ }
    }, 150);
  });
  input.addEventListener("keydown", (e) => {
    const options = items().filter((li) => li.dataset.value);
    if (list.hidden || !options.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      highlight((active + (e.key === "ArrowDown" ? 1 : options.length - 1) + (active < 0 && e.key === "ArrowUp" ? 1 : 0)) % options.length);
    } else if (e.key === "Enter" && active >= 0) {
      e.preventDefault();
      pick(options[active]);
    } else if (e.key === "Escape") {
      e.preventDefault();  // closes the list, not the dialog around it
      e.stopPropagation();
      show(false);
    }
  });
  input.addEventListener("blur", () => setTimeout(() => show(false), 100));
});
