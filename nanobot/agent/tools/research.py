"""Research-mode tools built on top of generic web tooling."""

from __future__ import annotations

import json
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.research.literature import (
    build_paper_card,
    build_literature_search_plan,
    cluster_literature_results,
    dedupe_literature_results,
    parse_web_search_results,
    summarize_text,
)
from nanobot.research.store import ResearchStore

_GAP_TYPES = {"failure_mode", "evaluation_blind_spot", "assumption_break", "missing_capability", "efficiency_bottleneck"}


def _validate_card(card_type: str, data: dict[str, Any]) -> list[str]:
    """Validate required fields for LLM-produced research cards.

    Returns a list of error strings. Empty list means valid.
    """
    errors: list[str] = []
    if card_type == "gap":
        if not data.get("evidence_paper_ids"):
            errors.append("evidence_paper_ids must contain at least 1 paper card_id")
        gt = data.get("gap_type", "")
        if gt not in _GAP_TYPES:
            errors.append(
                f"gap_type must be one of {sorted(_GAP_TYPES)}, got: {repr(gt)}"
            )
    elif card_type == "idea":
        if not data.get("target_gap_ids"):
            errors.append("target_gap_ids must contain at least 1 gap card_id")
        if not (data.get("why_now") or "").strip():
            errors.append("why_now must not be empty — cite a specific recent development")
    elif card_type == "decision":
        if not (data.get("winner") or "").strip():
            errors.append("winner must be set to the card_id of the chosen idea")
        if not (data.get("why_winner") or "").strip():
            errors.append("why_winner must not be empty — explain the choice in terms of novelty and feasibility")
    return errors


class LiteratureSearchTool(Tool):
    """Actively search literature around a topic and return structured results."""

    name = "literature_search"
    description = (
        "Actively search nearby literature for a research topic. Returns expanded queries, "
        "deduplicated results, and lightweight clusters for idea discovery."
    )
    parameters = {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "Research topic or problem statement", "minLength": 3},
            "task": {"type": "string", "description": "Optional concrete task framing"},
            "focusTerms": {
                "type": "array",
                "description": "Optional focus terms such as datasets, baselines, or mechanisms",
                "items": {"type": "string"},
            },
            "keywords": {
                "type": "array",
                "description": "Optional keyword aliases or mechanism phrases",
                "items": {"type": "string"},
            },
            "baselineNames": {
                "type": "array",
                "description": "Optional baseline names for neighborhood search",
                "items": {"type": "string"},
            },
            "searchRound": {
                "type": "string",
                "description": "Which search ladder slice to run",
                "enum": ["full", "topic_expansion", "baseline_neighborhood", "method_family", "counter_evidence"],
            },
            "count": {
                "type": "integer",
                "description": "Results per expanded query (1-10)",
                "minimum": 1,
                "maximum": 10,
            },
            "maxQueries": {
                "type": "integer",
                "description": "Maximum expanded queries to issue",
                "minimum": 1,
                "maximum": 6,
            },
        },
        "required": ["topic"],
    }

    def __init__(
        self,
        *,
        search_tool: WebSearchTool | None = None,
        api_key: str | None = None,
        proxy: str | None = None,
    ):
        self.search_tool = search_tool or WebSearchTool(api_key=api_key, proxy=proxy)

    async def execute(
        self,
        topic: str,
        task: str = "",
        focusTerms: list[str] | None = None,
        keywords: list[str] | None = None,
        baselineNames: list[str] | None = None,
        searchRound: str = "full",
        count: int | None = None,
        maxQueries: int = 4,
        **kwargs: Any,
    ) -> str:
        plan = build_literature_search_plan(
            topic,
            task=task,
            focus_terms=focusTerms,
            keywords=keywords,
            baseline_names=baselineNames,
            search_round=searchRound,
            max_queries=maxQueries,
        )
        if not plan:
            return json.dumps({"error": "No valid search query could be built.", "topic": topic}, ensure_ascii=False)

        all_results: list[dict[str, str]] = []
        failures: list[dict[str, str]] = []
        for item in plan:
            query = item["query"]
            raw = await self.search_tool.execute(query=query, count=count)
            if raw.startswith("Error"):
                failures.append({"query": query, "bucket": item["bucket"], "error": raw})
                continue
            all_results.extend(
                parse_web_search_results(raw, query=query, bucket=item["bucket"], label=item["label"])
            )

        if not all_results and failures:
            return json.dumps(
                {
                    "topic": topic,
                    "search_round": searchRound,
                    "search_plan": plan,
                    "query_expansions": [item["query"] for item in plan],
                    "errors": failures,
                },
                ensure_ascii=False,
            )

        deduped = dedupe_literature_results(all_results)
        clusters = cluster_literature_results(deduped)
        return json.dumps(
            {
                "topic": topic,
                "task": task,
                "focus_terms": focusTerms or [],
                "keywords": keywords or [],
                "baseline_names": baselineNames or [],
                "search_round": searchRound,
                "search_plan": plan,
                "query_expansions": [item["query"] for item in plan],
                "result_count": len(deduped),
                "results": deduped,
                "clusters": clusters,
                "errors": failures,
                "next_hint": "Use paper_digest on the most relevant URLs to create paper-card templates, especially from strong or counter-evidence buckets.",
            },
            ensure_ascii=False,
            indent=2,
        )


