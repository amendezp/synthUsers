"""Receivable inboxes for synthetic users.

Every run with an email address gets `su.{batch_id}.{run_id}@{domain}` — the
local part routes inbound mail to the right run, and the domain makes every
account a synthetic user created in the target app self-identifying (and
therefore cleanable later; see the per-batch accounts.json manifest).

Mail reaches a run's inbox (run_dir/inbox/NNN.json) through three doors:
  1. deliver(msg)      — direct, used by the dashboard's inbound webhook
                         (e.g. a Cloudflare Email Routing worker POSTing JSON)
  2. the spool         — any local process drops {to,subject,text,...} JSON
                         files into runs/_spool/ (the friction lab does this);
                         ingest_spool() routes them
  3. IMAP polling      — poll_imap() reads unseen mail from a catch-all
                         mailbox (SYNTHUSERS_IMAP_HOST/USER/PASS)

check() is the agent-facing read: ingest everything, then format the run's
messages (with extracted links and codes) for a tool result.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import time
from typing import Optional

SPOOL_DIR = pathlib.Path("runs/_spool")
UNROUTED_DIR = pathlib.Path("runs/_unrouted")
LOCAL_PART_RE = re.compile(r"^su\.(?P<batch>[A-Za-z0-9._-]+)\.(?P<run>run_\d+)$")
LINK_RE = re.compile(r"https?://[^\s\"'<>)\]]+")
CODE_RE = re.compile(r"(?<![\d.])(\d{4,8})(?![\d.])")


def make_address(batch_id: str, run_id: str, domain: str) -> str:
    return f"su.{batch_id}.{run_id}@{domain}"


def parse_address(address: str) -> Optional[tuple[str, str]]:
    """-> (batch_id, run_id) for synthetic addresses, else None."""
    local = (address or "").split("@")[0].strip().lower()
    m = LOCAL_PART_RE.match(local)
    if not m:
        return None
    return m.group("batch"), m.group("run")


def _resolve_run_dir(batch_id: str, run_id: str,
                     roots: tuple[str, ...] = ("runs",)) -> Optional[pathlib.Path]:
    for root in roots:
        candidate = pathlib.Path(root) / batch_id / run_id
        if candidate.is_dir():
            return candidate
    return None


def deliver(msg: dict, roots: tuple[str, ...] = ("runs",)) -> Optional[pathlib.Path]:
    """Route one message dict {to, from?, subject?, text?, html?} to its run's
    inbox. Unroutable mail is kept in runs/_unrouted rather than dropped."""
    msg = dict(msg)
    msg.setdefault("received_at", datetime.datetime.now(datetime.timezone.utc).isoformat())
    parsed = parse_address(str(msg.get("to") or ""))
    if parsed:
        run_dir = _resolve_run_dir(*parsed, roots=roots)
        dest_dir = (run_dir / "inbox") if run_dir else UNROUTED_DIR
    else:
        dest_dir = UNROUTED_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    seq = len(list(dest_dir.glob("*.json")))
    dest = dest_dir / f"{seq:03d}-{time.time_ns()}.json"
    dest.write_text(json.dumps(msg, indent=2))
    return dest if dest_dir != UNROUTED_DIR else None


def ingest_spool(roots: tuple[str, ...] = ("runs",)) -> int:
    """Route any spooled messages (written by local senders like the friction
    lab) into run inboxes."""
    if not SPOOL_DIR.is_dir():
        return 0
    routed = 0
    for path in sorted(SPOOL_DIR.glob("*.json")):
        try:
            msg = json.loads(path.read_text())
            path.unlink()
        except (OSError, json.JSONDecodeError):
            continue  # another ingester won the race, or a partial write
        deliver(msg, roots=roots)
        routed += 1
    return routed


def imap_configured() -> bool:
    return bool(os.environ.get("SYNTHUSERS_IMAP_HOST"))


def poll_imap(roots: tuple[str, ...] = ("runs",)) -> int:
    """Fetch unseen mail from the configured catch-all mailbox and route it.
    Uses only the stdlib; failures are non-fatal (returns 0)."""
    host = os.environ.get("SYNTHUSERS_IMAP_HOST")
    if not host:
        return 0
    import email
    import email.policy
    import imaplib
    try:
        conn = imaplib.IMAP4_SSL(host)
        conn.login(os.environ.get("SYNTHUSERS_IMAP_USER", ""),
                   os.environ.get("SYNTHUSERS_IMAP_PASS", ""))
        conn.select(os.environ.get("SYNTHUSERS_IMAP_FOLDER", "INBOX"))
        _, data = conn.search(None, "UNSEEN")
        routed = 0
        for num in (data[0] or b"").split():
            _, fetched = conn.fetch(num, "(RFC822)")
            if not fetched or fetched[0] is None:
                continue
            parsed = email.message_from_bytes(fetched[0][1], policy=email.policy.default)
            to = parsed.get("Delivered-To") or parsed.get("X-Original-To") or parsed.get("To", "")
            text_part = parsed.get_body(preferencelist=("plain",))
            html_part = parsed.get_body(preferencelist=("html",))
            deliver({
                "to": to,
                "from": parsed.get("From", ""),
                "subject": parsed.get("Subject", ""),
                "text": text_part.get_content() if text_part else "",
                "html": html_part.get_content() if html_part else "",
            }, roots=roots)
            routed += 1
        conn.logout()
        return routed
    except Exception:
        return 0


# -- reading ---------------------------------------------------------------


def read_inbox(run_dir: pathlib.Path) -> list[dict]:
    inbox_dir = pathlib.Path(run_dir) / "inbox"
    if not inbox_dir.is_dir():
        return []
    messages = []
    for path in sorted(inbox_dir.glob("*.json")):
        try:
            messages.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return messages


def _strip_html(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def extract_links(msg: dict) -> list[str]:
    seen: list[str] = []
    for source in (msg.get("text") or "", msg.get("html") or ""):
        for link in LINK_RE.findall(source):
            if link not in seen:
                seen.append(link)
    return seen


def extract_codes(msg: dict) -> list[str]:
    body = f"{msg.get('subject') or ''} {msg.get('text') or ''} {_strip_html(msg.get('html') or '')}"
    seen: list[str] = []
    for code in CODE_RE.findall(body):
        if code not in seen:
            seen.append(code)
    return seen


def check(address: str, run_dir: pathlib.Path, wait_s: float = 4.0) -> tuple[str, dict]:
    """Agent-facing inbox read: ingest all sources, then return
    (tool_result_text, trace_summary). Retries briefly — verification mail
    often lags the click that triggered it."""
    deadline = time.monotonic() + wait_s
    while True:
        ingest_spool()
        poll_imap()
        messages = read_inbox(run_dir)
        if messages or time.monotonic() > deadline:
            break
        time.sleep(1.0)

    if not messages:
        return (f"No emails for {address} yet. Delivery can lag by a few seconds — "
                "continue if the page allows it, or check again after your next action.",
                {"found": 0})

    lines = [f"Inbox for {address} — {len(messages)} message(s), newest first:"]
    for i, msg in enumerate(reversed(messages[-5:]), 1):
        links, codes = extract_links(msg), extract_codes(msg)
        body = (msg.get("text") or _strip_html(msg.get("html") or "")).strip()
        lines.append(f"\n[{i}] From: {msg.get('from') or '(unknown)'}"
                     f"\n    Subject: {msg.get('subject') or '(no subject)'}"
                     f"\n    Received: {msg.get('received_at', '')}"
                     f"\n    Body: {body[:600]}")
        if links:
            lines.append("    Links: " + " | ".join(links[:5]))
        if codes:
            lines.append("    Codes: " + ", ".join(codes[:5]))
    summary = {"found": len(messages),
               "subjects": [m.get("subject") or "" for m in messages[-3:]]}
    return "\n".join(lines), summary
