"""Executes computer-use tool actions against a Playwright page.

Every mouse action first glides the visual cursor overlay to the target (so
videos show intent before the click), then performs the real input. Every
action returns a fresh screenshot so the model always sees the result without
spending an extra turn asking for one.
"""

from __future__ import annotations

import base64
import io
import time
from typing import Any, Optional

from PIL import Image
from playwright.sync_api import Page

# Map computer-use key names to Playwright key names (subset that differs).
KEY_ALIASES = {
    "return": "Enter", "enter": "Enter", "tab": "Tab", "escape": "Escape", "esc": "Escape",
    "space": " ", "backspace": "Backspace", "delete": "Delete", "up": "ArrowUp",
    "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight", "home": "Home",
    "end": "End", "page_up": "PageUp", "pagedown": "PageDown", "page_down": "PageDown",
    "pageup": "PageUp", "super": "Meta", "cmd": "Meta", "command": "Meta",
    "ctrl": "Control", "control": "Control", "alt": "Alt", "shift": "Shift",
}

MODIFIERS = {"shift": "Shift", "ctrl": "Control", "control": "Control", "alt": "Alt",
             "super": "Meta", "cmd": "Meta", "command": "Meta"}


def _normalize_key_combo(combo: str) -> str:
    parts = [p.strip() for p in combo.replace(" ", "+").split("+") if p.strip()]
    normalized = []
    for p in parts:
        low = p.lower()
        normalized.append(KEY_ALIASES.get(low, p if len(p) > 1 else p))
    return "+".join(normalized)


class ActionResult:
    def __init__(self, output: str = "", screenshot_png: Optional[bytes] = None,
                 error: Optional[str] = None):
        self.output = output
        self.screenshot_png = screenshot_png
        self.error = error

    def to_tool_result_content(self) -> list[dict]:
        content: list[dict] = []
        text = self.error or self.output
        if text:
            content.append({"type": "text", "text": text})
        if self.screenshot_png is not None:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(self.screenshot_png).decode(),
                },
            })
        return content or [{"type": "text", "text": "done"}]