class PaperDigestTool(Tool):
    """Fetch a paper page and prepare a structured paper-card template."""

    name = "paper_digest"
    description = (
        "Fetch a paper or literature page and return a PaperCard template plus a compact excerpt "
        "for research memory."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Paper or literature URL"},
            "title": {"type": "string", "description": "Optional known title from search results"},
            "snippet": {"type": "string", "description": "Optional known snippet from search results"},
            "query": {"type": "string", "description": "Search query that surfaced this item"},
            "maxChars": {
                "type": "integer",
                "description": "Maximum fetched text length",
                "minimum": 1000,
                "maximum": 30000,
            },
        },
        "required": ["url"],
    }

    def __init__(self, *, fetch_tool: WebFetchTool | None = None, proxy: str | None = None):
        self.fetch_tool = fetch_tool or WebFetchTool(proxy=proxy)

    async def execute(
        self,
        url: str,
        title: str = "",
        snippet: str = "",
        query: str = "",
        maxChars: int = 12000,
        **kwargs: Any,
    ) -> str:
        raw = await self.fetch_tool.execute(url=url, extractMode="text", maxChars=maxChars)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return json.dumps({"url": url, "error": raw}, ensure_ascii=False)

        if payload.get("error"):
            return json.dumps(payload, ensure_ascii=False)

        text = payload.get("text", "")
        paper = build_paper_card(
            title=title or "",
            url=url,
            snippet=snippet or "",
            query=query or "",
            fetched_text=text,
        )
        return json.dumps(
            {
                "paper_card": paper.model_dump(mode="json"),
                "suggested_path": f"memory/papers/{paper.card_id}.md",
                "excerpt": summarize_text(text, max_chars=1800),
                "fetch": {
                    "url": payload.get("url", url),
                    "finalUrl": payload.get("finalUrl", url),
                    "status": payload.get("status"),
                    "extractor": payload.get("extractor"),
                    "truncated": payload.get("truncated", False),
                },
            },
            ensure_ascii=False,
            indent=2,
        )


class SaveResearchCardTool(Tool):
    """Save a validated research card into the workspace."""

    name = "save_research_card"
    description = (
        "Validate and save a research card into the workspace. Use this after paper_digest, gap synthesis, "
        "idea generation, or final shortlisting."
    )
    parameters = {
        "type": "object",
        "properties": {
            "cardType": {
                "type": "string",
                "enum": ["problem", "paper", "gap", "idea", "decision"],
                "description": "Structured research card type",
            },
            "data": {
                "type": "object",
                "description": "Card payload matching the selected card type",
            },
            "filename": {
                "type": "string",
                "description": "Optional file stem override without extension",
            },
        },
        "required": ["cardType", "data"],
    }

    def __init__(self, workspace):
        self.store = ResearchStore(workspace)

    async def execute(self, cardType: str, data: dict[str, Any], filename: str | None = None, **kwargs: Any) -> str:
        errors = _validate_card(cardType, data)
        if errors:
            return json.dumps(
                {
                    "status": "validation_error",
                    "card_type": cardType,
                    "errors": errors,
                    "hint": "Fix the listed fields and call save_research_card again.",
                },
                ensure_ascii=False,
                indent=2,
            )
        path = self.store.save_card(cardType, data, filename=filename)
        return json.dumps(
            {
                "status": "saved",
                "card_type": cardType,
                "path": str(path),
            },
            ensure_ascii=False,
            indent=2,
        )


