"""
analyzer.py — LLM-powered analysis of issues using Groq.

This is where the agent earns its keep. After deterministic scoring narrows
to a shortlist, the LLM:
  1. ANALYZE: explain the issue in plain English and the likely approach
  2. DRAFT: produce a code patch for top-ranked issues
  3. REVIEW: critique the user's own open PRs

All three modes return Pydantic-validated structured outputs.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
MAX_TOKENS = 3000


# ─── Pydantic schemas ──────────────────────────────────────────────────────


class IssueAnalysis(BaseModel):
    """Lightweight analysis — what the issue is asking, why it matters, suggested approach."""

    plain_english_summary: str = Field(..., description="What the issue is asking, in 2-3 sentences")
    why_it_exists: str = Field(..., description="The underlying problem, 1-2 sentences")
    suggested_approach: str = Field(..., description="High-level approach, 2-4 bullet points worth of guidance")
    files_likely_involved: list[str] = Field(default_factory=list, description="File paths the contributor will likely touch")
    questions_to_ask: list[str] = Field(default_factory=list, description="Questions to ask the maintainer before drafting if anything is unclear")
    estimated_time: str = Field(..., description="Estimated time for someone moderately familiar with the codebase")
    risk_flags: list[str] = Field(default_factory=list, description="Things to verify before submitting (e.g., 'breaking change?')")


class CodePatch(BaseModel):
    """A drafted code change for the contributor to review."""

    approach_summary: str = Field(..., description="2-3 sentences describing what the patch does and why")
    files_changed: list[dict] = Field(..., description="List of {path, change_type, content_or_diff}")
    tests_to_add: Optional[str] = Field(None, description="Suggested test cases or existing tests that should cover this")
    review_checklist: list[str] = Field(..., description="Things the human MUST verify before submitting")
    caveats: list[str] = Field(default_factory=list, description="Known unknowns, assumptions, things the agent couldn't verify")


class PRReview(BaseModel):
    """Self-review of one of the user's open PRs."""

    overall_assessment: str = Field(..., description="Honest 2-3 sentence assessment")
    strengths: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list, description="Concrete problems to fix before review")
    suggested_changes: list[str] = Field(default_factory=list, description="Specific code-level suggestions")
    likelihood_of_merge: str = Field(..., description="low | medium | high, with one-sentence justification")


# ─── Internal helpers ──────────────────────────────────────────────────────


def _client() -> OpenAI:
    return OpenAI(api_key=os.environ["GROQ_API_KEY"], base_url=GROQ_BASE_URL)


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [ln for ln in lines if not ln.startswith("```")]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found: {text[:200]}")
    return json.loads(text[start : end + 1])


def _call(system: str, user: str) -> str:
    response = _client().chat.completions.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=0.3,  # lower temp for code/analysis
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return response.choices[0].message.content or ""


def _call_with_retry(system: str, user: str, schema: type, label: str):
    """Call LLM with one retry on validation failure."""
    last_err = None
    for attempt in (1, 2):
        try:
            text = _call(system, user)
            data = _extract_json(text)
            return schema(**data)
        except (ValidationError, ValueError, json.JSONDecodeError) as e:
            last_err = e
            if attempt == 1:
                user += "\n\nIMPORTANT: Previous response was not valid JSON matching the schema. Return ONLY a JSON object."
    raise ValueError(f"{label} generation failed after 2 attempts: {last_err}")


# ─── Mode 1: Scout — analyze an issue ──────────────────────────────────────


ANALYZE_SYSTEM = """You are an experienced open-source contributor and senior engineer reviewing
issues on behalf of a Master's-level CS student looking for AI Engineering roles.

Your job: read a GitHub issue and tell them whether/how to contribute, honestly.

Rules:
- NEVER recommend submitting code without first verifying the approach with maintainers
  for projects that require it (LangChain, FastMCP, etc.)
- If the issue is underspecified, say so — don't invent requirements.
- If the issue would require deep knowledge the contributor doesn't have, flag it.
- Be specific about files/modules likely involved. "Probably somewhere in the codebase"
  is useless. Reference real file paths when you can infer them from the issue.
- Estimated times: be realistic. "30 min" is for trivial typo fixes. Most real
  bugs are 2-4 hours including testing.

Return ONLY a JSON object matching the schema. No prose outside the JSON.
"""

ANALYZE_USER_TEMPLATE = """Repo: {repo}
Issue #{number}: {title}
URL: {url}
Labels: {labels}

Issue body:
---
{body}
---

Recent comments (newest first, max 3):
---
{comments}
---

Project-specific notes:
{repo_notes}

Return a JSON object with these keys:
- plain_english_summary (string)
- why_it_exists (string)
- suggested_approach (string)
- files_likely_involved (array of strings, file paths)
- questions_to_ask (array of strings)
- estimated_time (string like "30 min", "2-4 hours", "half-day")
- risk_flags (array of strings)
"""


