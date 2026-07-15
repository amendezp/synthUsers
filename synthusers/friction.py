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


# -- verification pass ------------------------------------------------------

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "verdict": {"type": "string",
                                "enum": ["confirmed", "uncertain", "refuted"]},
                    "reason": {"type": "string"},
                    "duplicate_of": {"type": ["integer", "null"]},
                    "title": {"type": "string"},
                    "recommendation": {"type": "string"},
                },
                "required": ["index", "verdict", "reason", "duplicate_of",
                             "title", "recommendation"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["findings"],
    "additionalProperties": False,
}

VERIFY_PROMPT = """You are a skeptical UX-research reviewer doing quality control on the findings of a usability study before they reach the product team. The study: synthetic users attempted this task on {url}:

{task}

Below are the draft findings, each with its verbatim evidence (quotes from the users' think-aloud commentary) — followed by screenshots of the moments in question, in the same order.

For EVERY finding, judge it adversarially:
- verdict "confirmed": the quoted evidence (and screenshot, where given) genuinely supports the claim.
- verdict "uncertain": plausible, but the evidence is thin, one-off, or could be the persona's disposition rather than a flaw in the interface.
- verdict "refuted": the evidence does not support the claim, contradicts it, or describes normal expected behavior rather than friction.
- duplicate_of: if this finding describes the SAME underlying product issue as an earlier finding (even under a different friction type or page), give that finding's index; the earliest/strongest statement of the issue is the canonical one. Otherwise null.
- title: rewrite as one plain sentence naming the PRODUCT ISSUE (not the friction taxonomy), e.g. "The free-plan link is nearly invisible on the pricing page".
- recommendation: the single most useful fix, one or two sentences, merging the suggestions if the finding absorbs duplicates.

Findings:
{findings}"""


def _verify_findings_text(clusters: list[dict]) -> str:
    parts = []
    for i, c in enumerate(clusters):
        lines = [f"[{i}] type={c['type']} page={c['page']} "
                 f"users_affected={len(c.get('runs_affected', []))} events={c['count']}"]
        for ex in c.get("examples", [])[:4]:
            what = ex.get("evidence") or ex.get("detail") or ""
            lines.append(f'    - {ex.get("run")} step {ex.get("step")}'
                         + (f' [{ex["ui_element"]}]' if ex.get("ui_element") else "")
                         + f': "{what[:300]}"')
            if ex.get("suggestion"):
                lines.append(f"      suggested fix: {ex['suggestion'][:200]}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _cluster_shot(cluster: dict, batch_dir) -> Optional[bytes]:
    for ex in cluster.get("examples", []):
        run, step = ex.get("run"), ex.get("step")
        if run is None or step is None:
            continue
        for name in (f"{step:03d}_after.png", f"{step:03d}_annotated.png"):
            p = batch_dir / run / "shots" / name
            if p.exists():
                return p.read_bytes()
    return None


def review_clusters(clusters: list[dict], task: str, target_url: str,
                    batch_dir, model: str) -> Optional[list[dict]]:
    """Adversarial second pass over the batch findings: verify each cluster
    against its own evidence (+ a screenshot of the moment), refute what does
    not hold up, and merge findings that describe the same underlying issue —
    so the report is neither wrong nor repetitive. Returns the revised list,
    or None when the API is unavailable (callers keep the originals)."""
    if not clusters:
        return clusters
    try:
        import base64

        import anthropic
        client = anthropic.Anthropic()

        content: list[dict] = [{"type": "text", "text": VERIFY_PROMPT.format(
            url=target_url, task=task.strip(),
            findings=_verify_findings_text(clusters))}]
        for i, c in enumerate(clusters[:8]):   # cap image payload
            png = _cluster_shot(c, batch_dir)
            if png:
                content.append({"type": "text", "text": f"Screenshot for finding [{i}]:"})
                content.append({"type": "image", "source": {
                    "type": "base64", "media_type": "image/png",
                    "data": base64.b64encode(png).decode()}})

        response = client.messages.create(
            model=model,
            max_tokens=4096,
            output_config={"format": {"type": "json_schema", "schema": VERIFY_SCHEMA}},
            messages=[{"role": "user", "content": content}],
        )
        if response.stop_reason == "refusal":
            return None
        rows = {r["index"]: r
                for r in json.loads(next(b.text for b in response.content
                                         if b.type == "text"))["findings"]
                if 0 <= r.get("index", -1) < len(clusters)}

        # Resolve duplicate chains to a canonical, non-self target.
        def canon(i: int, seen=None) -> int:
            seen = seen or set()
            dup = rows.get(i, {}).get("duplicate_of")
            if dup is None or dup == i or dup in seen or dup not in rows:
                return i
            return canon(dup, seen | {i})

        merged: dict[int, dict] = {}
        for i, cluster in enumerate(clusters):
            row = rows.get(i)
            target = canon(i)
            if row and target != i:                      # fold into canonical
                dst = merged.get(target)
                if dst is not None:
                    dst["count"] += cluster["count"]
                    dst["runs_affected"] = sorted(set(dst["runs_affected"])
                                                  | set(cluster["runs_affected"]))
                    dst["examples"] = (dst["examples"] + cluster["examples"])[:6]
                    dst.setdefault("merged_from", []).append(cluster["title"])
                    continue
                # canonical not materialized yet (forward reference): fall through
                # and keep this cluster standalone rather than lose it.
            out = dict(cluster)
            if row:
                out["title"] = (row.get("title") or "").strip() or cluster["title"]
                out["recommendation"] = (row.get("recommendation") or "").strip() or None
                out["verification"] = {"verdict": row["verdict"],
                                       "reason": row.get("reason", "")}
            merged[i] = out
        return list(merged.values())
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
