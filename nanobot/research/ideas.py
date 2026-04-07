"""Idea generation and lightweight critique for research mode — evidence-driven.

This service is a DETERMINISTIC FALLBACK for programmatic and integration-test use.
In LLM-interactive sessions, idea generation and critique are performed by the agent
using the idea-miner and idea-critic skill prompts + save_research_card tool directly.
This service uses static direction seeds (_DIRECTION_SEEDS) and keyword-based scoring
without any LLM calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from nanobot.research.memory_schema import DecisionCard, GapCard, IdeaCard, PaperCard, ProblemCard
from nanobot.research.store import ResearchStore
from nanobot.utils.helpers import safe_filename

_SCORE_MAP = {
    "high": 3,
    "moderate": 2,
    "medium": 2,
    "moderate to high": 2,
    "low": 1,
}

# Direction templates used as seeds when no LLM is in the loop.
# Each gap_type maps to a direction template that provides a starting mechanism.
_DIRECTION_SEEDS: dict[str, list[dict[str, str]]] = {
    "failure_mode": [
        {
            "title_suffix": "Adaptive Failure Mitigation",
            "mechanism": "Add a detection-and-routing layer that adapts method behavior when it enters the failure regime.",
            "why_now_hint": "Recent work shows the failure regime is measurable, enabling targeted mitigation.",
        },
        {
            "title_suffix": "Robustness-Aware Training",
            "mechanism": "Modify the training objective to penalize behavior degradation under the identified failure conditions.",
            "why_now_hint": "The failure mode is now well-documented enough to design a training signal against it.",
        },
    ],
    "evaluation_blind_spot": [
        {
            "title_suffix": "Extended Evaluation Protocol",
            "mechanism": "Introduce an evaluation extension that covers the blind spot while maintaining backward compatibility.",
            "why_now_hint": "The blind spot has been independently noted by multiple papers, creating consensus.",
        },
        {
            "title_suffix": "Calibrated Comparison Layer",
            "mechanism": "Add a calibration step that normalizes method outputs across the inconsistent evaluation dimension.",
            "why_now_hint": "Enough benchmark variants exist to validate the calibration approach.",
        },
    ],
    "assumption_break": [
        {
            "title_suffix": "Assumption-Free Variant",
            "mechanism": "Replace the fragile assumption with a learned or adaptive component that works across settings.",
            "why_now_hint": "Recent evidence shows the assumption breaks more often than previously thought.",
        },
        {
            "title_suffix": "Graceful Degradation Route",
            "mechanism": "Keep the assumption as default but add a fallback path when it is violated.",
            "why_now_hint": "The violation conditions are now well-characterized enough to detect and handle.",
        },
    ],
    "missing_capability": [
        {
            "title_suffix": "Capability Extension",
            "mechanism": "Extend the current best method with a new module that addresses the missing capability.",
            "why_now_hint": "The gap is now visible because the rest of the method family has matured enough to expose it.",
        },
        {
            "title_suffix": "Cross-Method Integration",
            "mechanism": "Borrow the missing capability from an adjacent method family and integrate it into the current pipeline.",
            "why_now_hint": "The adjacent method has matured enough for reliable integration.",
        },
    ],
    "efficiency_bottleneck": [
        {
            "title_suffix": "Selective Computation Route",
            "mechanism": "Replace the expensive component with a selective or sparse variant that preserves quality on the critical path.",
            "why_now_hint": "Selective computation techniques have become reliable enough for this domain.",
        },
        {
            "title_suffix": "Efficient Architecture Redesign",
            "mechanism": "Redesign the bottleneck component to achieve the same function with fundamentally less computation.",
            "why_now_hint": "Architectural innovations in adjacent fields provide proven patterns to adapt.",
        },
    ],
}


@dataclass
class IdeaGenerationInputs:
    """Inputs for one local idea-mining pass."""

    max_ideas: int = 5


@dataclass
class IdeaGenerationResult:
    """Summary of one idea generation pass."""

    workspace: Path
    idea_ids: list[str] = field(default_factory=list)
    saved_cards: list[str] = field(default_factory=list)
    saved_artifacts: list[str] = field(default_factory=list)
    next_anchor: str = "idea-critic"
    is_llm_generated: bool = False


@dataclass
class IdeaCritiqueInputs:
    """Inputs for one lightweight shortlist pass."""

    shortlist_size: int = 3


@dataclass
class IdeaCritiqueResult:
    """Summary of one idea shortlist pass."""

    workspace: Path
    shortlisted_ids: list[str] = field(default_factory=list)
    saved_cards: list[str] = field(default_factory=list)
    saved_artifacts: list[str] = field(default_factory=list)
    next_anchor: str = "decision-lite"
    is_llm_generated: bool = False


class IdeaGenerationService:
    """Turn gap cards into candidate innovation ideas using bounded divergence."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.store = ResearchStore(workspace)

    def run(self, inputs: IdeaGenerationInputs | None = None) -> IdeaGenerationResult:
        params = inputs or IdeaGenerationInputs()
        problem = self.store.load_problem()
        gaps = [card for card in self.store.load_cards("gap") if isinstance(card, GapCard)]
        papers = [card for card in self.store.load_cards("paper") if isinstance(card, PaperCard)]

        # Check for existing rejected/parked ideas to reuse
        existing_ideas = [card for card in self.store.load_cards("idea") if isinstance(card, IdeaCard)]
        parked = [i for i in existing_ideas if i.status in ("parked", "rejected")]

        result = IdeaGenerationResult(workspace=self.workspace)
        if not gaps:
            return result

        # Phase 1: Raw Slate — generate 2 directions per gap
        raw_slate: list[IdeaCard] = []
        for gap in gaps[:params.max_ideas]:
            raw_slate.extend(self._generate_directions(problem, gap, papers))

        # Phase 2: Serious Frontier — collapse trivial duplicates
        frontier = self._collapse_to_frontier(raw_slate)

        # Limit to max_ideas
        frontier = frontier[:params.max_ideas]

        for idea in frontier:
            path = self.store.save_card("idea", idea.model_dump(mode="json"))
            result.idea_ids.append(idea.card_id)
            result.saved_cards.append(str(path))

        artifact = self.store.save_artifact(
            "idea_candidates",
            title=f"{(problem.title if problem else 'research')} idea candidates",
            content=self._render_idea_candidates(problem, frontier, parked),
        )
        result.saved_artifacts.append(str(artifact))

        # Phase 4: Novelty audit artifact
        novelty_artifact = self.store.save_artifact(
            "novelty_audit",
            title=f"{(problem.title if problem else 'research')} novelty audit",
            content=self._render_novelty_audit(problem, frontier),
        )
        result.saved_artifacts.append(str(novelty_artifact))
        return result

    def _generate_directions(self, problem: ProblemCard | None, gap: GapCard, papers: list[PaperCard]) -> list[IdeaCard]:
        """Generate 2 directions per gap using the bounded divergence approach."""
        gap_type = gap.gap_type or "missing_capability"
        seeds = _DIRECTION_SEEDS.get(gap_type, _DIRECTION_SEEDS["missing_capability"])[:2]

        # Find the closest paper for prior-work contrast
        closest_paper = self._find_closest_paper(gap, papers)
        closest_prior = f"{closest_paper.title} ({closest_paper.card_id})" if closest_paper else "no close prior work identified yet"

        ideas: list[IdeaCard] = []
        for i, seed in enumerate(seeds):
            idea_id = safe_filename(f"idea-{gap_type}-{i + 1}")
            title = f"{seed['title_suffix']} for {gap.title}"
            if problem and problem.topic:
                topic_anchor = f" Anchored on {problem.topic}."
            else:
                topic_anchor = ""

            baseline_diff = ""
            if problem and problem.baselines:
                baseline_diff = f"Unlike {', '.join(problem.baselines[:2])}, this route specifically addresses {gap.title.lower()}."
            else:
                baseline_diff = f"The baseline approach does not explicitly handle the {gap_type.replace('_', ' ')} identified in this gap."

            # Derive novelty/feasibility from the gap's evidence strength
            # Stronger evidence → higher feasibility (we know the gap is real)
            # First seed per gap type → slightly higher novelty (more novel approach)
            confidence_map = {"strong": "High", "moderate": "Moderate", "weak": "Low"}
            ev_conf = getattr(gap, "evidence_confidence", "moderate") or "moderate"
            feasibility_from_evidence = confidence_map.get(ev_conf, "Medium")
            novelty_level = "High" if i == 0 else "Moderate"

            idea = IdeaCard(
                card_id=idea_id,
                title=title,
                target_gap_ids=[gap.card_id],
                one_sentence_pitch=f"{seed['mechanism']}{topic_anchor}",
                core_mechanism=seed["mechanism"],
                difference_from_baseline=baseline_diff,
                difference_from_prior_work=f"Differs from {closest_prior} by targeting the specific {gap_type.replace('_', ' ')} pattern.",
                expected_value=gap.research_value,
                novelty=novelty_level,
                feasibility=feasibility_from_evidence,
                main_risk=gap.main_risk,
                validation_hint=f"Compare behavior with and without the proposed mechanism on the {gap_type.replace('_', ' ')} condition.",
                why_now=seed["why_now_hint"],
                closest_prior_work=closest_prior,
                status="candidate",
                notes=f"Generated from {gap.card_id} ({gap.title}) via bounded divergence.",
            )
            ideas.append(idea)

        return ideas

    def _find_closest_paper(self, gap: GapCard, papers: list[PaperCard]) -> PaperCard | None:
        """Find the paper most closely related to a gap."""
        if gap.evidence_paper_ids:
            for p in papers:
                if p.card_id in gap.evidence_paper_ids:
                    return p
        return papers[0] if papers else None

    def _render_novelty_audit(self, problem: ProblemCard | None, ideas: list[IdeaCard]) -> str:
        """Render a novelty audit table for the current idea frontier."""
        lines = [
            f"## Novelty Audit — {problem.title if problem else 'research'}",
            "",
            "| Idea | Closest Prior Work | Differentiation | Overlap Risk |",
            "|------|-------------------|-----------------|--------------|",
        ]
        for idea in ideas:
            prior = idea.closest_prior_work or "—"
            diff = idea.difference_from_prior_work or "—"
            # Overlap risk: high = unknown prior work; high = too short diff; else low
            if not (idea.closest_prior_work or "").strip():
                risk = "high (prior work unknown)"
            elif len((idea.difference_from_prior_work or "").split()) < 5:
                risk = "high (differentiation too vague)"
            else:
                risk = "low"
            lines.append(f"| {idea.title} | {prior[:60]} | {diff[:60]} | {risk} |")
        return "\n".join(lines).strip()

    def _collapse_to_frontier(self, raw_slate: list[IdeaCard]) -> list[IdeaCard]:
        """Collapse raw slate by removing trivially similar ideas."""
        seen_mechanisms: dict[str, IdeaCard] = {}
        for idea in raw_slate:
            key = idea.core_mechanism[:50].lower().strip()
            if key not in seen_mechanisms:
                seen_mechanisms[key] = idea
        return list(seen_mechanisms.values())

    def _render_idea_candidates(self, problem: ProblemCard | None, ideas: list[IdeaCard], parked: list[IdeaCard]) -> str:
        lines = [
            "## Candidate Frontier",
            f"- topic: {problem.title if problem else 'unresolved'}",
            f"- candidate count: {len(ideas)}",
            f"- reusable parked ideas from prior rounds: {len(parked)}",
            "",
            "## Candidates",
        ]
        for idea in ideas:
            lines.append(f"- {idea.title} (`{idea.card_id}`)")
            lines.append(f"  - gap: {', '.join(idea.target_gap_ids) or 'none'}")
            lines.append(f"  - pitch: {idea.one_sentence_pitch}")
            lines.append(f"  - why now: {idea.why_now}")
            lines.append(f"  - closest prior: {idea.closest_prior_work}")
            lines.append(f"  - novelty: {idea.novelty}")
            lines.append(f"  - feasibility: {idea.feasibility}")

        if parked:
            lines.extend(["", "## Previously Parked (may be revisited)"])
            for idea in parked[:3]:
                lines.append(f"- {idea.title} (`{idea.card_id}`): {idea.notes or 'no notes'}")

        lines.extend(
            [
                "",
                "## Recommended Next Anchor",
                "- next stage: idea-critic",
                "- rationale: candidate generation is complete only after weak or duplicate ideas are explicitly filtered.",
            ]
        )
        return "\n".join(lines).strip()


