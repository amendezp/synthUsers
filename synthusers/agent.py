"""Agent drivers.

ComputerUseAgent: the real synthetic user — Claude's computer-use tool driving
the Playwright executor in a screenshot -> decide -> act loop.

ScriptedAgent: replays a fixed action list through the same executor/trace
pipeline. Used for offline smoke tests and pipeline development without API
credits.
"""

from __future__ import annotations

import base64
import dataclasses
import time
from typing import Any, Optional

from .config import COMPUTER_USE_BETA, AgentConfig, Persona
from .executor import ComputerExecutor
from .trace import TraceRecorder

DONE_MARKER = "DONE:"
GAVE_UP_MARKER = "GAVE_UP:"

SYSTEM_TEMPLATE = """You are participating in a usability test as a synthetic user. You control a web browser through the computer tool and must attempt the task below exactly as a real person would.

{persona_block}

Ground rules for realistic behavior:
- Only act on what you can SEE in the screenshots. Do not use developer knowledge: never guess or type URLs to skip ahead, never assume how the site works internally.
- Before each action, briefly say out loud what you are looking at, what you expect, and why you chose the action — including any confusion, hesitation or annoyance you feel. This commentary is the point of the test: think like a user, not like an engineer.
- If something is unclear or frustrating, react the way your persona would (re-read, scroll around, try something else, or give up).
- Do not retry the same failing action more than a couple of times.

When you believe the task is complete, stop using tools and write a final message starting with "DONE:" followed by a short summary. If you decide to abandon the task, write "GAVE_UP:" followed by why you gave up and where you got stuck."""

DEFAULT_PERSONA_BLOCK = "Persona: a typical first-time visitor with average technical skill and average patience."


@dataclasses.dataclass
class RunResult:
    stop_reason: str            # agent_done | agent_gave_up | max_steps | timeout | refusal | error
    final_text: str = ""
    steps: int = 0
    duration_s: float = 0.0
    usage: dict = dataclasses.field(default_factory=dict)
    error: Optional[str] = None


def _persona_block(persona: Optional[Persona]) -> str:
    if persona is None:
        return DEFAULT_PERSONA_BLOCK
    return f"Persona: {persona.name}. {persona.prompt}"


def _extract_reasoning(content: list) -> str:
    """Visible text (not raw thinking) the model produced before acting."""
    parts = []
    for block in content:
        btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
        if btype == "text":
            parts.append(getattr(block, "text", None) or block.get("text", ""))
    return "\n".join(p for p in parts if p).strip()


class ComputerUseAgent:
    def __init__(self, cfg: AgentConfig, persona: Optional[Persona], viewport: dict):
        import anthropic  # deferred so scripted runs work without the SDK configured

        self.cfg = cfg
        self.persona = persona
        self.viewport = viewport
        self.client = anthropic.Anthropic()
        self.usage_totals = {"input_tokens": 0, "output_tokens": 0,
                             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}

    # -- message plumbing ------------------------------------------------

    def _tools(self) -> list[dict]:
        return [{
            "type": "computer_20251124",
            "name": "computer",
            "display_width_px": self.viewport["width"],
            "display_height_px": self.viewport["height"],
            "enable_zoom": True,
        }]

    def _system(self) -> list[dict]:
        return [{
            "type": "text",
            "text": SYSTEM_TEMPLATE.format(persona_block=_persona_block(self.persona)),
            "cache_control": {"type": "ephemeral"},
        }]

    @staticmethod
    def _mark_cache(messages: list[dict]) -> None:
        """Keep a single moving cache breakpoint on the newest user message."""
        for msg in messages:
            if msg["role"] == "user" and isinstance(msg["content"], list):
                for block in msg["content"]:
                    if isinstance(block, dict):
                        block.pop("cache_control", None)
        for msg in reversed(messages):
            if msg["role"] == "user" and isinstance(msg["content"], list):
                last = msg["content"][-1]
                if isinstance(last, dict):
                    last["cache_control"] = {"type": "ephemeral"}
                break

    def _prune_images(self, messages: list[dict]) -> None:
        keep = self.cfg.keep_last_images
        if keep is None:
            return
        seen = 0
        for msg in reversed(messages):
            if msg["role"] != "user" or not isinstance(msg["content"], list):
                continue
            for block in reversed(msg["content"]):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    inner = block.get("content")
                    if not isinstance(inner, list):
                        continue
                    for i, part in enumerate(inner):
                        if isinstance(part, dict) and part.get("type") == "image":
                            seen += 1
                            if seen > keep:
                                inner[i] = {"type": "text", "text": "[earlier screenshot removed]"}

    def _record_usage(self, usage: Any) -> None:
        for key in self.usage_totals:
            self.usage_totals[key] += getattr(usage, key, 0) or 0

    # -- main loop --------------------------------------------------------

    def run(self, executor: ComputerExecutor, trace: TraceRecorder, task: str) -> RunResult:
        start = time.monotonic()
        deadline = start + self.cfg.max_minutes * 60

        initial_png = executor.screenshot()
        initial_path = trace.save_screenshot(initial_png, "initial")
        last_png = initial_png

        messages: list[dict] = [{
            "role": "user",
            "content": [
                {"type": "text", "text": f"Your task:\n{task}\n\nHere is the current state of the browser."},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.b64encode(initial_png).decode()}},
            ],
        }]
        trace.record_step({"action": "initial_state"}, "", executor.page.url, initial_path, None)

        steps = 0
        while True:
            if steps >= self.cfg.max_steps:
                return self._finish("max_steps", "", steps, start)
            if time.monotonic() > deadline:
                return self._finish("timeout", "", steps, start)

            self._prune_images(messages)
            self._mark_cache(messages)

            t0 = time.monotonic()
            try:
                response = self.client.beta.messages.create(
                    model=self.cfg.model,
                    max_tokens=8192,
                    betas=[COMPUTER_USE_BETA],
                    thinking={"type": "adaptive"},
                    output_config={"effort": self.cfg.effort},
                    system=self._system(),
                    tools=self._tools(),
                    messages=messages,
                )
            except Exception as e:
                return self._finish("error", "", steps, start, error=str(e))
            model_latency = time.monotonic() - t0
            self._record_usage(response.usage)

            if response.stop_reason == "refusal":
                return self._finish("refusal", "", steps, start, error="model refused")

            reasoning = _extract_reasoning(response.content)
            tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]

            if not tool_uses:
                stop = "agent_gave_up" if GAVE_UP_MARKER in reasoning else "agent_done"
                return self._finish(stop, reasoning, steps, start)

            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for tu in tool_uses:
                action = dict(tu.input)
                annotated = trace.save_annotated(last_png, action)

                t1 = time.monotonic()
                result = executor.execute(action)
                exec_latency = time.monotonic() - t1

                shot_path = None
                if result.screenshot_png is not None:
                    last_png = result.screenshot_png
                    shot_path = trace.save_screenshot(result.screenshot_png, "after")

                trace.record_step(
                    action=action,
                    reasoning=reasoning,
                    url=executor.page.url,
                    screenshot=shot_path,
                    annotated=annotated,
                    model_latency_s=model_latency,
                    exec_latency_s=exec_latency,
                    error=result.error,
                )
                reasoning = ""  # attribute the model's text to the first action only
                model_latency = 0.0
                steps += 1

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result.to_tool_result_content(),
                    **({"is_error": True} if result.error else {}),
                })

            messages.append({"role": "user", "content": tool_results})

    def _finish(self, stop_reason: str, final_text: str, steps: int, start: float,
                error: Optional[str] = None) -> RunResult:
        return RunResult(
            stop_reason=stop_reason,
            final_text=final_text,
            steps=steps,
            duration_s=time.monotonic() - start,
            usage=dict(self.usage_totals),
            error=error,
        )


