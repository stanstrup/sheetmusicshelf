/* Keep the screen on while a score is open, and offer real fullscreen.
 *
 * The single most phone-specific thing this app needs: your hands are on the
 * keys, so nothing is going to tap the screen for the next four minutes, and
 * a score that dims mid-phrase is worse than useless.
 *
 * Wake Lock is not available everywhere (notably not on iOS Safari before 16.4
 * and not over plain HTTP on some builds), so the control hides itself rather
 * than offering a button that cannot work.
 */
(function () {
  "use strict";

  var button = document.getElementById("awake");
  var full = document.getElementById("fullscreen");
  var supported = "wakeLock" in navigator;
  var lock = null;

  if (button && !supported) {
    button.hidden = true;
  }

  function setState(on) {
    if (!button) { return; }
    button.setAttribute("aria-pressed", String(on));
    button.title = on ? "Screen will stay on" : "Keep the screen on";
  }

  function request() {
    return navigator.wakeLock.request("screen").then(function (sentinel) {
      lock = sentinel;
      setState(true);
      // The browser drops the lock whenever the tab is hidden; note it so the
      // button never claims to be holding one it lost.
      sentinel.addEventListener("release", function () {
        lock = null;
        setState(false);
      });
    }).catch(function () {
      setState(false);
    });
  }

  function release() {
    if (lock) { lock.release().catch(function () {}); }
    lock = null;
    setState(false);
  }

  if (button && supported) {
    button.addEventListener("click", function () {
      if (lock) { release(); } else { request(); }
    });

    // Re-acquire after the tab comes back, since the lock is dropped on hide.
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible" &&
          button.getAttribute("aria-pressed") === "true" && !lock) {
        request();
      }
    });
  }

  if (full) {
    if (!document.documentElement.requestFullscreen) {
      full.hidden = true;
    } else {
      full.addEventListener("click", function () {
        if (document.fullscreenElement) {
          document.exitFullscreen().catch(function () {});
        } else {
          document.documentElement.requestFullscreen().catch(function () {});
        }
      });
      document.addEventListener("fullscreenchange", function () {
        full.setAttribute("aria-pressed", String(!!document.fullscreenElement));
      });
    }
  }
}());
