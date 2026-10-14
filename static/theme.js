(function () {
  const KEY = "ytfin-theme";
  const root = document.documentElement;
  const btn = document.getElementById("theme-toggle");

  function current() {
    return root.getAttribute("data-theme") === "light" ? "light" : "dark";
  }

  function apply(theme) {
    if (theme === "light") root.setAttribute("data-theme", "light");
    else root.removeAttribute("data-theme");
    if (btn) {
      btn.textContent = theme === "light" ? "☀️" : "🌙";
      btn.title = theme === "light" ? "Switch to dark mode" : "Switch to light mode";
    }
  }

  apply(current());

  if (btn) {
    btn.addEventListener("click", function () {
      const next = current() === "dark" ? "light" : "dark";
      try { localStorage.setItem(KEY, next); } catch (e) { /* storage may be blocked */ }
      apply(next);
    });
  }
})();
