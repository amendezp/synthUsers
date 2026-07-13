"""Run spec loading: one YAML file describes a batch of synthetic-user runs."""

from __future__ import annotations

import dataclasses
import pathlib
from typing import Any, Optional

import yaml

DEFAULT_MODEL = "claude-opus-4-8"
COMPUTER_USE_BETA = "computer-use-2025-11-24"


@dataclasses.dataclass
class LocalApp:
    command: str
    ready_url: str
    cwd: Optional[str] = None
    startup_timeout_s: float = 15.0


@dataclasses.dataclass
class Target:
    url: str
    local_app: Optional[LocalApp] = None


@dataclasses.dataclass
class Success:
    url_matches: Optional[str] = None   # regex against the final URL
    page_text: Optional[str] = None     # substring expected on the final page
    judge: bool = True                  # run the LLM judge as well


@dataclasses.dataclass
class Persona:
    name: str
    prompt: str


@dataclasses.dataclass
class AgentConfig:
    driver: str = "computer_use"        # "computer_use" | "scripted"
    model: str = DEFAULT_MODEL
    judge_model: Optional[str] = None   # defaults to `model`
    effort: str = "high"
    max_steps: int = 40
    max_minutes: float = 12.0
    keep_last_images: Optional[int] = None  # None = keep full screenshot history
    script: Optional[list] = None       # for driver=scripted: list of action dicts


@dataclasses.dataclass
class Spec:
    name: str
    target: Target
    task: str
    success: Success
    runs: int = 1
    parallel: int = 1
    agent: AgentConfig = dataclasses.field(default_factory=AgentConfig)
    viewport: dict = dataclasses.field(default_factory=lambda: {"width": 1280, "height": 800})
    personas: list[Persona] = dataclasses.field(default_factory=list)
    output_dir: str = "runs"
    path: Optional[pathlib.Path] = None

    def persona_for_run(self, index: int) -> Optional[Persona]:
        if not self.personas:
            return None
        return self.personas[index % len(self.personas)]


def spec_to_dict(spec: Spec) -> dict:
    """Round-trippable dict of a Spec, for freezing programmatically built
    specs (no source YAML file) into a batch dir."""
    d: dict[str, Any] = {
        "name": spec.name,
        "target": {"url": spec.target.url},
        "task": spec.task,
        "success": {
            "url_matches": spec.success.url_matches,
            "page_text": spec.success.page_text,
            "judge": spec.success.judge,
        },
        "runs": spec.runs,
        "parallel": spec.parallel,
        "agent": {
            "driver": spec.agent.driver,
            "model": spec.agent.model,
            "effort": spec.agent.effort,
            "max_steps": spec.agent.max_steps,
            "max_minutes": spec.agent.max_minutes,
        },
        "viewport": spec.viewport,
        "output_dir": spec.output_dir,
    }
    if spec.target.local_app:
        d["target"]["local_app"] = dataclasses.asdict(spec.target.local_app)
    if spec.personas:
        d["personas"] = [{"name": p.name, "prompt": p.prompt} for p in spec.personas]
    return d


def _pick(d: dict, cls) -> dict:
    fields = {f.name for f in dataclasses.fields(cls)}
    unknown = set(d) - fields
    if unknown:
        raise ValueError(f"Unknown keys for {cls.__name__}: {sorted(unknown)}")
    return d


def load_spec(path: str | pathlib.Path) -> Spec:
    path = pathlib.Path(path)
    raw: dict[str, Any] = yaml.safe_load(path.read_text())

    target_raw = dict(raw["target"])
    local_app = None
    if target_raw.get("local_app"):
        local_app = LocalApp(**_pick(dict(target_raw["local_app"]), LocalApp))
    target = Target(url=target_raw["url"], local_app=local_app)

    success = Success(**_pick(dict(raw.get("success", {})), Success))
    agent = AgentConfig(**_pick(dict(raw.get("agent", {})), AgentConfig))
    personas = [Persona(**p) for p in raw.get("personas", [])]

    return Spec(
        name=raw["name"],
        target=target,
        task=raw["task"].strip(),
        success=success,
        runs=int(raw.get("runs", 1)),
        parallel=int(raw.get("parallel", 1)),
        agent=agent,
        viewport=dict(raw.get("viewport", {"width": 1280, "height": 800})),
        personas=personas,
        output_dir=raw.get("output_dir", "runs"),
        path=path,
    )
