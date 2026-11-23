// Folder-path inputs suggest extensions as you type: <input data-suggest="owner/repo"> searches that repository,
// <input data-suggest-from="select-name"> the repository picked in that select of the same form.
document.querySelectorAll("input[data-suggest], input[data-suggest-from]").forEach((input, i) => {
  const list = document.createElement("datalist");
  list.id = `suggest-${i}`;
  input.after(list);
  input.setAttribute("list", list.id);
  input.autocomplete = "off";
  let timer, last = "";
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = input.value.trim();
      const from = input.dataset.suggestFrom && input.form?.elements[input.dataset.suggestFrom];
      const origin = from ? from.value : input.dataset.suggest;
      if (q.length < 2 || !origin || `${origin} ${q}` === last) return;
      last = `${origin} ${q}`;
      try {
        const r = await fetch(`/api/v1/${origin}/extensions?q=${encodeURIComponent(q)}&limit=12`);
        if (!r.ok) return;
        list.replaceChildren(...(await r.json()).items.map((e) => Object.assign(document.createElement("option"),
          { value: e.path, label: `${e.name}${e.lang ? ` (${e.lang})` : ""}` })));
      } catch { /* offline or the API refused: typing still works */ }
    }, 150);
  });
});
