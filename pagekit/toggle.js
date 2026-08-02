/* pagekit/toggle.js — theme toggle + persistence for report pages (WP3).
 * Inlined via $PAGEKIT at render time (same contract as kit.css). Never <script src=>.
 * localStorage key / data-theme values match the garden (dashboard/generate.py, WP2):
 * key "loops-theme", values "dark"|"light", attribute data-theme on <html>. */
(function () {
  var KEY = "loops-theme";
  var root = document.documentElement;

  // Pre-paint stamp: runs when this script is inlined in <head> before <style>.
  try {
    var saved = localStorage.getItem(KEY);
    if (saved === "dark" || saved === "light") {
      root.setAttribute("data-theme", saved);
    }
  } catch (e) {}

  function resolvedTheme() {
    var current = root.getAttribute("data-theme");
    if (current === "dark" || current === "light") return current;
    if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      return "dark";
    }
    return "light";
  }

  function toggleTheme() {
    var next = resolvedTheme() === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try {
      localStorage.setItem(KEY, next);
    } catch (e) {}
  }

  function wire() {
    var btn = document.getElementById("theme-toggle");
    if (btn) btn.addEventListener("click", toggleTheme);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
})();
