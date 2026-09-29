// Theme switch and copy buttons. Loaded in <head>, so a saved theme applies before the first paint.
(function () {
  "use strict";
  var root = document.documentElement;
  try {
    if (localStorage.getItem("theme") === "light") root.dataset.theme = "light";
  } catch (e) {}

  function syncThemeColor() {
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = root.dataset.theme === "light" ? "#f2f6f4" : "#090e0c";
  }
  document.addEventListener("DOMContentLoaded", syncThemeColor);

  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-theme-toggle]")) {
      if (root.dataset.theme === "light") delete root.dataset.theme;
      else root.dataset.theme = "light";
      try { localStorage.setItem("theme", root.dataset.theme || "dark"); } catch (e) {}
      syncThemeColor();
      return;
    }

    var button = event.target.closest("[data-copy], [data-copy-from]");
    if (!button || !navigator.clipboard) return;
    var source = button.dataset.copyFrom && document.getElementById(button.dataset.copyFrom);
    var text = source ? source.textContent : button.dataset.copy;
    navigator.clipboard.writeText(text).then(function () {
      var label = button.getAttribute("aria-label");
      button.classList.add("copied");
      button.setAttribute("aria-label", "Copied");
      setTimeout(function () {
        button.classList.remove("copied");
        button.setAttribute("aria-label", label);
      }, 1600);
    });
  });
})();