class ScriptedAgent:
    """Replays a fixed action script through the executor. No API calls."""

    def __init__(self, cfg: AgentConfig, persona: Optional[Persona], viewport: dict):
        self.cfg = cfg
        self.script = cfg.script or []

    def run(self, executor: ComputerExecutor, trace: TraceRecorder, task: str) -> RunResult:
        start = time.monotonic()
        initial_png = executor.screenshot()
        trace.record_step({"action": "initial_state"}, "", executor.page.url,
                          trace.save_screenshot(initial_png, "initial"), None)
        last_png = initial_png
        final_text = ""
        steps = 0

        for entry in self.script:
            entry = dict(entry)
            reasoning = entry.pop("reasoning", "")
            # pause_s is simulated think-time: recorded as deliberation latency in
            # the trace, but the actual wait is capped so smoke runs stay fast.
            pause = float(entry.pop("pause_s", 0) or 0)
            if "final" in entry:
                final_text = entry["final"]
                break
            if pause:
                executor.page.wait_for_timeout(int(min(pause, 2.0) * 1000))
            if entry.get("action") == "click_selector":
                entry = self._resolve_selector_click(executor, entry)
            annotated = trace.save_annotated(last_png, entry)
            t1 = time.monotonic()
            result = executor.execute(entry)
            exec_latency = time.monotonic() - t1
            shot_path = None
            if result.screenshot_png is not None:
                last_png = result.screenshot_png
                shot_path = trace.save_screenshot(result.screenshot_png, "after")
            trace.record_step(entry, reasoning, executor.page.url, shot_path, annotated,
                              model_latency_s=pause, exec_latency_s=exec_latency,
                              error=result.error)
            steps += 1

        stop = "agent_done" if DONE_MARKER in final_text else (
            "agent_gave_up" if GAVE_UP_MARKER in final_text else "agent_done")
        return RunResult(stop_reason=stop, final_text=final_text, steps=steps,
                         duration_s=time.monotonic() - start)

    @staticmethod
    def _resolve_selector_click(executor: ComputerExecutor, entry: dict) -> dict:
        """Turn {action: click_selector, selector: ...} into a coordinate click,
        scrolling the element into view first (as a user would)."""
        selector = entry["selector"]
        loc = executor.page.locator(selector).first
        loc.scroll_into_view_if_needed(timeout=5000)
        executor.page.wait_for_timeout(150)
        box = loc.bounding_box()
        if box is None:
            raise RuntimeError(f"selector not visible: {selector}")
        return {"action": "left_click",
                "coordinate": [int(box["x"] + box["width"] / 2),
                               int(box["y"] + box["height"] / 2)]}


def make_agent(cfg: AgentConfig, persona: Optional[Persona], viewport: dict):
    if cfg.driver == "scripted":
        return ScriptedAgent(cfg, persona, viewport)
    return ComputerUseAgent(cfg, persona, viewport)
