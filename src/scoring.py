"""
scoring.py — Rank candidate issues by suitability for the human contributor.

The scorer is deterministic and explainable: every issue ends up with a
component breakdown (label boost + recency + skill match - low-effort penalty)
so the daily report shows WHY the agent picked these issues, not just that it did.

This is the "judgment" layer that protects against the agent recommending slop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .github_client import Issue


@dataclass
class ScoredIssue:
    issue: Issue
    score: float
    components: dict[str, float] = field(default_factory=dict)
    skill_matches: list[str] = field(default_factory=list)
    estimated_effort: str = "unknown"  # "small" | "medium" | "large"
    flag_reasons: list[str] = field(default_factory=list)  # warnings to surface in report


def _days_since(iso_ts: str) -> float:
    try:
        ts = datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).timestamp()
        return (datetime.now(timezone.utc).timestamp() - ts) / 86400
    except Exception:
        return 999.0


def _match_skills(text: str, skills_profile: dict) -> tuple[list[str], float]:
    """Return matched skills + weighted skill-match score."""
    text_lower = text.lower()
    matches: list[str] = []
    score = 0.0

    weights = {"proficient": 1.0, "intermediate": 0.7, "learning": 0.4, "awareness_only": 0.1}
    for level, weight in weights.items():
        for skill in skills_profile.get(level, []) or []:
            if skill.lower() in text_lower:
                matches.append(f"{skill} ({level})")
                score += weight
    return matches, score


def _detect_exclusions(text: str, exclusions: list[str]) -> list[str]:
    """Return exclusion phrases that match. Any match flags the issue."""
    text_lower = text.lower()
    hits = []
    # Map exclusion concept to detection phrases
    detect_map = {
        "TypeScript only": ["typescript", " .ts ", ".tsx", "in ts"],
        "Rust core": ["rust", " .rs ", "cargo"],
        "C++ core": ["c++", "cpp", " .cc ", " .cpp "],
        "Go": [" golang ", " .go ", " in go "],
        "frontend / React UI": ["react", "jsx", "tailwind", "ui component"],
        "mobile (Swift / Kotlin)": ["swift", "kotlin", " ios ", " android "],
    }
    for exc in exclusions:
        for phrase in detect_map.get(exc, [exc.lower()]):
            if phrase in text_lower:
                hits.append(exc)
                break
    return hits


def _estimate_effort(issue: Issue) -> tuple[str, float]:
    """Heuristic effort estimate. Returns (label, penalty_or_bonus).

    Signals:
    - Body length (very short = probably small; very long = either RFC or well-documented bug)
    - Labels (docs/typo = small; feature/refactor = large)
    - Number of code blocks (more = better-documented = lower risk)
    """
    body = issue.body or ""
    word_count = len(body.split())
    code_block_count = body.count("```")
    labels_lower = [l.lower() for l in issue.labels]

    small_signals = ["typo", "documentation", "docs", "good first issue", "docs-only"]
    large_signals = ["epic", "rfc", "refactor", "redesign", "breaking", "major"]

    if any(s in " ".join(labels_lower) for s in large_signals):
        return "large", -1.5
    if word_count < 30 and not any(s in " ".join(labels_lower) for s in small_signals):
        # Very short and not docs-labeled = probably underspecified
        return "underspecified", -1.0
    if any(s in " ".join(labels_lower) for s in small_signals) and word_count < 100:
        return "small", -0.5  # could be too trivial to be valuable
    if word_count > 50 and code_block_count >= 2:
        # Well-documented with repro = sweet spot
        return "medium-well-documented", 1.0
    if word_count > 500:
        return "large-discussion", -0.5
    return "medium", 0.0


def score_issue(
    issue: Issue,
    *,
    repo_priority: str,  # "high" | "medium" | "low"
    weights: dict,
    skills_profile: dict,
    exclusions: list[str],
) -> ScoredIssue:
    """Compute a score for one issue. Returns a ScoredIssue with full breakdown."""
    components: dict[str, float] = {}
    flags: list[str] = []

    # 1. Repo priority
    priority_score = {
        "high": weights.get("priority_high", 2.0),
        "medium": weights.get("priority_medium", 1.0),
        "low": weights.get("priority_low", 0.5),
    }.get(repo_priority, 1.0)
    components["repo_priority"] = priority_score

    # 2. Label boost (any "good first issue" / "help wanted" type label)
    good_labels = {"good first issue", "good-first-issue", "help wanted", "help-wanted", "documentation", "docs"}
    label_set = {l.lower() for l in issue.labels}
    if label_set & good_labels:
        components["label_match"] = weights.get("label_match", 3.0)
    else:
        components["label_match"] = 0.0

    # 3. Recency
    days_old = _days_since(issue.updated_at)
    if days_old < 14:
        components["recent_activity"] = weights.get("recent_activity", 2.0)
    elif days_old < 30:
        components["recent_activity"] = weights.get("recent_activity", 2.0) * 0.5
    else:
        components["recent_activity"] = 0.0
        if days_old > 60:
            flags.append(f"Issue is {int(days_old)} days old without recent activity.")

    # 4. Linked-PR check (set externally by main.py via has_linked_pr)
    if issue.has_linked_pr:
        components["no_existing_pr"] = 0.0
        flags.append("This issue already has a linked PR; verify it's not duplicate work.")
    else:
        components["no_existing_pr"] = weights.get("no_existing_pr", 2.0)

    # 5. Skill matching
    text = f"{issue.title} {issue.body}"
    matches, skill_score = _match_skills(text, skills_profile)
    components["skill_match"] = skill_score * weights.get("skill_match", 3.0) / 3.0

    # 6. Exclusion check
    exclusion_hits = _detect_exclusions(text, exclusions)
    if exclusion_hits:
        components["exclusion_penalty"] = -10.0  # effectively skip
        flags.append(f"Matches exclusions: {', '.join(exclusion_hits)}")

    # 7. Effort estimate
    effort_label, effort_adjustment = _estimate_effort(issue)
    components["effort_adjustment"] = effort_adjustment
    if effort_label == "underspecified":
        flags.append("Issue body is short — may need clarification from maintainer before drafting.")
    if effort_label == "large-discussion":
        flags.append("Long issue thread — likely an RFC; not a quick PR.")

    total = sum(components.values())

    return ScoredIssue(
        issue=issue,
        score=total,
        components=components,
        skill_matches=matches,
        estimated_effort=effort_label,
        flag_reasons=flags,
    )


def rank_issues(
    scored: list[ScoredIssue],
    *,
    max_results: int,
    min_score: float = 1.0,
) -> list[ScoredIssue]:
    """Sort by score descending, filter out hopeless candidates."""
    valid = [s for s in scored if s.score >= min_score]
    valid.sort(key=lambda s: s.score, reverse=True)
    return valid[:max_results]
