// Gallery filters apply as soon as they change: selects and checkboxes at once, a tri-state list when it closes,
// the text box on Enter or when it loses focus. The Apply button stays for browsers without JavaScript.
const form = document.getElementById("gallery-filters");
if (form) {
  document.documentElement.classList.add("js");
  const go = () => form.requestSubmit();
  // Leave unset fields out of the address: ?fix=noissue, not ?q=&broken=&fix=noissue&content=.
  form.addEventListener("submit", () => {
    for (const el of form.elements) if (el.name && !el.disabled && el.value === "" && el.type !== "submit") el.disabled = true;
  });
  form.addEventListener("change", (e) => { if (!e.target.closest(".tri")) go(); });
  for (const tri of form.querySelectorAll("details.tri")) {
    let before = "";
    tri.addEventListener("toggle", () => {
      const now = [...tri.querySelectorAll("input:not([disabled])")].map((i) => i.value).join();
      if (tri.open) before = now; else if (now !== before) go();
    });
  }
  // On phones the filters fold behind "Filters (n)" unless some are set.
  const box = form.closest("details.filter-box");
  if (box && matchMedia("(max-width: 600px)").matches && !box.querySelector("summary").textContent.includes("(")) box.open = false;
}
