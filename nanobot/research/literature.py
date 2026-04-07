"""Helpers for active literature search in research mode."""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlparse

from nanobot.research.memory_schema import PaperCard
from nanobot.utils.helpers import safe_filename

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")
_STOPWORDS = {
    "about",
    "after",
    "against",
    "analysis",
    "approach",
    "approaches",
    "based",
    "between",
    "from",
    "into",
    "method",
    "methods",
    "model",
    "models",
    "paper",
    "papers",
    "research",
    "results",
    "study",
    "survey",
    "system",
    "using",
    "with",
}


def expand_literature_queries(
    topic: str,
    focus_terms: list[str] | None = None,
    *,
    max_queries: int = 4,
) -> list[str]:
    """Expand a topic into several search queries for nearby literature."""
    return [item["query"] for item in build_literature_search_plan(topic, focus_terms=focus_terms, max_queries=max_queries)]


def build_literature_search_plan(
    topic: str,
    *,
    task: str = "",
    focus_terms: list[str] | None = None,
    keywords: list[str] | None = None,
    baseline_names: list[str] | None = None,
    search_round: str = "full",
    max_queries: int = 6,
) -> list[dict[str, str]]:
    """Build a bounded, DeepScientist-inspired search ladder for literature discovery."""
    base = " ".join(topic.split()).strip()
    if not base:
        return []

    task = " ".join(task.split()).strip()
    focus_terms = [term.strip() for term in (focus_terms or []) if term and term.strip()]
    keywords = [term.strip() for term in (keywords or []) if term and term.strip()]
    baseline_names = [term.strip() for term in (baseline_names or []) if term and term.strip()]
    primary_focus = _dedupe_terms([*focus_terms, *keywords])[:3]

    strategy_builders = {
        "topic_expansion": _topic_expansion_queries,
        "baseline_neighborhood": _baseline_neighborhood_queries,
        "method_family": _method_family_queries,
        "counter_evidence": _counter_evidence_queries,
    }
    ordered_rounds = list(strategy_builders)
    active_rounds = ordered_rounds if search_round == "full" else [search_round]

    bucket_queries: dict[str, list[str]] = {}
    max_depth = 0
    for round_name in active_rounds:
        builder = strategy_builders.get(round_name)
        if not builder:
            continue
        queries = builder(base=base, task=task, focus_terms=primary_focus, baseline_names=baseline_names)
        bucket_queries[round_name] = queries
        max_depth = max(max_depth, len(queries))

    seen: set[str] = set()
    expanded: list[dict[str, str]] = []
    for depth in range(max_depth):
        for round_name in active_rounds:
            queries = bucket_queries.get(round_name, [])
            if depth >= len(queries):
                continue
            query = queries[depth]
            normalized = query.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            expanded.append(
                {
                    "query": query,
                    "bucket": round_name,
                    "label": f"{round_name}:{depth + 1}",
                }
            )
            if len(expanded) >= max_queries:
                return expanded
    return expanded


def _topic_expansion_queries(*, base: str, task: str, focus_terms: list[str], baseline_names: list[str]) -> list[str]:
    queries = [
        base,
        f"{base} survey",
        f"{base} recent advances",
        f"{base} paper",
    ]
    if task and task.lower() != base.lower():
        queries.append(f"{task} {base}")
    if focus_terms:
        queries.append(f"{base} {' '.join(focus_terms[:2])}")
    return queries


def _baseline_neighborhood_queries(
    *,
    base: str,
    task: str,
    focus_terms: list[str],
    baseline_names: list[str],
) -> list[str]:
    queries: list[str] = []
    for baseline in baseline_names[:2]:
        queries.append(f"{base} {baseline}")
        queries.append(f"{baseline} follow-up")
    if baseline_names and focus_terms:
        queries.append(f"{base} {baseline_names[0]} {' '.join(focus_terms[:2])}")
    return queries


def _method_family_queries(*, base: str, task: str, focus_terms: list[str], baseline_names: list[str]) -> list[str]:
    queries: list[str] = []
    if focus_terms:
        queries.append(f"{base} {' '.join(focus_terms[:2])} method")
        queries.append(f"{base} {' '.join(focus_terms[:2])} benchmark")
    if task and task.lower() != base.lower():
        queries.append(f"{task} {' '.join(focus_terms[:2])}".strip())
    return [query for query in queries if query.strip()]


