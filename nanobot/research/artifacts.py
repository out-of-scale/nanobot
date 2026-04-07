"""Artifact definitions for research mode."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

ArtifactKind = Literal[
    "framing_report",
    "literature_map",
    "gap_report",
    "idea_candidates",
    "idea_shortlist",
    "idea_brief",
    "novelty_audit",
]

ARTIFACT_DIRECTORIES: dict[ArtifactKind, str] = {
    "framing_report": "artifacts/framing_report",
    "literature_map": "artifacts/literature_map",
    "gap_report": "artifacts/gap_report",
    "idea_candidates": "artifacts/idea_candidates",
    "idea_shortlist": "artifacts/idea_shortlist",
    "idea_brief": "artifacts/idea_brief",
    "novelty_audit": "artifacts/novelty_audit",
}


def get_artifact_dir(workspace: Path, kind: ArtifactKind) -> Path:
    """Resolve the directory used to store one artifact kind."""
    return workspace / ARTIFACT_DIRECTORIES[kind]
