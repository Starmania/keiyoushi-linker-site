// Links to other sites (GitHub, extension websites, issue links) open in a new tab, on every page, including links
// added later by scripts. App links (intent:, tachiyomi:) and this site's own pages are left alone.
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[href]");
  if (!a || a.target || !/^https?:$/.test(a.protocol) || a.origin === location.origin) return;
  a.target = "_blank";
  a.relList.add("noopener");
});
