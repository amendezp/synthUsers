"""Persona generation for custom-URL launches.

Personas are the strongest variance lever the harness has — far more than
sampling parameters — so custom targets default to a generated cast: one
small LLM call tailors N diverse personas to the task and site. If the call
fails (no key, network, unparseable output) we fall back to a curated pool,
so a launch never dies because of persona generation.
"""

from __future__ import annotations

import json
import random
import re

from .config import Persona

GENERATION_MODEL = "claude-sonnet-5"

# Fallback cast: broad spread of age, tech skill, patience, and reading style.
CURATED = [
    Persona("busy-executive", "You are a 45-year-old executive between back-to-back "
            "meetings. You skim, click the most prominent thing, never read fine "
            "print, and abandon anything that takes more than a couple of minutes."),
    Persona("cautious-retiree", "You are a 68-year-old retiree who is careful with "
            "technology. You read every word before acting, distrust anything asking "
            "for personal data, and re-read error messages slowly. You persist, but "
            "unclear interfaces make you anxious."),
    Persona("distracted-parent", "You are a 34-year-old parent doing this on your "
            "phone-sized attention budget while a toddler shouts nearby. You lose "
            "your place, forget what you just read, and sometimes click the wrong "
            "thing before noticing."),
    Persona("skeptical-engineer", "You are a 29-year-old software engineer with high "
            "standards. You move fast, notice every piece of UX sloppiness out loud, "
            "and refuse to hand over data an app has no business asking for."),
    Persona("non-native-speaker", "You are a 41-year-old professional using the "
            "interface in your second language. Idioms, jargon and clever button "
            "labels confuse you; you prefer icons and plain words, and you re-read "
            "anything ambiguous."),
    Persona("bargain-hunter", "You are a 52-year-old deal-seeker. You are here only "
            "because something was free; the moment anything hints at payment, "
            "upsells or subscriptions, you look for the exit or the fine print."),
    Persona("first-time-novice", "You are a 19-year-old trying this kind of product "
            "for the first time. You don't know the conventions, hover before "
            "clicking, and follow whatever the interface literally says."),
    Persona("power-user-in-a-hurry", "You are a 26-year-old heavy app user with no "
            "patience for onboarding. You skip every tour, dismiss every modal, tab "
            "through forms, and get irritated by anything that slows you down."),
]

_PROMPT = """You are casting synthetic users for a usability test.

Site under test: {url}
Task they will attempt: {task}

Invent {n} distinct user personas likely to encounter this product. Make them
genuinely diverse across age, tech confidence, patience, reading style,
motivation, privacy sensitivity, and mood/context — the point is behavioral
variance. Keep each prompt 2-4 sentences, second person ("You are ..."),
concrete enough to change how someone clicks around.

Reply with ONLY a JSON array, no prose:
[{{"name": "kebab-case-label", "prompt": "You are ..."}}, ...]"""


def curated_sample(n: int) -> list[Persona]:
    return random.sample(CURATED, min(n, len(CURATED)))


def generate(task: str, url: str, n: int, model: str = GENERATION_MODEL) -> list[Persona]:
    """N personas tailored to the task, or a curated sample on any failure."""
    n = max(1, min(n, 8))
    try:
        import anthropic
        client = anthropic.Anthropic(timeout=25.0)
        response = client.messages.create(
            model=model,
            max_tokens=1500,
            messages=[{"role": "user",
                       "content": _PROMPT.format(url=url, task=task.strip(), n=n)}],
        )
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        match = re.search(r"\[.*\]", text, re.DOTALL)
        parsed = json.loads(match.group(0) if match else text)
        personas = []
        for i, item in enumerate(parsed[:n]):
            prompt = str(item.get("prompt", "")).strip()
            if not prompt:
                continue
            name = re.sub(r"[^a-z0-9-]+", "-", str(item.get("name", "")).lower()).strip("-")
            personas.append(Persona(name or f"generated-{i + 1}", prompt))
        if personas:
            return personas
    except Exception:
        pass
    return curated_sample(n)
