"""
Unit tests that don't require API access.
Tests the scoring logic, config loading, and template rendering on stub data.
"""

from __future__ import annotations

from src.analyzer import CodePatch, IssueAnalysis, PRReview
from src.config import load_profile, load_watchlist
from src.github_client import Issue, is_issue_claimed
from src.reporter import format_draft_markdown, render_html, render_markdown
from src.scoring import score_issue


# ─── Config tests ──────────────────────────────────────────────────────────


def test_watchlist_loads():
    w = load_watchlist()
    assert len(w.repositories) >= 5
    repos = {r.repo for r in w.repositories}
    assert "langchain-ai/langchain" in repos
    assert "jlowin/fastmcp" in repos


def test_profile_loads():
    p = load_profile()
    assert p.skills
    assert "Python" in p.skills.get("proficient", [])
    assert "RAG" in p.skills.get("proficient", [])


# ─── Scoring tests ─────────────────────────────────────────────────────────


def _stub_issue(**overrides) -> Issue:
    defaults = dict(
        repo="test/repo", number=1, title="Test", body="Body",
        labels=["good first issue"], state="open",
        html_url="https://github.com/test/repo/issues/1",
        created_at="2026-05-01T00:00:00Z", updated_at="2026-05-20T00:00:00Z",
        comments=2, assignees=[], user="someone",
    )
    defaults.update(overrides)
    return Issue(**defaults)


def test_score_basic_python_issue_high():
    w = load_watchlist()
    p = load_profile()
    issue = _stub_issue(
        title="Bug in RAG retrieval with FAISS",
        body="When using ChromaDB with embeddings, the Python client raises an error.",
    )
    s = score_issue(
        issue,
        repo_priority="high",
        weights=w.weights,
        skills_profile=p.skills,
        exclusions=p.exclusions,
    )
    assert s.score > 5, f"Expected high score for matching issue, got {s.score}"
    assert "RAG (proficient)" in s.skill_matches or "FAISS (proficient)" in s.skill_matches


def test_score_excluded_topic_low():
    w = load_watchlist()
    p = load_profile()
    issue = _stub_issue(
        title="Improve React UI for the dashboard",
        body="The frontend in React needs refactoring for Tailwind classes.",
    )
    s = score_issue(
        issue, repo_priority="high",
        weights=w.weights, skills_profile=p.skills, exclusions=p.exclusions,
    )
    assert s.score < 0, f"Expected negative score for excluded topic, got {s.score}"


def test_is_issue_claimed_true_when_comment_says_so():
    comments = [{
        "user": "alice",
        "body": "I'll work on this. PR coming today!",
        "created_at": "2026-05-20T00:00:00Z",
    }]
    assert is_issue_claimed(comments, assignees=[]) is True


def test_is_issue_claimed_false_when_no_signal():
    comments = [{
        "user": "alice",
        "body": "I have the same problem on Python 3.12.",
        "created_at": "2026-05-20T00:00:00Z",
    }]
    assert is_issue_claimed(comments, assignees=[]) is False


def test_is_issue_claimed_true_with_assignee():
    assert is_issue_claimed(comments=[], assignees=["someone"]) is True


# ─── Template tests ────────────────────────────────────────────────────────


def test_html_template_renders_with_no_picks():
    ctx = {
        "date": "2026-05-25",
        "subject": "OSS Scout 2026-05-25: nothing today",
        "headline": "0 issues matched today",
        "stats": {"considered": 12, "repos_scanned": 5, "drafts": 0, "reviews": 0},
        "dashboard": {"open_prs": 0, "merged_prs": 0, "total_reports": 1},
        "open_prs": [],
        "picks": [],
        "reviews": [],
        "github_repo": "kaushik/oss-contribution-agent",
    }
    html = render_html(ctx)
    assert "OSS Contribution Scout" in html
    assert "No suitable issues found today" in html


def test_md_template_renders_with_pick():
    ctx = {
        "date": "2026-05-25",
        "subject": "test",
        "headline": "1 issue matched",
        "stats": {"considered": 12, "repos_scanned": 5, "drafts": 1, "reviews": 0},
        "dashboard": {"open_prs": 0, "merged_prs": 0, "total_reports": 1},
        "open_prs": [],
        "picks": [{
            "repo": "test/repo", "number": 42, "title": "Fix bug",
            "url": "https://github.com/test/repo/issues/42",
            "score": 9.5, "score_breakdown": "skill_match=+3.0, label_match=+3.0",
            "estimated_time": "2-4 hours",
            "summary": "Bug in foo.py", "why_it_exists": "Edge case in parser",
            "approach": "1. Reproduce locally\n2. Fix\n3. Add test",
            "files": ["src/foo.py", "tests/test_foo.py"],
            "questions": [], "risk_flags": [], "skill_matches": ["Python (proficient)"],
            "has_draft": True, "draft_path": "examples/drafts/2026-05-25_test_repo_42.md",
        }],
        "reviews": [],
        "github_repo": "kaushik/oss-contribution-agent",
    }
    md = render_markdown(ctx)
    assert "## Today's Picks" in md
    assert "test/repo#42" in md
    assert "Bug in foo.py" in md


def test_format_draft_markdown():
    patch = CodePatch(
        approach_summary="Add a None check in foo.py",
        files_changed=[{
            "path": "src/foo.py",
            "change_type": "modify",
            "content_or_diff": "if value is not None:\n    return value.strip()",
        }],
        tests_to_add="def test_none_handling():\n    assert foo(None) is None",
        review_checklist=[
            "Run the project test suite",
            "Verify the issue is still open",
        ],
        caveats=["ASSUMPTION: value can be None based on the stack trace in the issue"],
    )
    md = format_draft_markdown(
        repo="test/repo", issue_number=42,
        issue_url="https://github.com/test/repo/issues/42",
        title="Crash on None input", patch=patch,
    )
    assert "Draft patch for test/repo#42" in md
    assert "src/foo.py" in md
    assert "Run the project test suite" in md
    assert "ASSUMPTION" in md