def _counter_evidence_queries(
    *,
    base: str,
    task: str,
    focus_terms: list[str],
    baseline_names: list[str],
) -> list[str]:
    core = " ".join(focus_terms[:2]).strip()
    pivot = f"{base} {core}".strip()
    queries = [
        f"{pivot} failure analysis",
        f"{pivot} limitations",
        f"{pivot} negative results",
        f"{pivot} simpler baseline",
    ]
    if baseline_names:
        queries.append(f"{baseline_names[0]} limitations")
        queries.append(f"alternative to {baseline_names[0]}")
    if len(baseline_names) >= 2:
        queries.append(f"{baseline_names[0]} vs {baseline_names[1]}")
    return queries


def _dedupe_terms(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        normalized = term.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(term)
    return result


def parse_web_search_results(
    raw: str,
    *,
    query: str | None = None,
    bucket: str | None = None,
    label: str | None = None,
) -> list[dict[str, str]]:
    """Parse the plain-text output of WebSearchTool into structured results."""
    if not raw or raw.startswith("Error"):
        return []

    lines = raw.splitlines()
    inferred_query = query or ""
    if lines and lines[0].startswith("Results for:"):
        inferred_query = lines[0].split(":", 1)[1].strip()

    results: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if match := re.match(r"^\d+\.\s+(.*)$", stripped):
            if current:
                results.append(current)
            current = {
                "title": match.group(1).strip(),
                "url": "",
                "snippet": "",
                "query": inferred_query,
                "bucket": bucket or "topic_expansion",
                "label": label or "",
            }
            continue
        if current is None:
            continue
        if stripped.startswith("http://") or stripped.startswith("https://"):
            current["url"] = stripped
        else:
            snippet = current.get("snippet", "")
            current["snippet"] = f"{snippet} {stripped}".strip()

    if current:
        results.append(current)
    return [item for item in results if item.get("url")]


def dedupe_literature_results(results: list[dict[str, str]]) -> list[dict[str, str]]:
    """Merge duplicated search hits by URL while preserving query provenance."""
    merged: dict[str, dict[str, str | list[str]]] = {}
    for item in results:
        url = item.get("url", "").rstrip("/")
        if not url:
            continue
        query = item.get("query", "")
        bucket = item.get("bucket", "")
        label = item.get("label", "")
        if url not in merged:
            merged[url] = {
                "title": item.get("title", ""),
                "url": url,
                "snippet": item.get("snippet", ""),
                "domain": _domain_for(url),
                "matched_queries": [query] if query else [],
                "matched_buckets": [bucket] if bucket else [],
                "matched_labels": [label] if label else [],
            }
            continue

        existing = merged[url]
        if len(item.get("title", "")) > len(str(existing["title"])):
            existing["title"] = item.get("title", "")
        if len(item.get("snippet", "")) > len(str(existing["snippet"])):
            existing["snippet"] = item.get("snippet", "")
        if query and query not in existing["matched_queries"]:
            existing["matched_queries"].append(query)
        if bucket and bucket not in existing["matched_buckets"]:
            existing["matched_buckets"].append(bucket)
        if label and label not in existing["matched_labels"]:
            existing["matched_labels"].append(label)

    return sorted(
        (
            {
                "title": str(item["title"]),
                "url": str(item["url"]),
                "snippet": str(item["snippet"]),
                "domain": str(item["domain"]),
                "matched_queries": list(item["matched_queries"]),
                "matched_buckets": list(item["matched_buckets"]),
                "matched_labels": list(item["matched_labels"]),
            }
            for item in merged.values()
        ),
        key=lambda item: (len(item["matched_queries"]) + len(item["matched_buckets"]), item["domain"], item["title"]),
        reverse=True,
    )


def cluster_literature_results(results: list[dict[str, str]]) -> list[dict[str, object]]:
    """Create lightweight clusters so the agent sees structure rather than a flat hit list."""
    buckets: dict[str, list[dict[str, str]]] = {}
    for item in results:
        label = classify_literature_result(item)
        buckets.setdefault(label, []).append(item)

    clusters: list[dict[str, object]] = []
    for label, items in sorted(buckets.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        clusters.append(
            {
                "label": label,
                "count": len(items),
                "domains": sorted({entry["domain"] for entry in items}),
                "buckets": sorted({bucket for entry in items for bucket in entry.get("matched_buckets", [])}),
                "titles": [entry["title"] for entry in items[:5]],
            }
        )
    return clusters


def classify_literature_result(item: dict[str, str]) -> str:
    """Assign a lightweight literature label for grouping."""
    haystack = " ".join(
        [item.get("title", ""), item.get("snippet", ""), item.get("domain", "")]
    ).lower()
    if "survey" in haystack or "review" in haystack:
        return "survey_or_review"
    if "benchmark" in haystack or "dataset" in haystack or "leaderboard" in haystack:
        return "benchmark_or_dataset"
    if item.get("domain", "").endswith("github.com"):
        return "code_or_repo"
    if "arxiv" in haystack or "openreview" in haystack or "acm" in haystack or "ieee" in haystack:
        return "paper_or_preprint"
    return "general_reference"


def infer_keywords(*parts: str, limit: int = 8) -> list[str]:
    """Infer keywords from title, snippets, and fetched text."""
    counts: Counter[str] = Counter()
    for part in parts:
        for word in _WORD_RE.findall((part or "").lower()):
            if word in _STOPWORDS or word.isdigit():
                continue
            counts[word] += 1
    return [word for word, _ in counts.most_common(limit)]


def estimate_evidence_confidence(url: str, domain: str = "") -> str:
    """Estimate evidence confidence based on source domain credibility."""
    resolved_domain = domain or _domain_for(url)
    high_confidence = ("arxiv.org", "openreview.net", "aclweb.org", "proceedings.mlr.press")
    moderate_confidence = ("acm.org", "ieee.org", "springer.com", "sciencedirect.com", "nature.com")
    if any(resolved_domain.endswith(d) for d in high_confidence):
        return "strong"
    if any(resolved_domain.endswith(d) for d in moderate_confidence):
        return "moderate"
    return "weak"


def build_paper_card(
    *,
    title: str,
    url: str,
    snippet: str = "",
    query: str = "",
    fetched_text: str = "",
) -> PaperCard:
    """Create a minimally filled PaperCard from fetched web material."""
    resolved_title = title.strip() or _fallback_title(url)
    card_id = safe_filename(resolved_title.lower()).replace(" ", "-")[:80] or "paper"
    year = infer_year(resolved_title, snippet, fetched_text, url)
    notes = summarize_text(fetched_text or snippet, max_chars=1000)
    domain = _domain_for(url)
    evidence_confidence = estimate_evidence_confidence(url, domain)
    cluster_label = _infer_cluster_label(resolved_title, snippet, domain)
    claimed_contribution = _extract_contribution_hint(fetched_text or snippet)
    return PaperCard(
        card_id=card_id,
        title=resolved_title,
        year=year,
        url=url,
        keywords=infer_keywords(resolved_title, snippet, fetched_text),
        source_queries=[query] if query else [],
        evidence_confidence=evidence_confidence,
        cluster=cluster_label,
        claimed_contribution=claimed_contribution,
        notes=notes,
    )


def summarize_text(text: str, *, max_chars: int = 1200) -> str:
    """Extract a compact summary-like excerpt from fetched text."""
    compact = " ".join((text or "").split())
    if not compact:
        return ""
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def infer_year(*parts: str) -> int | None:
    """Infer a publication year if one is visible in available text."""
    for part in parts:
        if not part:
            continue
        if match := _YEAR_RE.search(part):
            return int(match.group(0))
    return None


def _domain_for(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc.lower()


def _fallback_title(url: str) -> str:
    parsed = urlparse(url)
    tail = parsed.path.rstrip("/").split("/")[-1] if parsed.path else parsed.netloc
    return tail.replace("-", " ").replace("_", " ").strip() or parsed.netloc or "Untitled paper"


def _infer_cluster_label(title: str, snippet: str, domain: str) -> str:
    """Infer a lightweight cluster label for a paper card."""
    haystack = f"{title} {snippet} {domain}".lower()
    if "survey" in haystack or "review" in haystack:
        return "survey_or_review"
    if "benchmark" in haystack or "dataset" in haystack:
        return "benchmark_or_dataset"
    if domain.endswith("github.com"):
        return "code_or_repo"
    return "paper_or_preprint"


def _extract_contribution_hint(text: str) -> str:
    """Extract a one-sentence contribution hint from fetched text."""
    if not text:
        return ""
    lower = text.lower()
    for marker in ("we propose", "we introduce", "we present", "this paper proposes", "this work introduces"):
        idx = lower.find(marker)
        if idx >= 0:
            end = text.find(".", idx)
            if end > idx:
                return text[idx:end + 1].strip()[:300]
    return ""
