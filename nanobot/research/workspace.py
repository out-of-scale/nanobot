"""Workspace bootstrap for research mode."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from nanobot.research.artifacts import ARTIFACT_DIRECTORIES
from nanobot.research.memory_schema import ProblemCard
from nanobot.research.prompt_templates import RESEARCH_GUIDE, RESEARCH_SKILLS
from nanobot.utils.helpers import ensure_dir, sync_workspace_templates


RESEARCH_DIRECTORIES = [
    "memory/papers",
    "memory/gaps",
    "memory/ideas",
    "memory/decisions",
    *ARTIFACT_DIRECTORIES.values(),
]

RESEARCH_READMES = {
    "memory/papers/README.md": "# Paper Cards\n\nStore one paper note per markdown file.\n",
    "memory/gaps/README.md": "# Gap Cards\n\nStore each research gap as a separate markdown card.\n",
    "memory/ideas/README.md": "# Idea Cards\n\nStore each candidate innovation point as a separate markdown card.\n",
    "memory/decisions/README.md": "# Decision Cards\n\nRecord shortlist, reject, and next-step decisions here.\n",
    "artifacts/framing_report/README.md": "# Framing Report\n\nGenerated scout framing reports live here.\n",
    "artifacts/literature_map/README.md": "# Literature Map\n\nGenerated literature maps live here.\n",
    "artifacts/gap_report/README.md": "# Gap Report\n\nGenerated gap analyses live here.\n",
    "artifacts/idea_candidates/README.md": "# Idea Candidates\n\nGenerated idea candidate batches live here.\n",
    "artifacts/idea_shortlist/README.md": "# Idea Shortlist\n\nShortlisted directions live here.\n",
    "artifacts/idea_brief/README.md": "# Idea Brief\n\nFinal idea briefs live here.\n",
}

def _problem_template(topic: str | None) -> str:
    problem = ProblemCard(
        title=topic or "Untitled research topic",
        topic=topic or "",
    )
    return problem.to_frontmatter()

@dataclass
class ResearchInitResult:
    """Result of bootstrapping research mode into a workspace."""

    workspace: Path
    created_files: list[str] = field(default_factory=list)
    created_directories: list[str] = field(default_factory=list)


class ResearchWorkspaceService:
    """Create and maintain the minimum workspace layout for research mode."""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    def initialize(self, topic: str | None = None) -> ResearchInitResult:
        """Create missing research-mode files and directories."""
        ensure_dir(self.workspace)
        sync_workspace_templates(self.workspace, silent=True)

        result = ResearchInitResult(workspace=self.workspace)

        for relative in RESEARCH_DIRECTORIES:
            path = self.workspace / relative
            if not path.exists():
                ensure_dir(path)
                result.created_directories.append(relative)

        self._write_if_missing("problem.md", _problem_template(topic), result)
        self._write_if_missing("RESEARCH.md", RESEARCH_GUIDE, result)

        for relative, content in RESEARCH_READMES.items():
            self._write_if_missing(relative, content, result)

        for skill_name, content in RESEARCH_SKILLS.items():
            skill_dir = self.workspace / "skills" / skill_name
            if not skill_dir.exists():
                ensure_dir(skill_dir)
                result.created_directories.append(str(skill_dir.relative_to(self.workspace)))
            self._write_if_missing(f"skills/{skill_name}/SKILL.md", content, result)

        return result

    def _write_if_missing(self, relative_path: str, content: str, result: ResearchInitResult) -> None:
        path = self.workspace / relative_path
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        result.created_files.append(relative_path)
