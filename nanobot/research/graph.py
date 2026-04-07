"""Research knowledge graph utilities for nanobot research mode."""

from __future__ import annotations

from nanobot.research.memory_schema import DecisionCard, GapCard, IdeaCard, PaperCard
from nanobot.research.store import ResearchStore


class ResearchGraphSummary:
    """Build a lightweight knowledge graph summary from a research workspace.

    Maps relationships between papers, gaps, ideas, and decisions so that
    the workflow resolver can render evidence chains in the agent context.
    """

    def __init__(self, store: ResearchStore):
        self.store = store

    def build(self) -> dict:
        """Return a graph summary dict with links and integrity warnings.

        Returns:
            {
                "papers": [card_ids],
                "gaps": [card_ids],
                "ideas": [card_ids],
                "decisions": [card_ids],
                "links": {
                    "gaps_to_papers": {gap_id: [paper_ids]},
                    "ideas_to_gaps": {idea_id: [gap_ids]},
                    "decisions_to_ideas": {decision_id: [idea_ids]},
                },
                "integrity_warnings": [warning_strings],
            }
        """
        papers = [c for c in self.store.load_cards("paper") if isinstance(c, PaperCard)]
        gaps = [c for c in self.store.load_cards("gap") if isinstance(c, GapCard)]
        ideas = [c for c in self.store.load_cards("idea") if isinstance(c, IdeaCard)]
        decisions = [c for c in self.store.load_cards("decision") if isinstance(c, DecisionCard)]

        paper_ids = {p.card_id for p in papers}
        gap_ids_set = {g.card_id for g in gaps}
        idea_ids_set = {i.card_id for i in ideas}

        gaps_to_papers: dict[str, list[str]] = {}
        for gap in gaps:
            linked = [pid for pid in gap.evidence_paper_ids if pid in paper_ids]
            if linked:
                gaps_to_papers[gap.card_id] = linked

        ideas_to_gaps: dict[str, list[str]] = {}
        for idea in ideas:
            linked = [gid for gid in idea.target_gap_ids if gid in gap_ids_set]
            if linked:
                ideas_to_gaps[idea.card_id] = linked

        decisions_to_ideas: dict[str, list[str]] = {}
        for decision in decisions:
            linked = [iid for iid in decision.idea_ids if iid in idea_ids_set]
            if linked:
                decisions_to_ideas[decision.card_id] = linked

        integrity_warnings = []
        for warnings in self.store.check_graph_integrity().values():
            integrity_warnings.extend(warnings)

        return {
            "papers": [p.card_id for p in papers],
            "gaps": [g.card_id for g in gaps],
            "ideas": [i.card_id for i in ideas],
            "decisions": [d.card_id for d in decisions],
            "links": {
                "gaps_to_papers": gaps_to_papers,
                "ideas_to_gaps": ideas_to_gaps,
                "decisions_to_ideas": decisions_to_ideas,
            },
            "integrity_warnings": integrity_warnings,
        }
