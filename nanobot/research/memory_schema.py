"""Structured research memory objects with frontmatter serialization."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

IdeaStatus = Literal["candidate", "shortlisted", "rejected", "parked"]
DecisionOutcome = Literal["shortlist", "reject", "park", "next-step"]

_FRONTMATTER_RE = re.compile(r"^---\n(?P<fm>.*?)\n---\n(?P<body>.*)", re.DOTALL)
_JSON_BLOCK_RE = re.compile(r"```json\s*(?P<payload>[\s\S]*?)\s*```", re.IGNORECASE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from markdown text. Returns (metadata, body)."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    meta = yaml.safe_load(match.group("fm")) or {}
    return meta, match.group("body").strip()


def _dump_frontmatter(metadata: dict[str, Any], body: str) -> str:
    """Serialize metadata as YAML frontmatter + markdown body."""
    fm = yaml.safe_dump(metadata, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{fm}\n---\n{body}\n"


class ResearchModel(BaseModel):
    """Base model for research cards with frontmatter serialization."""

    model_config = ConfigDict(extra="forbid")

    updated_at: str = Field(default_factory=_now_iso)

    def to_json(self) -> str:
        """Render the object as pretty JSON for storage or debugging."""
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        """Render the object as a markdown card with YAML frontmatter."""
        return self.to_frontmatter()

    def to_frontmatter(self) -> str:
        """Render the object as YAML frontmatter + a markdown body section."""
        data = self.model_dump(mode="json")
        title = data.pop("title", self.__class__.__name__)
        notes = data.pop("notes", "")
        data["updated_at"] = data.get("updated_at") or _now_iso()
        body_parts = [f"# {title}"]
        if notes:
            body_parts.extend(["", notes])
        return _dump_frontmatter(data, "\n".join(body_parts))

    @classmethod
    def from_frontmatter(cls, text: str) -> "ResearchModel":
        """Parse a frontmatter-backed markdown card into its model."""
        meta, body = _parse_frontmatter(text)
        if meta:
            if "title" not in meta:
                for line in body.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("# "):
                        meta["title"] = stripped[2:].strip()
                        break
            notes_lines = [line for line in body.splitlines() if not line.strip().startswith("# ")]
            if notes_lines and "notes" not in meta:
                meta["notes"] = "\n".join(notes_lines).strip()
            return cls.model_validate(meta)
        # Fallback: try legacy JSON block format
        jmatch = _JSON_BLOCK_RE.search(text)
        if jmatch:
            payload = json.loads(jmatch.group("payload"))
            return cls.model_validate(payload)
        raise ValueError("No frontmatter or JSON block found in card text")


class ProblemCard(ResearchModel):
    """Research topic framing card."""

    card_type: Literal["problem"] = "problem"
    card_id: str = "problem"
    title: str = "Untitled research topic"
    topic: str = ""
    objective: str = ""
    constraints: list[str] = Field(default_factory=list)
    baselines: list[str] = Field(default_factory=list)
    evaluation_targets: list[str] = Field(default_factory=list)
    user_preferences: list[str] = Field(default_factory=list)
    preferred_idea_ids: list[str] = Field(default_factory=list)
    excluded_directions: list[str] = Field(default_factory=list)
    checkpoint_reached: str = ""
    notes: str = ""


class PaperCard(ResearchModel):
    """Literature note card."""

    card_type: Literal["paper"] = "paper"
    card_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str = ""
    url: str = ""
    task: str = ""
    method_family: str = ""
    core_mechanism: str = ""
    contributions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    source_queries: list[str] = Field(default_factory=list)
    claimed_contribution: str = ""
    possible_reuse: str = ""
    relevance_score: str = ""
    evidence_confidence: str = ""
    cluster: str = ""
    notes: str = ""


class GapCard(ResearchModel):
    """Structured research gap candidate."""

    card_type: Literal["gap"] = "gap"
    card_id: str
    title: str
    description: str
    evidence_paper_ids: list[str] = Field(default_factory=list)
    related_baselines: list[str] = Field(default_factory=list)
    research_value: str = ""
    main_risk: str = ""
    gap_type: str = ""
    evidence_confidence: str = ""
    notes: str = ""


class IdeaCard(ResearchModel):
    """Candidate innovation idea."""

    card_type: Literal["idea"] = "idea"
    card_id: str
    title: str
    target_gap_ids: list[str] = Field(default_factory=list)
    one_sentence_pitch: str
    core_mechanism: str = ""
    difference_from_baseline: str = ""
    difference_from_prior_work: str = ""
    expected_value: str = ""
    novelty: str = ""
    feasibility: str = ""
    main_risk: str = ""
    validation_hint: str = ""
    why_now: str = ""
    closest_prior_work: str = ""
    status: IdeaStatus = "candidate"
    notes: str = ""


class DecisionCard(ResearchModel):
    """Decision log for shortlist, rejection, or next-step guidance."""

    card_type: Literal["decision"] = "decision"
    card_id: str
    title: str
    outcome: DecisionOutcome
    idea_ids: list[str] = Field(default_factory=list)
    rationale: str
    next_steps: list[str] = Field(default_factory=list)
    question: str = ""
    winner: str = ""
    why_winner: str = ""
    why_not_others: list[str] = Field(default_factory=list)
    next_action: str = ""
    notes: str = ""
