"""Per-run trace capture: JSONL step log, raw + annotated screenshots, run metadata.

The trace is the core asset of the harness — everything downstream (verdicts,
friction detection, the report) reads from it.
"""

from __future__ import annotations

import io
import json
import pathlib
import time
from typing import Any, Optional

from PIL import Image, ImageDraw


class TraceRecorder:
    def __init__(self, run_dir: pathlib.Path):
        self.run_dir = run_dir
        self.shots_dir = run_dir / "shots"
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        self._trace_file = (run_dir / "trace.jsonl").open("a")
        self.step_index = 0

    # -- screenshots -----------------------------------------------------

    def save_screenshot(self, png_bytes: bytes, label: str) -> str:
        name = f"{self.step_index:03d}_{label}.png"
        (self.shots_dir / name).write_bytes(png_bytes)
        return f"shots/{name}"

    def save_annotated(self, png_bytes: bytes, action: dict, label: str = "annotated") -> Optional[str]:
        """Draw the action's target on the screenshot: click marker, scroll
        arrow, or drag line. Returns a run-dir-relative path, or None when the
        action has nothing to draw."""
        marker = self._annotate(png_bytes, action)
        if marker is None:
            return None
        name = f"{self.step_index:03d}_{label}.png"
        marker.save(self.shots_dir / name, format="PNG")
        return f"shots/{name}"

    @staticmethod
    def _annotate(png_bytes: bytes, action: dict) -> Optional[Image.Image]:
        kind = action.get("action", "")
        coord = action.get("coordinate")
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        draw = ImageDraw.Draw(img)
        red = (224, 49, 27)

        def crosshair(x: int, y: int, color=red):
            r = 16
            draw.ellipse([x - r, y - r, x + r, y + r], outline=color, width=4)
            draw.line([x - r - 8, y, x - r + 4, y], fill=color, width=3)
            draw.line([x + r - 4, y, x + r + 8, y], fill=color, width=3)
            draw.line([x, y - r - 8, x, y - r + 4], fill=color, width=3)
            draw.line([x, y + r - 4, x, y + r + 8], fill=color, width=3)

        if kind in {"left_click", "right_click", "middle_click", "double_click", "triple_click", "mouse_move"} and coord:
            crosshair(int(coord[0]), int(coord[1]))
        elif kind == "left_click_drag":
            start = action.get("start_coordinate")
            end = action.get("coordinate")
            if not (start and end):
                return None
            draw.line([tuple(start), tuple(end)], fill=red, width=4)
            crosshair(int(end[0]), int(end[1]))
        elif kind == "scroll" and coord:
            x, y = int(coord[0]), int(coord[1])
            direction = action.get("scroll_direction", "down")
            dy = {"down": 60, "up": -60}.get(direction, 0)
            dx = {"right": 60, "left": -60}.get(direction, 0)
            draw.line([x, y, x + dx, y + dy], fill=red, width=4)
            crosshair(x, y)
        elif kind == "type" and coord is None:
            return None
        else:
            return None
        return img

    # -- step log ---------------------------------------------------------

    def record_step(
        self,
        action: dict,
        reasoning: str,
        url: str,
        screenshot: Optional[str],
        annotated: Optional[str],
        model_latency_s: float = 0.0,
        exec_latency_s: float = 0.0,
        error: Optional[str] = None,
    ) -> None:
        entry = {
            "step": self.step_index,
            "ts": time.time(),
            "action": action,
            "reasoning": reasoning,
            "url": url,
            "screenshot": screenshot,
            "annotated": annotated,
            "model_latency_s": round(model_latency_s, 3),
            "exec_latency_s": round(exec_latency_s, 3),
            "error": error,
        }
        self._trace_file.write(json.dumps(entry) + "\n")
        self._trace_file.flush()
        self.step_index += 1

    def close(self) -> None:
        self._trace_file.close()

    def write_meta(self, meta: dict[str, Any]) -> None:
        (self.run_dir / "meta.json").write_text(json.dumps(meta, indent=2))


def read_trace(run_dir: pathlib.Path) -> list[dict]:
    path = run_dir / "trace.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def read_meta(run_dir: pathlib.Path) -> dict:
    path = run_dir / "meta.json"
    return json.loads(path.read_text()) if path.exists() else {}
