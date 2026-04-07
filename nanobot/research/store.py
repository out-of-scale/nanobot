"""Persistence helpers for research cards and artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from nanobot.research.artifacts import ARTIFACT_DIRECTORIES
from nanobot.research.memory_schema import (
    DecisionCard,
    GapCard,
    IdeaCard,
    PaperCard,
    ProblemCard,
    ResearchModel,
    _parse_frontmatter,
)
from nanobot.utils.helpers import ensure_dir, safe_filename

CardKind = Literal["problem", "paper", "gap", "idea", "decision"]
ArtifactKind = Literal["framing_report", "literature_map", "gap_report", "idea_candidates", "idea_shortlist", "idea_brief", "novelty_audit"]

CARD_MODELS: dict[CardKind, type[ResearchModel]] = {
    "problem": ProblemCard,
    "paper": PaperCard,
    "gap": GapCard,
    "idea": IdeaCard,
    "decision": DecisionCard,
}

CARD_DIRECTORIES: dict[CardKind, str] = {
    "problem": "",
    "paper": "memory/papers",
    "gap": "memory/gaps",
    "idea": "memory/ideas",
    "decision": "memory/decisions",
}

_JSON_BLOCK_RE = re.compile(r"```json\s*(?P<payload>[\s\S]*?)\s*```", re.IGNORECASE)


class ResearchStore:
    """Save structured research outputs inside a workspace."""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    def save_card(self, kind: CardKind, data: dict[str, Any], filename: str | None = None) -> Path:
        """Validate and save a research card as frontmatter markdown."""
        model_cls = CARD_MODELS[kind]
        card = model_cls.model_validate(data)
        if kind == "problem":
            target = self.workspace / "problem.md"
        else:
            directory = ensure_dir(self.workspace / CARD_DIRECTORIES[kind])
            stem = filename or getattr(card, "card_id", None) or getattr(card, "title", kind)
            target = directory / f"{safe_filename(str(stem))}.md"

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(card.to_frontmatter(), encoding="utf-8")
        return target

    def load_problem(self) -> ProblemCard | None:
        """Load the current problem card if it exists."""
        path = self.workspace / "problem.md"
        if not path.exists():
            return None
        return self._load_from_path(ProblemCard, path)

    def load_cards(self, kind: CardKind) -> list[ResearchModel]:
        """Load all cards of one kind from the workspace."""
        if kind == "problem":
            problem = self.load_problem()
            return [problem] if problem else []

        directory = self.workspace / CARD_DIRECTORIES[kind]
        if not directory.exists():
            return []

        model_cls = CARD_MODELS[kind]
        cards: list[tuple[str, ResearchModel]] = []
        for path in sorted(directory.glob("*.md")):
            if path.name.upper() == "README.MD":
                continue
            try:
                cards.append((path.stem, self._load_from_path(model_cls, path)))
            except ValueError:
                continue
        return [card for _, card in sorted(cards, key=lambda item: item[0])]

    def load_card(self, kind: CardKind, card_id: str) -> ResearchModel | None:
        """Load a single card by kind and id/path stem."""
        if kind == "problem":
            return self.load_problem()

        directory = self.workspace / CARD_DIRECTORIES[kind]
        path = directory / f"{safe_filename(card_id)}.md"
        if not path.exists():
            return None
        return self._load_from_path(CARD_MODELS[kind], path)

    def search_cards(
        self,
        query: str,
        *,
        kinds: tuple[CardKind, ...] = ("problem", "paper", "gap", "idea", "decision"),
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search cards by simple case-insensitive text matching."""
        needle = query.strip().lower()
        if not needle:
            return []

        matches: list[dict[str, Any]] = []
        for kind in kinds:
            for card in self.load_cards(kind):
                payload = card.model_dump(mode="json")
                haystack = json.dumps(payload, ensure_ascii=False).lower()
                if needle not in haystack:
                    continue
                matches.append(
                    {
                        "kind": kind,
                        "card_id": payload.get("card_id", "problem"),
                        "title": payload.get("title", kind),
                        "path": str(self._card_path(kind, payload)),
                    }
                )
                if len(matches) >= limit:
                    return matches
        return matches

    def list_recent_cards(
        self,
        *,
        kinds: tuple[CardKind, ...] = ("paper", "gap", "idea", "decision"),
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """List the most recently modified cards across selected kinds."""
        items: list[tuple[float, dict[str, Any]]] = []
        problem = self.load_problem()
        if problem and "problem" in kinds:
            path = self.workspace / "problem.md"
            items.append(
                (
                    path.stat().st_mtime,
                    {
                        "kind": "problem",
                        "card_id": "problem",
                        "title": problem.title,
                        "path": str(path),
                    },
                )
            )
        for kind in kinds:
            if kind == "problem":
                continue
            directory = self.workspace / CARD_DIRECTORIES[kind]
            if not directory.exists():
                continue
            for path in directory.glob("*.md"):
                if path.name.upper() == "README.MD":
                    continue
                try:
                    card = self._load_from_path(CARD_MODELS[kind], path)
                except ValueError:
                    continue
                payload = card.model_dump(mode="json")
                items.append(
                    (
                        path.stat().st_mtime,
                        {
                            "kind": kind,
                            "card_id": payload.get("card_id", path.stem),
                            "title": payload.get("title", kind),
                            "path": str(path),
                        },
                    )
                )
        return [item for _, item in sorted(items, key=lambda item: item[0], reverse=True)[:limit]]

    def save_artifact(
        self,
        kind: ArtifactKind,
        title: str,
        content: str,
        filename: str | None = None,
    ) -> Path:
        """Save a generated artifact as markdown."""
        directory = ensure_dir(self.workspace / ARTIFACT_DIRECTORIES[kind])
        stem = filename or title or kind
        target = directory / f"{safe_filename(stem)}.md"
        body = f"# {title or kind}\n\n{content.strip()}\n"
        target.write_text(body, encoding="utf-8")
        return target

    def list_artifacts(self, kind: ArtifactKind | None = None, *, limit: int = 10) -> list[dict[str, Any]]:
        """List saved research artifacts, newest first."""
        kinds = [kind] if kind else list(ARTIFACT_DIRECTORIES.keys())
        items: list[tuple[float, dict[str, Any]]] = []
        for artifact_kind in kinds:
            directory = self.workspace / ARTIFACT_DIRECTORIES[artifact_kind]
            if not directory.exists():
                continue
            for path in directory.glob("*.md"):
                if path.name.upper() == "README.MD":
                    continue
                items.append(
                    (
                        path.stat().st_mtime,
                        {
                            "kind": artifact_kind,
                            "title": path.stem,
                            "path": str(path),
                        },
                    )
                )
        return [item for _, item in sorted(items, key=lambda item: item[0], reverse=True)[:limit]]

    def read_artifact(self, *, kind: ArtifactKind, name: str | None = None, path: str | None = None) -> dict[str, Any] | None:
        """Read one artifact by explicit path or by file stem within one bucket."""
        if path:
            target = Path(path)
        elif name:
            target = self.workspace / ARTIFACT_DIRECTORIES[kind] / f"{safe_filename(name)}.md"
        else:
            return None
        if not target.exists() or not target.is_file():
            return None
        return {
            "kind": kind,
            "path": str(target),
            "content": target.read_text(encoding="utf-8"),
        }

    def validate_links(self, card: ResearchModel) -> list[str]:
        """Check that all ID references in a card resolve to existing saved cards.

        Returns a list of warning strings. Empty list means all references are valid.
        Saves still proceed even when warnings are returned — this is non-blocking.
        """
        warnings: list[str] = []
        if isinstance(card, GapCard):
            for pid in card.evidence_paper_ids:
                if not (self.workspace / CARD_DIRECTORIES["paper"] / f"{safe_filename(pid)}.md").exists():
                    warnings.append(f"gap '{card.card_id}': evidence_paper_id '{pid}' does not resolve to a saved paper card")
        elif isinstance(card, IdeaCard):
            for gid in card.target_gap_ids:
                if not (self.workspace / CARD_DIRECTORIES["gap"] / f"{safe_filename(gid)}.md").exists():
                    warnings.append(f"idea '{card.card_id}': target_gap_id '{gid}' does not resolve to a saved gap card")
        elif isinstance(card, DecisionCard):
            for iid in card.idea_ids:
                if not (self.workspace / CARD_DIRECTORIES["idea"] / f"{safe_filename(iid)}.md").exists():
                    warnings.append(f"decision '{card.card_id}': idea_id '{iid}' does not resolve to a saved idea card")
        return warnings

    def check_graph_integrity(self) -> dict[str, list[str]]:
        """Scan all cards and collect broken link warnings by card type."""
        result: dict[str, list[str]] = {}
        for kind in ("gap", "idea", "decision"):
            warnings: list[str] = []
            for card in self.load_cards(kind):  # type: ignore[arg-type]
                warnings.extend(self.validate_links(card))
            if warnings:
                result[kind] = warnings
        return result

    def migrate_card(self, path: Path) -> bool:
        """Migrate a single card from legacy JSON block format to frontmatter."""
        text = path.read_text(encoding="utf-8")
        meta, _ = _parse_frontmatter(text)
        if meta:
            return False  # Already frontmatter format

        jmatch = _JSON_BLOCK_RE.search(text)
        if not jmatch:
            return False
        try:
            payload = json.loads(jmatch.group("payload"))
        except json.JSONDecodeError:
            return False

        card_type = payload.get("card_type", "")
        model_cls = CARD_MODELS.get(card_type)
        if not model_cls:
            return False

        card = model_cls.model_validate(payload)
        path.write_text(card.to_frontmatter(), encoding="utf-8")
        return True

    def migrate_all(self) -> list[str]:
        """Migrate all legacy JSON block cards to frontmatter format."""
        migrated: list[str] = []
        # problem.md
        problem_path = self.workspace / "problem.md"
        if problem_path.exists() and self.migrate_card(problem_path):
            migrated.append(str(problem_path))
        # All card directories
        for kind, directory_rel in CARD_DIRECTORIES.items():
            if kind == "problem" or not directory_rel:
                continue
            directory = self.workspace / directory_rel
            if not directory.exists():
                continue
            for path in directory.glob("*.md"):
                if path.name.upper() == "README.MD":
                    continue
                if self.migrate_card(path):
                    migrated.append(str(path))
        return migrated

    def _load_from_path(self, model_cls: type[ResearchModel], path: Path) -> ResearchModel:
        """Parse one markdown-backed card into its model.

        Tries frontmatter first, then falls back to legacy JSON block.
        """
        text = path.read_text(encoding="utf-8")
        # Try frontmatter first
        meta, body = _parse_frontmatter(text)
        if meta:
            if "title" not in meta:
                for line in body.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("# "):
                        meta["title"] = stripped[2:].strip()
                        break
            notes_lines = [line for line in body.splitlines() if not line.strip().startswith("# ")]
            joined_notes = "\n".join(notes_lines).strip()
            if joined_notes and "notes" not in meta:
                meta["notes"] = joined_notes
            return model_cls.model_validate(meta)

        # Fallback: legacy JSON block
        jmatch = _JSON_BLOCK_RE.search(text)
        if not jmatch:
            raise ValueError(f"No frontmatter or JSON block found in {path}")
        try:
            payload = json.loads(jmatch.group("payload"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON block in {path}") from exc
        return model_cls.model_validate(payload)

    def _card_path(self, kind: CardKind, payload: dict[str, Any]) -> Path:
        """Resolve the expected path of one card payload."""
        if kind == "problem":
            return self.workspace / "problem.md"
        directory = self.workspace / CARD_DIRECTORIES[kind]
        stem = payload.get("card_id") or payload.get("title") or kind
        return directory / f"{safe_filename(str(stem))}.md"