class IdeaCritiqueService:
    """Perform five-dimensional assessment and create a shortlist with comparison table."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.store = ResearchStore(workspace)

    def run(self, inputs: IdeaCritiqueInputs | None = None) -> IdeaCritiqueResult:
        params = inputs or IdeaCritiqueInputs()
        problem = self.store.load_problem()
        ideas = [card for card in self.store.load_cards("idea") if isinstance(card, IdeaCard)]

        result = IdeaCritiqueResult(workspace=self.workspace)
        if not ideas:
            return result

        ranked = sorted(ideas, key=self._score_idea, reverse=True)
        shortlist = ranked[: max(params.shortlist_size, 1)]
        shortlist_ids = {idea.card_id for idea in shortlist}

        # Build why-not explanations for parked ideas
        why_not_others: list[str] = []
        for idea in ranked:
            status = "shortlisted" if idea.card_id in shortlist_ids else "parked"
            updated = idea.model_copy(update={"status": status})
            path = self.store.save_card("idea", updated.model_dump(mode="json"))
            result.saved_cards.append(str(path))

            if status == "parked":
                why_not_others.append(
                    f"{idea.card_id}: parked because {'novelty is weaker' if _score_text(idea.novelty) < 2 else 'validation path is less clear'} compared to shortlisted ideas."
                )

        winner = shortlist[0]
        decision = DecisionCard(
            card_id="decision-idea-shortlist",
            title="Idea shortlist decision",
            outcome="shortlist",
            idea_ids=[idea.card_id for idea in shortlist],
            rationale=self._decision_rationale(shortlist, ranked[len(shortlist):]),
            next_steps=[idea.validation_hint for idea in shortlist],
            question=f"Which research direction to pursue for {problem.title if problem else 'this topic'}?",
            winner=winner.card_id,
            why_winner=f"{winner.title} leads because it balances novelty ({winner.novelty}) and feasibility ({winner.feasibility}) with a clear first validation path: {winner.validation_hint}",
            why_not_others=why_not_others,
            next_action=winner.validation_hint or "Define the smallest credible validation experiment.",
            notes="Shortlist created from the current research workspace frontier.",
        )
        decision_path = self.store.save_card("decision", decision.model_dump(mode="json"), filename=decision.card_id)

        shortlist_artifact = self.store.save_artifact(
            "idea_shortlist",
            title=f"{(problem.title if problem else 'research')} idea shortlist",
            content=self._render_shortlist(problem, shortlist, ranked),
        )
        brief_artifact = self.store.save_artifact(
            "idea_brief",
            title=f"{(problem.title if problem else 'research')} idea brief",
            content=self._render_idea_brief(problem, winner),
        )

        result.shortlisted_ids = [idea.card_id for idea in shortlist]
        result.saved_cards.append(str(decision_path))
        result.saved_artifacts.extend([str(shortlist_artifact), str(brief_artifact)])
        return result

    def _score_idea(self, idea: IdeaCard) -> tuple[int, int, int]:
        novelty = _score_text(idea.novelty)
        feasibility = _score_text(idea.feasibility)
        risk_penalty = _score_text(idea.main_risk, invert=True)
        return novelty + feasibility + risk_penalty, novelty, feasibility

    def _decision_rationale(self, shortlist: list[IdeaCard], parked: list[IdeaCard]) -> str:
        winner = shortlist[0]
        reasons = [
            f"{winner.card_id} leads because it balances novelty ({winner.novelty}) and feasibility ({winner.feasibility}) while keeping a clear first validation path.",
        ]
        if parked:
            reasons.append(
                "The parked alternatives were not discarded as bad ideas; they were deprioritized because their validation surface is weaker or their novelty story is less sharp right now."
            )
        return " ".join(reasons)

    def _render_shortlist(self, problem: ProblemCard | None, shortlist: list[IdeaCard], ranked: list[IdeaCard]) -> str:
        lines = [
            "## Shortlist Summary",
            f"- topic: {problem.title if problem else 'unresolved'}",
            f"- shortlisted: {len(shortlist)} / {len(ranked)}",
            "",
            "## Candidate Comparison Table",
            "",
            "| Idea | Novelty | Feasibility | Risk | Why-Now | First Validation |",
            "|------|---------|-------------|------|---------|-----------------|",
        ]
        for idea in ranked:
            status_marker = " **[SHORTLISTED]**" if idea.status == "shortlisted" else ""
            lines.append(
                f"| {idea.title}{status_marker} | {idea.novelty} | {idea.feasibility} | {idea.main_risk} | {idea.why_now or 'N/A'} | {idea.validation_hint or 'N/A'} |"
            )

        lines.extend(["", "## Winning Directions"])
        for idea in shortlist:
            lines.append(f"- {idea.title} (`{idea.card_id}`)")
            lines.append(f"  - pitch: {idea.one_sentence_pitch}")
            lines.append(f"  - novelty: {idea.novelty}")
            lines.append(f"  - feasibility: {idea.feasibility}")
            lines.append(f"  - risk: {idea.main_risk}")
            lines.append(f"  - why now: {idea.why_now}")
            lines.append(f"  - closest prior: {idea.closest_prior_work}")
            lines.append(f"  - first validation: {idea.validation_hint}")

        parked = [idea for idea in ranked if idea.card_id not in {item.card_id for item in shortlist}]
        if parked:
            lines.extend(["", "## Parked Directions (with why-not)"])
            for idea in parked:
                why = "weaker novelty signal" if _score_text(idea.novelty) < 2 else "less clear validation path"
                lines.append(f"- {idea.title} (`{idea.card_id}`): parked — {why}")

        lines.extend(
            [
                "",
                "## Recommended Next Anchor",
                "- next stage: decision-lite",
                "- rationale: a shortlist is useful only if the workspace now records why these directions won and what should be validated first.",
            ]
        )
        return "\n".join(lines).strip()

    def _render_idea_brief(self, problem: ProblemCard | None, winner: IdeaCard) -> str:
        return "\n".join(
            [
                "## Selected Direction",
                f"- topic: {problem.title if problem else 'unresolved'}",
                f"- winner: {winner.title}",
                f"- target gaps: {', '.join(winner.target_gap_ids) or 'none'}",
                "",
                "## Two-Sentence Pitch",
                f"{winner.one_sentence_pitch}",
                f"This route looks worth pursuing because {winner.expected_value.lower()}" if winner.expected_value else "",
                "",
                "## Why It Wins Now",
                f"- why now: {winner.why_now}",
                f"- closest prior work: {winner.closest_prior_work}",
                f"- baseline difference: {winner.difference_from_baseline}",
                f"- prior-work difference: {winner.difference_from_prior_work}",
                f"- novelty: {winner.novelty}",
                f"- feasibility: {winner.feasibility}",
                "",
                "## Main Risk",
                f"- {winner.main_risk}",
                "",
                "## First Validation Move",
                f"- {winner.validation_hint}",
            ]
        ).strip()


def _score_text(text: str, *, invert: bool = False) -> int:
    lowered = text.lower()
    value = 2
    for key, score in _SCORE_MAP.items():
        if key in lowered:
            value = score
            break
    if invert:
        return 4 - value
    return value
