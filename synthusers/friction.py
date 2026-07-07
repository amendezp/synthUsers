"""Friction detection.

Layer 1 — deterministic heuristics over the trace: backtracks, rage clicks,
wandering, action errors, long deliberation (hesitation proxy).
Layer 2 — an optional LLM pass that reads the agent's own commentary and labels
confusion moments with evidence quotes.
Batch level — events from all runs are clustered into the report's headline
findings ("5 of 30 users hesitated at the pricing step").
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from typing import Optional
from urllib.parse import urlparse

CLICK_ACTIONS = {"left_click", "double_click", "right_click", "triple_click"}
PASSIVE_ACTIONS = {"scroll", "screenshot", "mouse_move", "zoom", "wait"}
HESITATION_LATENCY_S = 25.0
RAGE_RADIUS_PX = 28
RAGE_COUNT = 3
WANDER_LEN = 4

CONFUSION_PATTERNS = re.compile(
    r"(confus|unclear|not sure|can'?t (find|see|tell)|don'?t (see|understand)|"
    r"unexpected|frustrat|annoying|hmm|strange|weird|where is|doesn'?t seem|"
    r"nothing happened|didn'?t work|still (on|showing)|no error|why)", re.I)


def _page(url: str) -> str:
    p = urlparse(url or "")
    return p.path or "/"


def heuristic_events(run_id: str, steps: list[dict]) -> list[dict]:
    events: list[dict] = []
    visited: list[str] = []
    recent_clicks: list[tuple[int, float, float]] = []
    wander_run: list[int] = []

    for s in steps:
        step_i = s["step"]
        action = s.get("action", {})
        kind = action.get("action", "")
        page = _page(s.get("url", ""))

        # -- backtrack: returning to a page previously left
        if not visited or visited[-1] != page:
            if page in visited[:-1] if visited else False:
                events.append(_ev(run_id, step_i, "backtrack", page,
                                  f"Returned to {page} after having moved past it."))
            visited.append(page)

        # -- action errors
        if s.get("error"):
            events.append(_ev(run_id, step_i, "action_error", page, s["error"]))

        # -- rage clicks: repeated clicks near the same point
        if kind in CLICK_ACTIONS and action.get("coordinate"):
            x, y = action["coordinate"]
            recent_clicks = [(i, cx, cy) for (i, cx, cy) in recent_clicks if step_i - i <= RAGE_COUNT + 1]
            recent_clicks.append((step_i, float(x), float(y)))
            close = [c for c in recent_clicks
                     if math.dist((c[1], c[2]), (float(x), float(y))) <= RAGE_RADIUS_PX]
            if len(close) >= RAGE_COUNT:
                events.append(_ev(run_id, step_i, "rage_click", page,
                                  f"{len(close)} clicks within {RAGE_RADIUS_PX}px around ({int(x)}, {int(y)})."))
                recent_clicks = []
        elif kind in {"type", "key"}:
            recent_clicks = []

        # -- wandering: long streak of passive actions (looking for something)
        if kind in PASSIVE_ACTIONS:
            wander_run.append(step_i)
            if len(wander_run) == WANDER_LEN:
                events.append(_ev(run_id, step_i, "wandering", page,
                                  f"{WANDER_LEN}+ consecutive scroll/look actions without interacting."))
        elif kind not in {"initial_state"}:
            wander_run = []

        # -- hesitation: unusually long deliberation before acting
        if (s.get("model_latency_s") or 0) >= HESITATION_LATENCY_S:
            events.append(_ev(run_id, step_i, "hesitation", page,
                              f"Deliberated {s['model_latency_s']:.0f}s before acting."))

        # -- self-reported confusion in the agent's commentary
        reasoning = s.get("reasoning") or ""
        m = CONFUSION_PATTERNS.search(reasoning)
        if m:
            snippet = _snippet(reasoning, m.start())
            events.append(_ev(run_id, step_i, "confusion", page, f'"{snippet}"'))

    return events


def _ev(run_id: str, step: int, kind: str, page: str, detail: str) -> dict:
    return {"run": run_id, "step": step, "type": kind, "page": page, "detail": detail}


def _snippet(text: str, pos: int, width: int = 140) -> str:
    start = max(0, pos - 40)
    s = text[start:start + width].strip().replace("\n", " ")
    return ("…" if start > 0 else "") + s + ("…" if start + width < len(text) else "")


# -- optional LLM labeling ------------------------------------------------

LABEL_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "step": {"type": "integer"},
                    "type": {"type": "string",
                             "enum": ["confusion", "hesitation", "backtrack", "dead_end",
                                      "unclear_copy", "hidden_affordance", "error_recovery", "other"]},
                    "ui_element": {"type": "string"},
                    "evidence": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
                "required": ["step", "type", "ui_element", "evidence", "suggestion"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["events"],
    "additionalProperties": False,
}


def llm_label_run(run_id: str, steps: list[dict], task: str, model: str) -> Optional[list[dict]]:
    """Ask the model to read the run's own commentary and label friction moments.
    Returns None when the API is unavailable."""
    try:
        import anthropic
        client = anthropic.Anthropic()

        lines = []
        for s in steps:
            action = s.get("action", {})
            desc = action.get("action", "?")
            if action.get("coordinate"):
                desc += f" @{tuple(action['coordinate'])}"
            if action.get("text"):
                desc += f" text={action['text']!r}"
            lines.append(f"[step {s['step']}] page={_page(s.get('url', ''))} action={desc} "
                         f"deliberation={s.get('model_latency_s', 0):.0f}s"
                         + (f" ERROR={s['error']}" if s.get("error") else ""))
            if s.get("reasoning"):
                lines.append(f"    user said: {s['reasoning'][:500]}")

        prompt = (
            "Below is the trace of one synthetic user attempting a task in a usability test. "
            "Identify moments of UX friction: confusion, hesitation, dead ends, unclear copy, "
            "hidden affordances, error recovery. Only report moments with concrete evidence in "
            "the trace, quote that evidence, name the UI element involved, and suggest a fix.\n\n"
            f"Task: {task}\n\nTrace:\n" + "\n".join(lines)
        )
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            output_config={"format": {"type": "json_schema", "schema": LABEL_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            return None
        text = next(b.text for b in response.content if b.type == "text")
        events = json.loads(text)["events"]
        for e in events:
            e["run"] = run_id
            e["source"] = "llm"
        return events
    except Exception:
        return None


# -- batch clustering -------------------------------------------------------

def cluster_events(all_events: list[dict]) -> list[dict]:
    """Group friction events into report findings. Deterministic fallback:
    group by (type, page)."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for e in all_events:
        groups[(e["type"], e.get("page", "?"))].append(e)

    clusters = []
    for (kind, page), events in groups.items():
        runs = sorted({e["run"] for e in events})
        clusters.append({
            "title": f"{kind.replace('_', ' ').title()} on {page}",
            "type": kind,
            "page": page,
            "count": len(events),
            "runs_affected": runs,
            "examples": events[:5],
        })
    clusters.sort(key=lambda c: (-len(c["runs_affected"]), -c["count"]))
    return clusters