class ComputerExecutor:
    def __init__(self, page: Page, viewport: dict, settle_ms: int = 350):
        self.page = page
        self.viewport = viewport
        self.settle_ms = settle_ms
        self.cursor_pos: tuple[int, int] = (viewport["width"] // 2, viewport["height"] // 2)

    # -- overlay sync ------------------------------------------------------

    def _overlay(self, fn: str, *args) -> None:
        try:
            self.page.evaluate(f"args => window.{fn} && window.{fn}(...args)", list(args))
        except Exception:
            pass  # overlay is best-effort; never fail an action over it

    def _glide_to(self, x: int, y: int) -> None:
        # Re-seed the overlay at the last known position (navigation resets the
        # DOM), then animate to the target and move the real virtual mouse.
        self._overlay("__suMoveCursor", self.cursor_pos[0], self.cursor_pos[1], True)
        self._overlay("__suMoveCursor", x, y, False)
        self.page.mouse.move(x, y, steps=12)
        self.page.wait_for_timeout(240)  # let the glide finish before acting
        self.cursor_pos = (x, y)

    def _ripple(self, x: int, y: int) -> None:
        self._overlay("__suClickRipple", x, y)

    # -- screenshots ---------------------------------------------------------

    def screenshot(self) -> bytes:
        # Keep the overlay visible: it represents the mouse position, which is
        # information a human user would also have.
        return self.page.screenshot()

    def _settle_and_shoot(self) -> bytes:
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=3000)
        except Exception:
            pass
        self.page.wait_for_timeout(self.settle_ms)
        self._overlay("__suMoveCursor", self.cursor_pos[0], self.cursor_pos[1], True)
        return self.screenshot()

    # -- action dispatch ------------------------------------------------------

    def execute(self, action: dict[str, Any]) -> ActionResult:
        kind = action.get("action")
        try:
            handler = getattr(self, f"_do_{kind}", None)
            if handler is None:
                return ActionResult(error=f"Unsupported action: {kind}")
            return handler(action)
        except Exception as e:  # surface failures to the model so it can adapt
            return ActionResult(error=f"Action {kind} failed: {e}",
                                screenshot_png=self._safe_shot())

    def _safe_shot(self) -> Optional[bytes]:
        try:
            return self.screenshot()
        except Exception:
            return None

    # -- handlers ---------------------------------------------------------

    def _do_screenshot(self, action: dict) -> ActionResult:
        return ActionResult(screenshot_png=self._settle_and_shoot())

    def _click(self, action: dict, button: str, count: int = 1) -> ActionResult:
        x, y = action["coordinate"]
        self._glide_to(int(x), int(y))
        modifier = MODIFIERS.get(str(action.get("text", "")).lower())
        if modifier:
            self.page.keyboard.down(modifier)
        self._ripple(int(x), int(y))
        self.page.mouse.click(int(x), int(y), button=button, click_count=count)
        if modifier:
            self.page.keyboard.up(modifier)
        return ActionResult(screenshot_png=self._settle_and_shoot())

    def _do_left_click(self, action: dict) -> ActionResult:
        return self._click(action, "left")

    def _do_right_click(self, action: dict) -> ActionResult:
        return self._click(action, "right")

    def _do_middle_click(self, action: dict) -> ActionResult:
        return self._click(action, "middle")

    def _do_double_click(self, action: dict) -> ActionResult:
        return self._click(action, "left", count=2)

    def _do_triple_click(self, action: dict) -> ActionResult:
        return self._click(action, "left", count=3)

    def _do_mouse_move(self, action: dict) -> ActionResult:
        x, y = action["coordinate"]
        self._glide_to(int(x), int(y))
        return ActionResult(screenshot_png=self._settle_and_shoot())

    def _do_left_click_drag(self, action: dict) -> ActionResult:
        sx, sy = action.get("start_coordinate", self.cursor_pos)
        ex, ey = action["coordinate"]
        self._glide_to(int(sx), int(sy))
        self.page.mouse.down()
        self._glide_to(int(ex), int(ey))
        self.page.mouse.up()
        return ActionResult(screenshot_png=self._settle_and_shoot())

    def _do_left_mouse_down(self, action: dict) -> ActionResult:
        coord = action.get("coordinate")
        if coord:
            self._glide_to(int(coord[0]), int(coord[1]))
        self.page.mouse.down()
        return ActionResult(output="mouse down")

    def _do_left_mouse_up(self, action: dict) -> ActionResult:
        coord = action.get("coordinate")
        if coord:
            self._glide_to(int(coord[0]), int(coord[1]))
        self.page.mouse.up()
        return ActionResult(screenshot_png=self._settle_and_shoot())

    def _do_type(self, action: dict) -> ActionResult:
        self.page.keyboard.type(action.get("text", ""), delay=28)
        return ActionResult(screenshot_png=self._settle_and_shoot())

    def _do_key(self, action: dict) -> ActionResult:
        combo = _normalize_key_combo(action.get("text", ""))
        repeat = int(action.get("repeat", 1) or 1)
        for _ in range(max(1, repeat)):
            self.page.keyboard.press(combo)
        return ActionResult(screenshot_png=self._settle_and_shoot())

    def _do_hold_key(self, action: dict) -> ActionResult:
        key = _normalize_key_combo(action.get("text", ""))
        duration = float(action.get("duration", 1.0) or 1.0)
        self.page.keyboard.down(key)
        self.page.wait_for_timeout(int(min(duration, 5.0) * 1000))
        self.page.keyboard.up(key)
        return ActionResult(screenshot_png=self._settle_and_shoot())

    def _do_scroll(self, action: dict) -> ActionResult:
        coord = action.get("coordinate") or list(self.cursor_pos)
        x, y = int(coord[0]), int(coord[1])
        self._glide_to(x, y)
        amount = int(action.get("scroll_amount", 3) or 3)
        direction = action.get("scroll_direction", "down")
        pixels = amount * 100
        dx, dy = 0, 0
        if direction == "down":
            dy = pixels
        elif direction == "up":
            dy = -pixels
        elif direction == "right":
            dx = pixels
        elif direction == "left":
            dx = -pixels
        modifier = MODIFIERS.get(str(action.get("text", "")).lower())
        if modifier:
            self.page.keyboard.down(modifier)
        self.page.mouse.wheel(dx, dy)
        if modifier:
            self.page.keyboard.up(modifier)
        return ActionResult(screenshot_png=self._settle_and_shoot())

    def _do_wait(self, action: dict) -> ActionResult:
        duration = float(action.get("duration", 1.0) or 1.0)
        self.page.wait_for_timeout(int(min(duration, 10.0) * 1000))
        return ActionResult(screenshot_png=self._settle_and_shoot())

    def _do_zoom(self, action: dict) -> ActionResult:
        x1, y1, x2, y2 = [int(v) for v in action["region"]]
        clip = {"x": x1, "y": y1, "width": max(1, x2 - x1), "height": max(1, y2 - y1)}
        png = self.page.screenshot(clip=clip)
        img = Image.open(io.BytesIO(png))
        img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return ActionResult(output=f"Zoomed view of region {action['region']}",
                            screenshot_png=buf.getvalue())

    def _do_cursor_position(self, action: dict) -> ActionResult:
        return ActionResult(output=f"Cursor at {self.cursor_pos}")
