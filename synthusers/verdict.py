"""Two-tier success classification.

Tier 1 — hard assertions (URL regex / page text): deterministic, trustworthy,
used whenever the spec defines them.
Tier 2 — LLM judge: looks at the final screenshots + the agent's own final
message and classifies completion. Used as fallback and as a cross-check.
"""

from __future__ import annotations

import base64
import json
import pathlib
import re
from typing import Optional

from .config import Spec

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "completed": {"type": "boolean"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "reason": {"type": "string"},
        "failure_point": {
            "type": ["string", "null"],
            "description": "Where in the flow the user failed or got stuck, if they did.",
        },
    },
    "required": ["completed", "confidence", "reason", "failure_point"],
    "additionalProperties": False,
}


def assertion_verdict(spec: Spec, final_url: str, final_page_text: str) -> Optional[dict]:
    """Returns {passed, checks} or None when the spec defines no assertions."""
    checks = {}
    if spec.success.url_matches:
        checks["url_matches"] = bool(re.search(spec.success.url_matches, final_url or ""))
    if spec.success.page_text:
        checks["page_text"] = spec.success.page_text.lower() in (final_page_text or "").lower()
    if not checks:
        return None
    return {"passed": all(checks.values()), "checks": checks}


def judge_verdict(spec: Spec, run_dir: pathlib.Path, meta: dict, steps: list[dict]) -> Optional[dict]:
    """LLM judge over the final state. Returns None when the API is unavailable."""
    try:
        import anthropic
        client = anthropic.Anthropic()

        content: list[dict] = [{
            "type": "text",
            "text": (
                "You are grading a usability-test run performed by a synthetic user.\n\n"
                f"Task given to the user:\n{spec.task}\n\n"
                f"Success criteria: {spec.success.url_matches or ''} {spec.success.page_text or ''}\n\n"
                f"The user's final message: {meta.get('final_text') or '(none)'}\n"
                f"Final URL: {meta.get('final_url')}\n"
                f"Run ended because: {meta.get('stop_reason')}\n\n"
                "Below are the last screenshots of the session. Decide whether the task was "
                "actually completed (do not take the user's word for it) and, if not, where they failed."
            ),
        }]
        shots = [s for s in steps if s.get("screenshot")][-3:]
        for s in shots:
            png = (run_dir / s["screenshot"]).read_bytes()
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": base64.b64encode(png).decode()}})

        model = spec.agent.judge_model or spec.agent.model
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            output_config={"format": {"type": "json_schema", "schema": JUDGE_SCHEMA}},
            messages=[{"role": "user", "content": content}],
        )
        if response.stop_reason == "refusal":
            return None
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)
    except Exception as e:
        return {"error": str(e)} if _is_hard_error(e) else None


def _is_hard_error(e: Exception) -> bool:
    # Auth/missing-key errors mean "judge unavailable" (None); anything else we
    # surface so a misconfigured judge doesn't silently disappear.
    name = type(e).__name__
    return name not in {"AuthenticationError", "PermissionDeniedError", "TypeError"} and "api_key" not in str(e).lower()


def combine(assertion: Optional[dict], judge: Optional[dict], stop_reason: str) -> dict:
    """Final verdict. Hard assertions win; the judge fills in when there are none."""
    completed: Optional[bool] = None
    source = "none"
    if assertion is not None:
        completed = assertion["passed"]
        source = "assertion"
    elif judge and "completed" in judge:
        completed = judge["completed"]
        source = "judge"

    disagreement = None
    if assertion is not None and judge and "completed" in judge:
        if assertion["passed"] != judge["completed"]:
            disagreement = {"assertion": assertion["passed"], "judge": judge["completed"]}

    return {
        "completed": completed,
        "source": source,
        "assertion": assertion,
        "judge": judge,
        "disagreement": disagreement,
        "gave_up": stop_reason == "agent_gave_up",
    }