class ResearchMemoryListRecentTool(Tool):
    """List recent research cards, similar to memory.list_recent in DeepScientist."""

    name = "research_memory_list_recent"
    description = "List the most recently updated research cards in this workspace."
    parameters = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["problem", "paper", "gap", "idea", "decision", "all"],
                "description": "Optional card kind filter",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of cards to return",
                "minimum": 1,
                "maximum": 20,
            },
        },
    }

    def __init__(self, workspace):
        self.store = ResearchStore(workspace)

    async def execute(self, kind: str = "all", limit: int = 10, **kwargs: Any) -> str:
        kinds = ("problem", "paper", "gap", "idea", "decision") if kind == "all" else (kind,)
        items = self.store.list_recent_cards(kinds=kinds, limit=limit)
        return json.dumps({"count": len(items), "items": items}, ensure_ascii=False, indent=2)


class ResearchMemorySearchTool(Tool):
    """Search research cards, similar to memory.search in DeepScientist."""

    name = "research_memory_search"
    description = "Search research cards by query before repeating literature or ideation work."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query across saved research cards", "minLength": 1},
            "kind": {
                "type": "string",
                "enum": ["problem", "paper", "gap", "idea", "decision", "all"],
                "description": "Optional card kind filter",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of matches to return",
                "minimum": 1,
                "maximum": 20,
            },
        },
        "required": ["query"],
    }

    def __init__(self, workspace):
        self.store = ResearchStore(workspace)

    async def execute(self, query: str, kind: str = "all", limit: int = 10, **kwargs: Any) -> str:
        kinds = ("problem", "paper", "gap", "idea", "decision") if kind == "all" else (kind,)
        items = self.store.search_cards(query, kinds=kinds, limit=limit)
        return json.dumps({"count": len(items), "items": items}, ensure_ascii=False, indent=2)


class ResearchMemoryReadTool(Tool):
    """Read one research card, similar to memory.read in DeepScientist."""

    name = "research_memory_read"
    description = "Read one saved research card by type and id."
    parameters = {
        "type": "object",
        "properties": {
            "cardType": {
                "type": "string",
                "enum": ["problem", "paper", "gap", "idea", "decision"],
                "description": "Structured research card type",
            },
            "cardId": {"type": "string", "description": "Card id or saved file stem"},
        },
        "required": ["cardType", "cardId"],
    }

    def __init__(self, workspace):
        self.store = ResearchStore(workspace)

    async def execute(self, cardType: str, cardId: str, **kwargs: Any) -> str:
        card = self.store.load_card(cardType, cardId)
        if not card:
            return json.dumps({"error": "card not found", "card_type": cardType, "card_id": cardId}, ensure_ascii=False)
        return card.to_json()


class SaveResearchArtifactTool(Tool):
    """Save a generated research artifact into the workspace."""

    name = "save_research_artifact"
    description = (
        "Save a generated literature map, gap report, idea shortlist, or brief into the research workspace."
    )
    parameters = {
        "type": "object",
        "properties": {
            "artifactType": {
                "type": "string",
                "enum": ["framing_report", "literature_map", "gap_report", "idea_candidates", "idea_shortlist", "idea_brief", "novelty_audit"],
                "description": "Artifact bucket to save into",
            },
            "title": {"type": "string", "description": "Artifact title", "minLength": 1},
            "content": {"type": "string", "description": "Markdown content to persist", "minLength": 1},
            "filename": {"type": "string", "description": "Optional file stem override without extension"},
        },
        "required": ["artifactType", "title", "content"],
    }

    def __init__(self, workspace):
        self.store = ResearchStore(workspace)

    async def execute(
        self,
        artifactType: str,
        title: str,
        content: str,
        filename: str | None = None,
        **kwargs: Any,
    ) -> str:
        path = self.store.save_artifact(artifactType, title=title, content=content, filename=filename)
        return json.dumps(
            {
                "status": "saved",
                "artifact_type": artifactType,
                "path": str(path),
            },
            ensure_ascii=False,
            indent=2,
        )