def analyze_issue(
    *,
    repo: str,
    number: int,
    title: str,
    body: str,
    labels: list[str],
    url: str,
    comments: list[dict],
    repo_notes: str,
) -> IssueAnalysis:
    """Generate a lightweight analysis for an issue."""
    comments_str = "\n\n".join(
        f"@{c.get('user', '?')}: {(c.get('body', '') or '')[:500]}"
        for c in comments[:3]
    ) or "(no comments)"

    user = ANALYZE_USER_TEMPLATE.format(
        repo=repo,
        number=number,
        title=title,
        url=url,
        labels=", ".join(labels),
        body=(body or "(no body)")[:3000],
        comments=comments_str,
        repo_notes=repo_notes or "(none)",
    )
    return _call_with_retry(ANALYZE_SYSTEM, user, IssueAnalysis, "Analysis")


# ─── Mode 2: Draft — produce a code patch ──────────────────────────────────


DRAFT_SYSTEM = """You are drafting a code patch for a human contributor to review and submit.

CRITICAL RULES:
- You are NOT submitting this PR. The human will review your patch line-by-line.
- If you don't have enough context (e.g., haven't seen the full file you're modifying),
  SAY SO explicitly in the caveats. Don't invent function signatures.
- Prefer minimal, focused changes. The smallest correct patch is the best patch.
- ALWAYS include a `review_checklist` of specific things the human must verify.
- Tests are first-class. If the issue is a bug fix, propose the failing test that
  would have caught it, not just the fix.
- Match the project's style conventions visible in the file snippets provided.
- Mark any guess explicitly: "ASSUMPTION: ..."

Return ONLY a JSON object matching the schema.
"""

DRAFT_USER_TEMPLATE = """Repo: {repo}
Issue #{number}: {title}
URL: {url}

Issue body:
---
{body}
---

Relevant file contents (truncated):
---
{file_snippets}
---

Project-specific notes:
{repo_notes}

Produce a code-patch draft as a JSON object with these keys:
- approach_summary (string, 2-3 sentences)
- files_changed (array of objects, each with keys: path, change_type ["create"|"modify"|"delete"], content_or_diff [the full new file content for "create", a unified diff or replacement snippet for "modify"])
- tests_to_add (string or null, suggested tests)
- review_checklist (array of strings, MUST include things the human should verify before submitting)
- caveats (array of strings, list assumptions and unknowns)

If you cannot draft a reasonable patch with the context provided, return a JSON object
where `approach_summary` explains what context is missing and `files_changed` is empty.
"""


def draft_patch(
    *,
    repo: str,
    number: int,
    title: str,
    body: str,
    url: str,
    file_snippets: dict[str, str],
    repo_notes: str,
) -> CodePatch:
    """Draft a code patch. file_snippets is {path: content} truncated to ~5KB each."""
    snippets_str = "\n\n".join(
        f"=== {path} ===\n{content[:5000]}"
        for path, content in (file_snippets or {}).items()
    ) or "(no files fetched; agent has limited context)"

    user = DRAFT_USER_TEMPLATE.format(
        repo=repo,
        number=number,
        title=title,
        url=url,
        body=(body or "(no body)")[:3000],
        file_snippets=snippets_str,
        repo_notes=repo_notes or "(none)",
    )
    return _call_with_retry(DRAFT_SYSTEM, user, CodePatch, "Draft")


# ─── Mode 3: Review — critique user's PRs ──────────────────────────────────


REVIEW_SYSTEM = """You are a senior engineer reviewing a contributor's open PR before
they ask for maintainer review.

Be specific. "Looks good" is useless. Point at lines, identify likely review comments
the maintainer will leave, and suggest preemptive fixes.

Likelihood of merge guidance:
- "high": follows conventions, has tests, scope is tight, no obvious issues
- "medium": needs polish but core change is sound
- "low": fundamental issues — wrong approach, missing tests, scope too broad

Return ONLY a JSON object matching the schema.
"""

REVIEW_USER_TEMPLATE = """Repo: {repo}
PR #{number}: {title}
URL: {url}

Diff (truncated):
---
{diff}
---

Review this PR honestly. Return JSON with keys:
- overall_assessment (string, 2-3 sentences)
- strengths (array of strings)
- concerns (array of strings, specific problems with line refs where possible)
- suggested_changes (array of strings, specific actionable changes)
- likelihood_of_merge (string: "low", "medium", or "high", with justification appended)
"""


def review_pr(*, repo: str, number: int, title: str, url: str, diff: str) -> PRReview:
    """Critique an open PR."""
    user = REVIEW_USER_TEMPLATE.format(
        repo=repo,
        number=number,
        title=title,
        url=url,
        diff=(diff or "(diff empty or unavailable)")[:8000],
    )
    return _call_with_retry(REVIEW_SYSTEM, user, PRReview, "Review")
