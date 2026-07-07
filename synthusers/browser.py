"""Browser session helpers: launch, video recording, and the visual cursor overlay.

The overlay is a DOM element injected into every page so that videos and
screenshots show where the agent's cursor is and what it clicks — the
"watch the agent work" layer.
"""

from __future__ import annotations

import os
import pathlib
from typing import Optional

from playwright.sync_api import Browser, BrowserContext, Page, Playwright

FALLBACK_CHROMIUM = os.environ.get("SYNTHUSERS_CHROMIUM", "/opt/pw-browsers/chromium")

# Injected on every navigation. Creates a fake cursor + click ripple that
# playwright's virtual mouse doesn't render. pointer-events:none keeps it
# invisible to hit-testing, so it never interferes with the page.
CURSOR_OVERLAY_JS = r"""
(() => {
  if (window.__suInstalled) return;
  window.__suInstalled = true;

  const CURSOR_SVG = `<svg width="24" height="24" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
    <path d="M4 2 L4 19 L8.5 15.5 L11.5 22 L14.5 20.5 L11.5 14 L17 14 Z"
          fill="#111" stroke="#fff" stroke-width="1.6"/></svg>`;

  function ensureCursor() {
    let el = document.getElementById('__su_cursor');
    if (el) return el;
    el = document.createElement('div');
    el.id = '__su_cursor';
    el.innerHTML = CURSOR_SVG;
    Object.assign(el.style, {
      position: 'fixed', left: '0px', top: '0px', width: '24px', height: '24px',
      zIndex: '2147483647', pointerEvents: 'none',
      transition: 'transform 0.22s cubic-bezier(0.25, 0.6, 0.35, 1)',
      transform: 'translate(-100px, -100px)',
      filter: 'drop-shadow(0 1px 2px rgba(0,0,0,0.4))',
    });
    (document.body || document.documentElement).appendChild(el);
    return el;
  }

  window.__suMoveCursor = (x, y, instant) => {
    const el = ensureCursor();
    if (instant) {
      const prev = el.style.transition;
      el.style.transition = 'none';
      el.style.transform = `translate(${x}px, ${y}px)`;
      // force reflow so the transition re-enables cleanly
      void el.offsetWidth;
      el.style.transition = prev;
    } else {
      el.style.transform = `translate(${x}px, ${y}px)`;
    }
    window.__suPos = [x, y];
  };

  window.__suClickRipple = (x, y, color) => {
    ensureCursor();
    const r = document.createElement('div');
    Object.assign(r.style, {
      position: 'fixed', left: (x - 18) + 'px', top: (y - 18) + 'px',
      width: '36px', height: '36px', borderRadius: '50%',
      border: `3px solid ${color || '#e0311b'}`, zIndex: '2147483646',
      pointerEvents: 'none', opacity: '0.9',
      animation: '__su_ripple 0.5s ease-out forwards',
    });
    if (!document.getElementById('__su_style')) {
      const s = document.createElement('style');
      s.id = '__su_style';
      s.textContent = '@keyframes __su_ripple { from { transform: scale(0.4); opacity: 0.9; } to { transform: scale(1.6); opacity: 0; } }';
      (document.head || document.documentElement).appendChild(s);
    }
    (document.body || document.documentElement).appendChild(r);
    setTimeout(() => r.remove(), 600);
  };
})();
"""


def launch_browser(pw: Playwright, headed: bool = False) -> Browser:
    """Launch Chromium, falling back to the preinstalled binary when the
    Playwright-pinned build isn't present (common in managed containers)."""
    try:
        return pw.chromium.launch(headless=not headed)
    except Exception:
        return pw.chromium.launch(headless=not headed, executable_path=FALLBACK_CHROMIUM)


def new_session(
    browser: Browser,
    viewport: dict,
    video_dir: Optional[pathlib.Path] = None,
) -> tuple[BrowserContext, Page]:
    kwargs: dict = {"viewport": viewport}
    if video_dir is not None:
        video_dir.mkdir(parents=True, exist_ok=True)
        kwargs["record_video_dir"] = str(video_dir)
        kwargs["record_video_size"] = viewport
    context = browser.new_context(**kwargs)
    context.add_init_script(CURSOR_OVERLAY_JS)
    page = context.new_page()
    return context, page


def finalize_video(context: BrowserContext, page: Page, dest: pathlib.Path) -> Optional[pathlib.Path]:
    """Close the context (which flushes the recording) and move the video to `dest`."""
    video = page.video
    context.close()
    if video is None:
        return None
    try:
        path = pathlib.Path(video.path())
        dest.parent.mkdir(parents=True, exist_ok=True)
        path.replace(dest)
        return dest
    except Exception:
        return None
