// Light is the default. The viewer can switch to dark; the choice is remembered in this browser only.
(function () {
  var KEY = "strata-bench-theme", root = document.documentElement;
  function read() { try { return localStorage.getItem(KEY) === "dark" ? "dark" : "light"; } catch (e) { return "light"; } }
  function save(v) { try { localStorage.setItem(KEY, v); } catch (e) { /* storage blocked: the choice lasts for this page view */ } }
  root.dataset.theme = read();
  document.addEventListener("DOMContentLoaded", function () {
    var nav = document.querySelector("nav");
    if (!nav) return;
    var b = document.createElement("button");
    b.type = "button"; b.className = "theme-toggle";
    function paint() { var dark = root.dataset.theme === "dark"; b.textContent = dark ? "Light mode" : "Dark mode"; b.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode"); }
    b.addEventListener("click", function () { var next = root.dataset.theme === "dark" ? "light" : "dark"; root.dataset.theme = next; save(next); paint(); document.dispatchEvent(new CustomEvent("themechange", { detail: next })); });
    paint(); nav.append(b);
  });
})();
