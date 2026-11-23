// Buttons post a form and come back to the same page (a full reload). Keep the reader's place: remember the scroll
// position and which <details> were open before the post, put them back after, and show the result as a toast.
const KEY = "keep-place:" + location.pathname;
document.addEventListener("submit", (e) => {
  if (e.target.method !== "post") return;
  const open = [...document.querySelectorAll("details")].map((d) => d.open);
  try { sessionStorage.setItem(KEY, JSON.stringify({ y: scrollY, open })); } catch { /* private mode: plain reload */ }
});
addEventListener("DOMContentLoaded", () => {
  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem(KEY)); sessionStorage.removeItem(KEY); } catch { /* none */ }
  const params = new URLSearchParams(location.search);
  if (saved && params.has("notice")) {
    const details = document.querySelectorAll("details");
    if (details.length === saved.open.length) details.forEach((d, i) => { d.open = saved.open[i]; });
    scrollTo(0, saved.y);
  }
  const notice = document.querySelector("main > .notice[role=status]");
  if (notice) {
    notice.classList.add("toast");
    setTimeout(() => notice.classList.add("gone"), 4000);
    params.delete("notice");  // a reload or a shared link doesn't repeat it
    history.replaceState(null, "", location.pathname + (params.size ? "?" + params : "") + location.hash);
  }
});
