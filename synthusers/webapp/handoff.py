"""The agent-ready handoff document: accepted findings as implementation
instructions a coding agent can act on directly.

Assembled deterministically from metrics.json + review.json — the LLM work
(labeling, verification, recommendations) already happened upstream, so
nothing new can be invented here.
"""

from __future__ import annotations

import pathlib

from . import state

PRIORITY_LABEL = {"high": "HIGH — fix first", "medium": "MEDIUM", "low": "LOW — polish"}


def _included(clusters: list[dict], decisions: dict) -> tuple[list[dict], list[str]]:
    """Accepted findings; untriaged ones ride along unless auto-refuted.
    Returns (clusters, notes-for-header)."""
    picked, notes = [], []
    untriaged = 0
    for c in clusters:
        decision = decisions.get(c.get("id"))
        refuted = (c.get("verification") or {}).get("verdict") == "refuted"
        if decision == "rejected":
            continue
        if decision == "accepted":
            picked.append(c)
            continue
        if refuted:
            continue          # untriaged + auto-refuted stays out
        picked.append(c)
        untriaged += 1
    if untriaged:
        notes.append(f"{untriaged} of {len(picked)} issues below are unreviewed "
                     "by a human (auto-verified only).")
    return picked, notes


def build(batch_dir: pathlib.Path) -> str | None:
    metrics = state.load_metrics(batch_dir)
    if not metrics:
        return None
    decisions = state.load_review(batch_dir)
    clusters, notes = _included(metrics.get("friction_clusters", []), decisions)

    persona_of = {r["run_id"]: r.get("persona") for r in metrics.get("runs", [])}
    n_runs = metrics.get("n_runs", 0)
    rate = metrics.get("success_rate")

    lines = [
        f"# UX fixes for {metrics.get('target_url', 'the product')}",
        "",
        f"Source: SynthUsers usability batch `{batch_dir.name}` — {n_runs} synthetic "
        f"user(s) attempted the task below; success rate "
        f"{'—' if rate is None else f'{round(rate * 100)}%'}. Findings were "
        "auto-verified against session evidence and human-triaged.",
        "",
        f'Task tested: "{metrics.get("task", "").strip()}"',
        "",
        "## Instructions",
        "",
        "You are a coding agent implementing UX fixes in this product's codebase. "
        "For each issue below: locate the page/element described, apply the fix, "
        "and check it against the acceptance criterion. The evidence quotes are "
        "verbatim think-aloud commentary from recorded user sessions — treat them "
        "as the ground truth for what users experienced. Do not change unrelated "
        "behavior; prefer the smallest change that resolves the issue.",
        "",
    ]
    for note in notes:
        lines += [f"> Note: {note}", ""]

    if not clusters:
        lines += ["_No accepted findings — nothing to fix._", ""]
        return "\n".join(lines)

    for n, c in enumerate(clusters, 1):
        affected = len(c.get("runs_affected", []))
        verdict = (c.get("verification") or {}).get("verdict")
        decision = decisions.get(c.get("id"))
        status_bits = [PRIORITY_LABEL.get(c.get("priority"), c.get("priority", ""))]
        status_bits.append(f"hit by {affected} of {n_runs} user(s)")
        if verdict:
            status_bits.append(f"auto-verification: {verdict}")
        if decision == "accepted":
            status_bits.append("human-accepted")
        lines += [f"## {n}. {c.get('title', 'Finding')}",
                  "",
                  f"**{' · '.join(status_bits)}**",
                  "",
                  f"- **Where:** `{c.get('page', '?')}`"
                  + (f" — {c['examples'][0]['ui_element']}"
                     if c.get("examples") and c["examples"][0].get("ui_element") else ""),
                  ]
        if c.get("merged_from"):
            lines.append(f"- **Also reported as:** {'; '.join(c['merged_from'])}")
        lines.append("- **What users experienced:**")
        for ex in c.get("examples", [])[:4]:
            quote = (ex.get("evidence") or ex.get("detail") or "").strip()
            if not quote:
                continue
            who = persona_of.get(ex.get("run")) or ex.get("run") or "user"
            lines.append(f'  - {who}, step {ex.get("step")}: "{quote}"')
        if c.get("recommendation"):
            label = "Fix (verified)" if c.get("verification") else "Fix"
            lines += [f"- **{label}:** {c['recommendation']}"]
        else:
            lines += ["- **Fix:** none prescribed — the evidence was not "
                      "conclusive enough to verify a specific change. Reproduce "
                      "the moment described above first; only implement a change "
                      "you can justify from that evidence."]
        lines += [
            f"- **Acceptance criterion:** re-running the same task, a user on "
            f"`{c.get('page', 'the affected page')}` completes this moment without "
            f"{c.get('type', 'friction').replace('_', ' ')}; the change is visible "
            "in the UI without breaking the existing flow.",
            "",
        ]

    lines += [
        "## After implementing",
        "",
        "Re-run the same batch (same URL + task) in SynthUsers and compare: the "
        "issues above should not recur, and the success rate should hold or "
        "improve.",
        "",
    ]
    return "\n".join(lines)
