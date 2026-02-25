(function () {
  const KEY = "ytfin-theme";
  const btn = document.getElementById("theme-toggle");
  const root = document.documentElement;

  function apply(theme) {
    if (theme === "light") {
      root.setAttribute("data-theme", "light");
      btn.textContent = "\u2600\uFE0F";
      btn.title = "Switch to dark mode";
    } else {
      root.removeAttribute("data-theme");
      btn.textContent = "\uD83C\uDF19";
      btn.title = "Switch to light mode";
    }
  }

  // Load saved preference, default to dark
  const saved = localStorage.getItem(KEY) || "dark";
  apply(saved);

  btn.addEventListener("click", function () {
    const current = root.getAttribute("data-theme") === "light" ? "light" : "dark";
    const next = current === "dark" ? "light" : "dark";
    localStorage.setItem(KEY, next);
    apply(next);
  });
})();
