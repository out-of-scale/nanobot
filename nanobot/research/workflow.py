"""Research workflow state resolution for interactive idea discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from nanobot.research.memory_schema import DecisionCard, GapCard, IdeaCard, PaperCard, ProblemCard
from nanobot.research.store import ResearchStore

# Minimum prerequisites for stage advancement
_MIN_PAPERS_FOR_GAPS = 3
_MIN_GAPS_FOR_IDEAS = 1
_MIN_IDEAS_FOR_CRITIC = 2
_MIN_SHORTLISTED_FOR_DECISION = 1


@dataclass
class ResearchWorkflowState:
    """Compact durable-state summary for research mode."""

    stage: str
    skill_names: list[str]
    problem: ProblemCard | None
    papers: list[PaperCard] = field(default_factory=list)
    gaps: list[GapCard] = field(default_factory=list)
    ideas: list[IdeaCard] = field(default_factory=list)
    decisions: list[DecisionCard] = field(default_factory=list)
    missing_prerequisites: list[str] = field(default_factory=list)
    rollback_reason: str = ""
    needs_user_input: bool = False
    user_prompt_hint: str = ""


class ResearchWorkflowResolver:
    """Resolve the next research anchor from the current workspace state."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.store = ResearchStore(workspace)

    def resolve(self) -> ResearchWorkflowState:
        """Infer the active research stage from durable cards with rollback logic."""
        problem = self.store.load_problem()
        papers = [card for card in self.store.load_cards("paper") if isinstance(card, PaperCard)]
        gaps = [card for card in self.store.load_cards("gap") if isinstance(card, GapCard)]
        ideas = [card for card in self.store.load_cards("idea") if isinstance(card, IdeaCard)]
        decisions = [card for card in self.store.load_cards("decision") if isinstance(card, DecisionCard)]

        shortlisted = [idea for idea in ideas if idea.status == "shortlisted"]
        candidates = [idea for idea in ideas if idea.status == "candidate"]

        missing: list[str] = []
        rollback_reason = ""
        needs_user_input = False
        user_prompt_hint = ""

        # Forward stage resolution
        if not papers:
            stage = "scout-lite"
        elif not gaps:
            # Check if we have enough papers before promoting to gap-finder
            if len(papers) < _MIN_PAPERS_FOR_GAPS:
                stage = "scout-lite"
                rollback_reason = f"Only {len(papers)} paper(s) saved; need at least {_MIN_PAPERS_FOR_GAPS} before gap extraction."
                missing.append(f"Need {_MIN_PAPERS_FOR_GAPS - len(papers)} more paper card(s) with meaningful content.")
            else:
                stage = "gap-finder"
        elif not ideas:
            # Check gap quality before promoting to idea-miner
            evidence_gaps = [g for g in gaps if g.evidence_paper_ids]
            if len(evidence_gaps) < _MIN_GAPS_FOR_IDEAS:
                stage = "gap-finder"
                rollback_reason = "No gaps have paper evidence; re-examine paper cards for recurring limitations."
                missing.append("At least 1 gap must reference paper evidence before ideation.")
            else:
                stage = "idea-miner"
        elif not shortlisted:
            if len(candidates) < _MIN_IDEAS_FOR_CRITIC:
                stage = "idea-miner"
                rollback_reason = f"Only {len(candidates)} candidate idea(s); need at least {_MIN_IDEAS_FOR_CRITIC} for meaningful comparison."
                missing.append(f"Need {_MIN_IDEAS_FOR_CRITIC - len(candidates)} more candidate idea(s).")
            else:
                stage = "idea-critic"
        else:
            stage = "decision-lite"

        # Additional self-check: collect missing prerequisites for current stage
        if stage == "gap-finder" and not problem:
            missing.append("Problem card is missing; consider running scout-lite to frame the problem first.")
        if stage == "idea-miner":
            if not any(g.evidence_confidence in ("strong", "moderate") for g in gaps if hasattr(g, "evidence_confidence")):
                missing.append("No gaps have strong or moderate evidence confidence; consider strengthening gap evidence.")
        if stage == "idea-critic":
            ideas_without_mechanism = [i for i in candidates if not i.core_mechanism]
            if ideas_without_mechanism:
                missing.append(f"{len(ideas_without_mechanism)} idea(s) lack a core mechanism description.")
            # Phase 4: Novelty audit gate
            ideas_without_prior_work = [i for i in candidates if not (i.closest_prior_work or "").strip()]
            if ideas_without_prior_work:
                if len(ideas_without_prior_work) == len(candidates):
                    rollback_reason = (
                        "No ideas have prior work comparison filled. "
                        "Complete novelty audit (idea-miner stage) before proceeding to critique."
                    )
                else:
                    missing.append(
                        f"{len(ideas_without_prior_work)} idea(s) are missing closest_prior_work — "
                        "fill this before shortlisting to avoid promoting under-validated ideas."
                    )

        # Phase 3: Pause conditions
        if stage == "idea-miner":
            has_moderate_gap = any(
                g.evidence_confidence in ("strong", "moderate") for g in gaps
            )
            if not has_moderate_gap and gaps:
                needs_user_input = True
                user_prompt_hint = (
                    "All research gaps have weak evidence confidence. "
                    "Validate gap quality before beginning ideation — should I proceed anyway or return to scout-lite?"
                )
        if stage == "decision-lite":
            preferred = getattr(problem, "preferred_idea_ids", []) if problem else []
            if not preferred:
                needs_user_input = True
                user_prompt_hint = (
                    "No preferred idea directions have been recorded. "
                    "Review the shortlisted ideas and tell me which direction you want to commit to."
                )

        return ResearchWorkflowState(
            stage=stage,
            skill_names=[stage],
            problem=problem,
            papers=papers,
            gaps=gaps,
            ideas=ideas,
            decisions=decisions,
            missing_prerequisites=missing,
            rollback_reason=rollback_reason,
            needs_user_input=needs_user_input,
            user_prompt_hint=user_prompt_hint,
        )

    def get_missing_prerequisites(self) -> list[str]:
        """Return the list of missing prerequisites for the current stage."""
        return self.resolve().missing_prerequisites

    def render_context_block(self, *, max_items: int = 5) -> str:
        """Render a small research-state summary for prompt injection."""
        state = self.resolve()
        lines = [
            "# Research State",
            f"- current_stage: {state.stage}",
            f"- active_skill: {', '.join(state.skill_names)}",
            f"- problem: {state.problem.title if state.problem else 'unresolved'}",
            f"- paper_count: {len(state.papers)}",
            f"- gap_count: {len(state.gaps)}",
            f"- idea_count: {len(state.ideas)}",
            f"- shortlisted_count: {len([idea for idea in state.ideas if idea.status == 'shortlisted'])}",
            "",
            "## Current Problem",
            f"- objective: {state.problem.objective if state.problem and state.problem.objective else 'unresolved'}",
            f"- baselines: {', '.join(state.problem.baselines) if state.problem and state.problem.baselines else 'none yet'}",
            f"- evaluation_targets: {', '.join(state.problem.evaluation_targets) if state.problem and state.problem.evaluation_targets else 'unresolved'}",
            "",
            "## Recent Paper Shortlist",
        ]
        if state.papers:
            for paper in state.papers[:max_items]:
                lines.append(
                    f"- {paper.title} (`{paper.card_id}`): {paper.method_family or paper.task or 'no family tagged yet'}"
                )
        else:
            lines.append("- no paper cards yet")

        lines.extend(["", "## Recent Gap Shortlist"])
        if state.gaps:
            for gap in state.gaps[:max_items]:
                evidence_info = f"evidence = {', '.join(gap.evidence_paper_ids) or 'none'}"
                type_info = f", type = {gap.gap_type}" if gap.gap_type else ""
                lines.append(f"- {gap.title} (`{gap.card_id}`): {evidence_info}{type_info}")
        else:
            lines.append("- no gap cards yet")

        lines.extend(["", "## Recent Idea Shortlist"])
        if state.ideas:
            for idea in state.ideas[:max_items]:
                lines.append(f"- {idea.title} (`{idea.card_id}`): status = {idea.status}")
        else:
            lines.append("- no idea cards yet")

        if state.decisions:
            lines.extend(["", "## Recent Decisions"])
            for decision in state.decisions[:max_items]:
                winner_info = f", winner = {decision.winner}" if decision.winner else ""
                lines.append(f"- {decision.title} (`{decision.card_id}`): outcome = {decision.outcome}{winner_info}")

        # Phase 2: Evidence Chains section
        if state.papers or state.gaps or state.ideas:
            from nanobot.research.graph import ResearchGraphSummary
            graph = ResearchGraphSummary(self.store).build()
            chain_lines: list[str] = []
            links = graph.get("links", {})
            gaps_to_papers = links.get("gaps_to_papers", {})
            ideas_to_gaps = links.get("ideas_to_gaps", {})
            for gid, pids in list(gaps_to_papers.items())[:3]:
                chain_lines.append(f"- {gid} ← {', '.join(pids[:3])}")
            for iid, gids in list(ideas_to_gaps.items())[:3]:
                chain_lines.append(f"- {iid} → {', '.join(gids[:3])}")
            if chain_lines:
                lines.extend(["", "## Evidence Chains"])
                lines.extend(chain_lines[:5])

        # Self-check section
        if state.missing_prerequisites or state.rollback_reason or state.needs_user_input:
            lines.extend(["", "## Self-Check"])
            if state.rollback_reason:
                lines.append(f"- rollback_reason: {state.rollback_reason}")
            for item in state.missing_prerequisites:
                lines.append(f"- missing: {item}")
            if state.needs_user_input and state.user_prompt_hint:
                lines.append(f"- needs_user_input: {state.user_prompt_hint}")

        return "\n".join(lines).strip()