class ResearchArtifactListTool(Tool):
    """List saved research artifacts, similar to artifact list behavior."""

    name = "research_artifact_list"
    description = "List saved research artifacts such as literature maps, gap reports, and idea briefs."
    parameters = {
        "type": "object",
        "properties": {
            "artifactType": {
                "type": "string",
                "enum": ["framing_report", "literature_map", "gap_report", "idea_candidates", "idea_shortlist", "idea_brief", "novelty_audit", "all"],
                "description": "Optional artifact bucket filter",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of artifacts to return",
                "minimum": 1,
                "maximum": 20,
            },
        },
    }

    def __init__(self, workspace):
        self.store = ResearchStore(workspace)

    async def execute(self, artifactType: str = "all", limit: int = 10, **kwargs: Any) -> str:
        kind = None if artifactType == "all" else artifactType
        items = self.store.list_artifacts(kind=kind, limit=limit)
        return json.dumps({"count": len(items), "items": items}, ensure_ascii=False, indent=2)


class ResearchArtifactReadTool(Tool):
    """Read one saved research artifact."""

    name = "research_artifact_read"
    description = "Read one saved research artifact by bucket and file stem."
    parameters = {
        "type": "object",
        "properties": {
            "artifactType": {
                "type": "string",
                "enum": ["framing_report", "literature_map", "gap_report", "idea_candidates", "idea_shortlist", "idea_brief", "novelty_audit"],
                "description": "Artifact bucket to read from",
            },
            "name": {"type": "string", "description": "Artifact file stem without extension"},
        },
        "required": ["artifactType", "name"],
    }

    def __init__(self, workspace):
        self.store = ResearchStore(workspace)

    async def execute(self, artifactType: str, name: str, **kwargs: Any) -> str:
        artifact = self.store.read_artifact(kind=artifactType, name=name)
        if not artifact:
            return json.dumps({"error": "artifact not found", "artifact_type": artifactType, "name": name}, ensure_ascii=False)
        return json.dumps(artifact, ensure_ascii=False, indent=2)


class ResearchMemoryAuditTool(Tool):
    """Return a compact audit of the current research workspace state.

    Use this at the start of a resumed session to orient quickly: check what cards
    and artifacts exist before deciding which recovery tool to call next.
    """

    name = "research_memory_audit"
    description = (
        "Return a compact summary of the research workspace: card counts by type, "
        "artifact counts by bucket, current stage, and a recommended first tool to call. "
        "Call this at the start of a resumed session before repeating prior work."
    )
    parameters = {"type": "object", "properties": {}}

    def __init__(self, workspace):
        self.store = ResearchStore(workspace)
        from nanobot.research.workflow import ResearchWorkflowResolver
        self._resolver_cls = ResearchWorkflowResolver
        self._workspace = workspace

    async def execute(self, **kwargs: Any) -> str:
        from nanobot.research.artifacts import ARTIFACT_DIRECTORIES
        state = self._resolver_cls(self._workspace).resolve()

        card_counts = {
            "paper": len(state.papers),
            "gap": len(state.gaps),
            "idea": len(state.ideas),
            "decision": len(state.decisions),
        }

        artifact_counts: dict[str, int] = {}
        for kind in ARTIFACT_DIRECTORIES:
            arts = self.store.list_artifacts(kind=kind, limit=100)
            if arts:
                artifact_counts[kind] = len(arts)

        # Recommended next tool based on stage
        _next_tool_hints: dict[str, str] = {
            "scout-lite": "literature_search to find papers in your topic neighborhood",
            "gap-finder": "research_memory_list_recent kind=paper to review saved papers before gap extraction",
            "idea-miner": "research_artifact_read artifactType=gap_report to review gaps before ideation",
            "idea-critic": "research_artifact_read artifactType=idea_candidates to review candidate ideas",
            "decision-lite": "research_artifact_read artifactType=idea_shortlist to review the shortlisted ideas",
        }
        recommended = _next_tool_hints.get(state.stage, "research_memory_list_recent to orient")

        memory_md = self._workspace / "memory" / "MEMORY.md"
        memory_length = len(memory_md.read_text(encoding="utf-8")) if memory_md.exists() else 0

        return json.dumps(
            {
                "current_stage": state.stage,
                "memory_md_length": memory_length,
                "cards": card_counts,
                "artifacts": artifact_counts,
                "recommended_next_tool": recommended,
                "rollback_reason": state.rollback_reason or None,
            },
            ensure_ascii=False,
            indent=2,
        )
