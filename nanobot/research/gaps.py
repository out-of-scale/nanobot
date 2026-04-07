"""Gap synthesis for research mode — evidence-driven, not template-driven.

This service is a DETERMINISTIC FALLBACK for programmatic and integration-test use.
In LLM-interactive sessions, gap analysis is performed by the agent using the
gap-finder skill prompt + save_research_card tool directly. This service runs
keyword-based classification and limitation clustering without any LLM calls.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from nanobot.research.memory_schema import GapCard, PaperCard, ProblemCard
from nanobot.research.store import ResearchStore
from nanobot.utils.helpers import safe_filename

# Gap taxonomy: each gap should be classified into one of these types.
GAP_TYPES = (
    "failure_mode",
    "evaluation_blind_spot",
    "assumption_break",
    "missing_capability",
    "efficiency_bottleneck",
)

# Keywords that hint at each gap type
_GAP_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "failure_mode": ("fail", "brittle", "collapse", "unstable", "break", "degrad", "noise", "shift", "ood", "robust"),
    "evaluation_blind_spot": ("metric", "benchmark", "evaluation", "protocol", "compare", "fair", "setting", "split"),
    "assumption_break": ("assum", "prerequisite", "condition", "require", "depend", "constraint", "violat"),
    "missing_capability": ("lack", "miss", "cannot", "unable", "no support", "absent", "gap", "limitation"),
    "efficiency_bottleneck": ("slow", "expensive", "cost", "compute", "memory", "latency", "scale", "efficien"),
}


@dataclass
class GapSynthesisInputs:
    """Inputs for one local gap-synthesis pass."""

    max_gaps: int = 5


@dataclass
class GapSynthesisResult:
    """Summary of a gap-synthesis run."""

    workspace: Path
    gap_ids: list[str] = field(default_factory=list)
    saved_cards: list[str] = field(default_factory=list)
    saved_artifacts: list[str] = field(default_factory=list)
    next_anchor: str = "idea-miner"
    is_llm_generated: bool = False


class GapSynthesisService:
    """Turn saved paper cards into a bounded set of evidence-backed research gaps."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.store = ResearchStore(workspace)

    def run(self, inputs: GapSynthesisInputs | None = None) -> GapSynthesisResult:
        """Synthesize a compact set of evidence-backed gaps from saved paper cards."""
        params = inputs or GapSynthesisInputs()
        problem = self.store.load_problem()
        papers = [card for card in self.store.load_cards("paper") if isinstance(card, PaperCard)]

        result = GapSynthesisResult(workspace=self.workspace)
        if not papers:
            gap = self._fallback_gap(problem)
            path = self.store.save_card("gap", gap.model_dump(mode="json"))
            artifact = self.store.save_artifact(
                "gap_report",
                title=f"{(problem.title if problem else 'research')} gap report",
                content=self._render_gap_report(problem, [gap], evidence_summary=["- literature coverage is still too thin; run scout-lite again with sharper focus terms"]),
            )
            result.gap_ids.append(gap.card_id)
            result.saved_cards.append(str(path))
            result.saved_artifacts.append(str(artifact))
            result.next_anchor = "scout-lite"
            return result

        # Extract limitations from all papers
        limitation_clusters = self._cluster_limitations(papers)

        # Build gaps from limitation clusters
        gaps = self._build_gaps_from_limitations(limitation_clusters, papers, problem, limit=params.max_gaps)

        for gap in gaps:
            path = self.store.save_card("gap", gap.model_dump(mode="json"))
            result.gap_ids.append(gap.card_id)
            result.saved_cards.append(str(path))

        artifact = self.store.save_artifact(
            "gap_report",
            title=f"{(problem.title if problem else 'research')} gap report",
            content=self._render_gap_report(problem, gaps, evidence_summary=self._render_evidence_summary(limitation_clusters, papers)),
        )
        result.saved_artifacts.append(str(artifact))
        return result

    def _cluster_limitations(self, papers: list[PaperCard]) -> dict[str, list[tuple[PaperCard, str]]]:
        """Cluster paper limitations by gap type."""
        clusters: dict[str, list[tuple[PaperCard, str]]] = defaultdict(list)

        for paper in papers:
            all_text = self._paper_text(paper)
            for limitation in paper.limitations:
                gap_type = self._classify_limitation(limitation, all_text)
                clusters[gap_type].append((paper, limitation))

            # Also check notes for limitations
            if paper.notes and not paper.limitations:
                gap_type = self._classify_limitation(paper.notes, all_text)
                clusters[gap_type].append((paper, paper.notes))

        return dict(clusters)

    def _classify_limitation(self, limitation: str, context: str = "") -> str:
        """Classify a limitation text into a gap type."""
        combined = f"{limitation} {context}".lower()
        scores: dict[str, int] = {}
        for gap_type, keywords in _GAP_TYPE_KEYWORDS.items():
            scores[gap_type] = sum(1 for kw in keywords if kw in combined)

        best_type = max(scores, key=lambda t: scores[t])
        if scores[best_type] > 0:
            return best_type
        return "missing_capability"

    def _build_gaps_from_limitations(
        self,
        clusters: dict[str, list[tuple[PaperCard, str]]],
        papers: list[PaperCard],
        problem: ProblemCard | None,
        limit: int,
    ) -> list[GapCard]:
        """Build gap cards from clustered limitations."""
        # Sort clusters by number of supporting papers (more evidence = higher priority)
        ranked = sorted(clusters.items(), key=lambda item: -len({p.card_id for p, _ in item[1]}))

        gaps: list[GapCard] = []
        for gap_type, entries in ranked[:limit]:
            evidence_papers = list({p.card_id: p for p, _ in entries}.values())
            limitations = [lim for _, lim in entries]

            # Determine evidence confidence
            confidence = "weak"
            unique_papers = len({p.card_id for p, _ in entries})
            if unique_papers >= 3:
                confidence = "strong"
            elif unique_papers >= 2:
                confidence = "moderate"

            # Build gap description from the actual limitations
            description = self._synthesize_description(gap_type, limitations, evidence_papers)
            title = self._gap_title(gap_type, limitations)
            gap_id = safe_filename(f"gap-{gap_type}")

            related_baselines = list(problem.baselines if problem else [])
            if not related_baselines:
                related_baselines = sorted({p.method_family for p in evidence_papers if p.method_family})[:3]

            gap = GapCard(
                card_id=gap_id,
                title=title,
                description=description,
                evidence_paper_ids=[p.card_id for p in evidence_papers[:5]],
                related_baselines=related_baselines,
                research_value=self._assess_value(gap_type, evidence_papers, problem),
                main_risk=self._assess_risk(gap_type),
                gap_type=gap_type,
                evidence_confidence=confidence,
                notes=self._gap_notes(gap_type, evidence_papers, limitations, problem),
            )
            gaps.append(gap)

        return gaps

    def _synthesize_description(self, gap_type: str, limitations: list[str], papers: list[PaperCard]) -> str:
        """Synthesize a gap description from paper limitations."""
        if not limitations:
            return f"A {gap_type.replace('_', ' ')} pattern was detected across the paper neighborhood but specific limitations need further investigation."

        # Use the first few unique limitations as evidence
        unique_lims = list(dict.fromkeys(limitations))[:3]
        evidence_str = "; ".join(unique_lims)
        paper_count = len(papers)
        return (
            f"Across {paper_count} paper(s) in the local neighborhood, recurring {gap_type.replace('_', ' ')} evidence includes: "
            f"{evidence_str}. "
            f"This pattern suggests a gap that current methods have not yet resolved."
        )

    def _gap_title(self, gap_type: str, limitations: list[str] | None = None) -> str:
        """Generate a descriptive title for a gap type using the first limitation for specificity."""
        base_titles = {
            "failure_mode": "Failure Mode",
            "evaluation_blind_spot": "Evaluation Blind Spot",
            "assumption_break": "Assumption Break",
            "missing_capability": "Missing Capability",
            "efficiency_bottleneck": "Efficiency Bottleneck",
        }
        base = base_titles.get(gap_type, gap_type.replace("_", " ").title())
        # Try to pick a short anchor phrase from the first limitation
        if limitations:
            first = limitations[0].strip().rstrip(".")
            # Use up to 6 words from the first limitation as a specific anchor
            words = first.split()[:6]
            anchor = " ".join(words)
            if anchor:
                return f"{base}: {anchor}"
        return f"{base} Gap"

    def _assess_value(self, gap_type: str, papers: list[PaperCard], problem: ProblemCard | None) -> str:
        """Assess the research value of a gap."""
        values = {
            "failure_mode": "Addressing failure modes creates sharper contributions than chasing average-case improvements.",
            "evaluation_blind_spot": "A cleaner evaluation contract can surface under-explored issues and make claims more defensible.",
            "assumption_break": "Breaking faulty assumptions often reveals more impactful research directions than incremental improvement.",
            "missing_capability": "Filling a missing capability gap can create a strong differentiated contribution.",
            "efficiency_bottleneck": "Efficiency improvements that preserve quality are often both practically useful and publishable.",
        }
        return values.get(gap_type, "This gap may lead to a meaningful research contribution.")

    def _assess_risk(self, gap_type: str) -> str:
        """Assess the main risk of pursuing a gap."""
        risks = {
            "failure_mode": "The failure regime may be underspecified without careful scoping.",
            "evaluation_blind_spot": "This may turn into benchmark hygiene rather than a method contribution.",
            "assumption_break": "The assumption may only break in edge cases that are hard to demonstrate.",
            "missing_capability": "The missing capability may be too expensive to address or too niche to justify.",
            "efficiency_bottleneck": "Efficiency-focused work can degrade into engineering tuning if the mechanism is not conceptually distinct.",
        }
        return risks.get(gap_type, "The gap may be less impactful than it initially appears.")

    def _fallback_gap(self, problem: ProblemCard | None) -> GapCard:
        title = "Literature Coverage Gap"
        return GapCard(
            card_id="gap-literature-coverage",
            title=title,
            description="The current workspace still lacks enough nearby paper evidence to identify a trustworthy research gap.",
            evidence_paper_ids=[],
            related_baselines=list(problem.baselines if problem else []),
            research_value="Sharpening the literature neighborhood is the fastest way to avoid chasing a pseudo-gap.",
            main_risk="Without more paper evidence, later idea generation will collapse into unsupported brainstorming.",
            gap_type="missing_capability",
            evidence_confidence="weak",
            notes="Return to scout-lite, tighten the task frame, and digest more representative papers before ideation.",
        )

    def _gap_notes(self, gap_type: str, papers: list[PaperCard], limitations: list[str], problem: ProblemCard | None) -> str:
        evidence_titles = ", ".join(p.title for p in papers[:3]) or "current paper set"
        focus = ", ".join(problem.user_preferences) if problem and problem.user_preferences else "the current task frame"
        lim_count = len(limitations)
        return (
            f"This {gap_type.replace('_', ' ')} gap is derived from {lim_count} limitation(s) across {evidence_titles}. "
            f"It should be evaluated relative to {focus} before promotion into ideation."
        )

    def _render_gap_report(
        self,
        problem: ProblemCard | None,
        gaps: list[GapCard],
        *,
        evidence_summary: list[str],
    ) -> str:
        lines = [
            "## Current Frame",
            f"- topic: {problem.title if problem else 'unresolved'}",
            f"- baselines: {', '.join(problem.baselines) if problem and problem.baselines else 'none yet'}",
            f"- evaluation targets: {', '.join(problem.evaluation_targets) if problem and problem.evaluation_targets else 'unresolved'}",
            "",
            "## Evidence Summary",
        ]
        lines.extend(evidence_summary or ["- no stable paper evidence was available"])
        lines.extend(["", "## Strongest Gaps"])
        for gap in gaps:
            lines.append(f"- {gap.title} (`{gap.card_id}`)")
            lines.append(f"  - type: {gap.gap_type}")
            lines.append(f"  - evidence: {', '.join(gap.evidence_paper_ids) or 'none'}")
            lines.append(f"  - confidence: {gap.evidence_confidence}")
            lines.append(f"  - value: {gap.research_value}")
            lines.append(f"  - risk: {gap.main_risk}")
        lines.extend(
            [
                "",
                "## Recommended Next Anchor",
                f"- next stage: {'idea-miner' if gaps and gaps[0].card_id != 'gap-literature-coverage' else 'scout-lite'}",
                "- rationale: ideation should start from a small number of explicit, evidence-backed gaps rather than a loose paper pile.",
            ]
        )
        return "\n".join(lines).strip()

    def _render_evidence_summary(self, clusters: dict[str, list[tuple[PaperCard, str]]], papers: list[PaperCard]) -> list[str]:
        lines: list[str] = []
        for gap_type, entries in sorted(clusters.items(), key=lambda item: -len(item[1])):
            unique_papers = len({p.card_id for p, _ in entries})
            paper_titles = ", ".join(sorted({p.title for p, _ in entries})[:3]) or "no representative papers"
            lines.append(f"- {gap_type}: {unique_papers} supporting paper(s); anchors = {paper_titles}")
        return lines

    @staticmethod
    def _paper_text(paper: PaperCard) -> str:
        parts = [
            paper.title,
            paper.task,
            paper.method_family,
            paper.core_mechanism,
            " ".join(paper.contributions),
            " ".join(paper.limitations),
            " ".join(paper.keywords),
            paper.notes,
        ]
        return " ".join(part.lower() for part in parts if part)
