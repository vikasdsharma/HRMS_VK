/* HRMS front-end behaviour. Kept in one external file so the Content-Security-Policy can forbid inline scripts. */
(function () {
  "use strict";

  // Switch organization from the top-bar dropdown (platform admin)
  document.querySelectorAll("select[data-org-switch]").forEach(function (sel) {
    sel.addEventListener("change", function () {
      if (!sel.value) return;
      var f = sel.form;
      f.action = "/orgs/" + encodeURIComponent(sel.value) + "/switch";
      f.submit();
    });
  });

  // Ask before destructive actions:  <form data-confirm="Delete this?">  or  <button data-confirm="...">
  document.addEventListener("submit", function (e) {
    var form = e.target;
    var msg = form.getAttribute("data-confirm") ||
      (e.submitter && e.submitter.getAttribute("data-confirm"));
    if (msg && !window.confirm(msg)) { e.preventDefault(); return; }
    // Block double-submits (double-click on Generate / Upload)
    if (form.hasAttribute("data-once") || form.method.toLowerCase() === "post") {
      if (form.dataset.submitted === "1") { e.preventDefault(); return; }
      form.dataset.submitted = "1";
      form.querySelectorAll("button:not([type=button]),input[type=submit]").forEach(function (b) {
        b.classList.add("busy");
        setTimeout(function () { b.disabled = true; }, 0);
      });
      // downloads never navigate away, so re-enable shortly after
      if (form.hasAttribute("data-download")) {
        setTimeout(function () {
          form.dataset.submitted = "0";
          form.querySelectorAll("button,input[type=submit]").forEach(function (b) { b.disabled = false; b.classList.remove("busy"); });
        }, 4000);
      }
    }
  });

  // Ask before following a link that exports sensitive data:  <a data-confirm-link="...">
  document.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a[data-confirm-link]");
    if (a && !window.confirm(a.getAttribute("data-confirm-link"))) { e.preventDefault(); }
  });

  // Auto-submit filter selects:  <select data-autosubmit>
  document.querySelectorAll("[data-autosubmit]").forEach(function (s) {
    s.addEventListener("change", function () { s.form.submit(); });
  });

  // Coming back with the Back button must not leave buttons disabled
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    document.querySelectorAll("form").forEach(function (f) { f.dataset.submitted = "0"; });
    document.querySelectorAll("button.busy").forEach(function (b) { b.disabled = false; b.classList.remove("busy"); });
  });

  // Mobile menu toggle
  var t = document.getElementById("nav-toggle");
  if (t) t.addEventListener("click", function () {
    var nav = document.getElementById("main-nav");
    var open = nav.classList.toggle("open");
    t.setAttribute("aria-expanded", open ? "true" : "false");
  });

  // Dismiss flash messages
  document.querySelectorAll(".flash .x").forEach(function (b) {
    b.addEventListener("click", function () { b.parentElement.remove(); });
  });

  // Warn before leaving a form with unsaved edits:  <form data-dirty-warn>
  document.querySelectorAll("form[data-dirty-warn]").forEach(function (form) {
    var dirty = false;
    form.addEventListener("input", function () { dirty = true; });
    form.addEventListener("submit", function () { dirty = false; });
    window.addEventListener("beforeunload", function (e) { if (dirty) { e.preventDefault(); e.returnValue = ""; } });
  });
})();
