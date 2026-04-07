"""Research-mode primitives for interactive idea discovery."""

from nanobot.research.memory_schema import (
    DecisionCard,
    GapCard,
    IdeaCard,
    PaperCard,
    ProblemCard,
)
from nanobot.research.literature import (
    build_paper_card,
    cluster_literature_results,
    dedupe_literature_results,
    expand_literature_queries,
    parse_web_search_results,
)
from nanobot.research.gaps import GapSynthesisInputs, GapSynthesisResult, GapSynthesisService
from nanobot.research.ideas import (
    IdeaCritiqueInputs,
    IdeaCritiqueResult,
    IdeaCritiqueService,
    IdeaGenerationInputs,
    IdeaGenerationResult,
    IdeaGenerationService,
)
from nanobot.research.workflow import ResearchWorkflowResolver, ResearchWorkflowState
from nanobot.research.workspace import ResearchInitResult, ResearchWorkspaceService

__all__ = [
    "DecisionCard",
    "GapCard",
    "IdeaCard",
    "PaperCard",
    "ProblemCard",
    "ResearchInitResult",
    "ResearchWorkspaceService",
    "build_paper_card",
    "cluster_literature_results",
    "dedupe_literature_results",
    "expand_literature_queries",
    "parse_web_search_results",
    "GapSynthesisInputs",
    "GapSynthesisResult",
    "GapSynthesisService",
    "IdeaGenerationInputs",
    "IdeaGenerationResult",
    "IdeaGenerationService",
    "IdeaCritiqueInputs",
    "IdeaCritiqueResult",
    "IdeaCritiqueService",
    "ResearchWorkflowResolver",
    "ResearchWorkflowState",
]
