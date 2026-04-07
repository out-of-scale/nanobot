"""Bounded scout pass for research-mode literature framing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from nanobot.agent.tools.research import LiteratureSearchTool, PaperDigestTool
from nanobot.research.literature import build_paper_card
from nanobot.research.memory_schema import ProblemCard
from nanobot.research.store import ResearchStore
from nanobot.research.workspace import ResearchWorkspaceService


@dataclass
class ScoutInputs:
    """Inputs for one bounded scout pass."""

    topic: str
    objective: str = ""
    dataset: str = ""
    metric: str = ""
    focus_terms: list[str] = field(default_factory=list)
    baselines: list[str] = field(default_factory=list)
    max_queries: int = 4
    count_per_query: int = 5
    digest_limit: int = 5


@dataclass
class ScoutRunResult:
    """Summary of one scout pass."""

    workspace: Path
    query_expansions: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    saved_cards: list[str] = field(default_factory=list)
    saved_artifacts: list[str] = field(default_factory=list)
    next_anchor: str = "gap-finder"


class ScoutService:
    """Run one DeepScientist-inspired scout pass inside a nanobot research workspace."""

    def __init__(
        self,
        workspace: Path,
        *,
        api_key: str | None = None,
        proxy: str | None = None,
        literature_tool: LiteratureSearchTool | None = None,
        digest_tool: PaperDigestTool | None = None,
    ):
        self.workspace = workspace
        self.workspace_service = ResearchWorkspaceService(workspace)
        self.store = ResearchStore(workspace)
        self.literature_tool = literature_tool or LiteratureSearchTool(api_key=api_key, proxy=proxy)
        self.digest_tool = digest_tool or PaperDigestTool(proxy=proxy)

    async def run(self, inputs: ScoutInputs) -> ScoutRunResult:
        """Execute a bounded scout pass and persist its outputs."""
        self.workspace_service.initialize(topic=inputs.topic)
        problem = ProblemCard(
            title=inputs.topic,
            topic=inputs.topic,
            objective=inputs.objective,
            baselines=inputs.baselines,
            evaluation_targets=[item for item in [inputs.dataset, inputs.metric] if item],
            user_preferences=inputs.focus_terms,
        )
        self.store.save_card("problem", problem.model_dump(mode="json"))

        raw = await self.literature_tool.execute(
            topic=inputs.topic,
            task=inputs.objective or inputs.topic,
            focusTerms=inputs.focus_terms,
            keywords=[item for item in [inputs.dataset, inputs.metric] if item],
            baselineNames=inputs.baselines,
            searchRound="full",
            count=inputs.count_per_query,
            maxQueries=inputs.max_queries,
        )
        payload = json.loads(raw)
        results = payload.get("results", [])

        scout_result = ScoutRunResult(
            workspace=self.workspace,
            query_expansions=payload.get("query_expansions", []),
        )
        scout_result.unresolved = self._minimum_unknowns(inputs, results)

        for entry in results[: max(inputs.digest_limit, 0)]:
            card_path = await self._persist_paper(entry)
            scout_result.saved_cards.append(str(card_path))

        literature_path = self.store.save_artifact(
            "literature_map",
            title=f"{inputs.topic} literature map",
            content=self._render_literature_map(inputs, payload),
        )
        framing_path = self.store.save_artifact(
            "framing_report",
            title=f"{inputs.topic} framing report",
            content=self._render_framing_report(inputs, payload, scout_result.unresolved),
        )
        scout_result.saved_artifacts.extend([str(literature_path), str(framing_path)])
        scout_result.next_anchor = "gap-finder" if results else "scout-lite"
        return scout_result

    async def _persist_paper(self, entry: dict) -> Path:
        """Persist one paper note, preferring fetched digests over shallow cards."""
        raw = await self.digest_tool.execute(
            url=entry.get("url", ""),
            title=entry.get("title", ""),
            snippet=entry.get("snippet", ""),
            query=(entry.get("matched_queries") or [""])[0],
        )
        payload = json.loads(raw)
        card_data = payload.get("paper_card")
        if not card_data:
            shallow = build_paper_card(
                title=entry.get("title", ""),
                url=entry.get("url", ""),
                snippet=entry.get("snippet", ""),
                query=(entry.get("matched_queries") or [""])[0],
            )
            card_data = shallow.model_dump(mode="json")
        return self.store.save_card("paper", card_data)

    def _minimum_unknowns(self, inputs: ScoutInputs, results: list[dict]) -> list[str]:
        """Extract the smallest unresolved frame questions that still matter."""
        unknowns: list[str] = []
        if not inputs.dataset:
            unknowns.append("dataset or benchmark contract is still unspecified")
        if not inputs.metric:
            unknowns.append("primary metric contract is still unspecified")
        if not inputs.baselines:
            unknowns.append("baseline shortlist is still unspecified")
        if not results:
            unknowns.append("literature neighborhood is still too thin to justify the next stage")
        return unknowns

    def _render_literature_map(self, inputs: ScoutInputs, payload: dict) -> str:
        """Render a compact literature map artifact."""
        lines = [
            "## Search Summary",
            f"- topic: {inputs.topic}",
            f"- focus terms: {', '.join(inputs.focus_terms) if inputs.focus_terms else 'none'}",
            f"- expanded queries: {len(payload.get('query_expansions', []))}",
            f"- deduplicated results: {payload.get('result_count', 0)}",
            "",
            "## Query Expansions",
        ]
        lines.extend(f"- {query}" for query in payload.get("query_expansions", []))
        lines.extend(["", "## Clusters"])
        clusters = payload.get("clusters", [])
        if clusters:
            for cluster in clusters:
                lines.append(
                    f"- {cluster.get('label')}: {cluster.get('count')} hits; domains = {', '.join(cluster.get('domains', []))}"
                )
        else:
            lines.append("- no stable cluster yet")
        lines.extend(["", "## Representative References"])
        results = payload.get("results", [])
        if results:
            for entry in results[:8]:
                lines.append(f"- [{entry.get('title', 'Untitled')}]({entry.get('url', '')})")
                if entry.get("snippet"):
                    lines.append(f"  - {entry['snippet']}")
        else:
            lines.append("- no search results available")
        return "\n".join(lines).strip()

    def _render_framing_report(self, inputs: ScoutInputs, payload: dict, unresolved: list[str]) -> str:
        """Render a DeepScientist-style scout framing report."""
        results = payload.get("results", [])
        lines = [
            "## Current Frame",
            f"- task-definition layer: {inputs.topic}",
            f"- objective: {inputs.objective or 'not yet specified'}",
            f"- focus terms: {', '.join(inputs.focus_terms) if inputs.focus_terms else 'none'}",
            "",
            "## Evaluation Contract Layer",
            f"- dataset / benchmark: {inputs.dataset or 'unresolved'}",
            f"- primary metric: {inputs.metric or 'unresolved'}",
            "",
            "## Literature Neighborhood Layer",
            f"- deduplicated references found: {payload.get('result_count', 0)}",
            f"- top cluster labels: {', '.join(cluster.get('label', '') for cluster in payload.get('clusters', [])[:4]) or 'none'}",
            "",
            "## Baseline Direction Layer",
            f"- baseline shortlist: {', '.join(inputs.baselines) if inputs.baselines else 'unresolved'}",
            "",
            "## Minimum Unknowns",
        ]
        if unresolved:
            lines.extend(f"- {item}" for item in unresolved)
        else:
            lines.append("- no blocking framing unknown remains in this first scout pass")
        lines.extend(
            [
                "",
                "## Recommended Next Anchor",
                f"- next stage: {'gap-finder' if results else 'scout-lite'}",
                "- rationale: move to gap extraction once the local literature neighborhood is credible; otherwise refresh scout with sharper constraints.",
            ]
        )
        return "\n".join(lines).strip()
