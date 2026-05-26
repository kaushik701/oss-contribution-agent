"""
github_client.py — Thin wrapper around the GitHub REST API for issue scouting.

Uses the `requests` library (no PyGithub dependency, keeps things small).
Authenticates with GITHUB_TOKEN env var. Public-only operations work without
a token but get rate-limited fast, so a token is strongly recommended.

We're read-only: this module never writes to GitHub. No comments, no PRs,
no stars. The agent's mantra is "read, draft, hand off to human."
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

GITHUB_API = "https://api.github.com"
DEFAULT_TIMEOUT = 30


@dataclass
class Issue:
    repo: str
    number: int
    title: str
    body: str
    labels: list[str]
    state: str
    html_url: str
    created_at: str
    updated_at: str
    comments: int
    assignees: list[str]
    user: str
    # Computed at scoring time, not from API
    has_linked_pr: bool = False


def _headers() -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "oss-contribution-agent",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get(url: str, params: Optional[dict] = None) -> dict | list:
    """GET with retry on rate-limit. Sleeps until reset rather than failing."""
    for attempt in range(3):
        resp = requests.get(url, headers=_headers(), params=params, timeout=DEFAULT_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            reset = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
            sleep_for = max(reset - int(time.time()), 5) + 2
            print(f"[github] Rate limited; sleeping {sleep_for}s.")
            time.sleep(min(sleep_for, 120))
            continue
        if resp.status_code >= 500 and attempt < 2:
            time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
    raise RuntimeError(f"GitHub API failed after retries: {url}")


def fetch_open_issues(
    repo: str,
    labels: list[str],
    *,
    per_page: int = 30,
    since_days: int = 60,
) -> list[Issue]:
    """Fetch open issues from a repo matching ANY of the given labels.

    GitHub's `labels` query is AND-of-labels, so we make one call per label
    and dedupe by issue number. Cheaper than pulling everything and filtering.
    """
    seen: dict[int, Issue] = {}
    cutoff = datetime.now(timezone.utc).timestamp() - since_days * 86400

    for label in labels:
        url = f"{GITHUB_API}/repos/{repo}/issues"
        params = {
            "state": "open",
            "labels": label,
            "per_page": per_page,
            "sort": "updated",
            "direction": "desc",
        }
        try:
            results = _get(url, params=params)
        except Exception as e:
            print(f"[github] Failed to fetch {repo} label={label}: {e}")
            continue

        if not isinstance(results, list):
            continue

        for it in results:
            # Skip pull requests; the issues endpoint returns both.
            if "pull_request" in it:
                continue
            num = it["number"]
            if num in seen:
                continue
            updated = datetime.fromisoformat(it["updated_at"].replace("Z", "+00:00")).timestamp()
            if updated < cutoff:
                continue

            seen[num] = Issue(
                repo=repo,
                number=num,
                title=it.get("title", ""),
                body=it.get("body") or "",
                labels=[lbl["name"] for lbl in it.get("labels", [])],
                state=it.get("state", "open"),
                html_url=it.get("html_url", ""),
                created_at=it.get("created_at", ""),
                updated_at=it.get("updated_at", ""),
                comments=it.get("comments", 0),
                assignees=[a["login"] for a in it.get("assignees", [])],
                user=(it.get("user") or {}).get("login", ""),
            )

    return list(seen.values())


def fetch_issue_comments(repo: str, issue_number: int, limit: int = 10) -> list[dict]:
    """Fetch the most recent comments on an issue. Used to detect 'I'll work on this'."""
    url = f"{GITHUB_API}/repos/{repo}/issues/{issue_number}/comments"
    try:
        comments = _get(url, params={"per_page": limit, "sort": "created", "direction": "desc"})
    except Exception as e:
        print(f"[github] Failed to fetch comments for {repo}#{issue_number}: {e}")
        return []
    if not isinstance(comments, list):
        return []
    return [
        {
            "user": (c.get("user") or {}).get("login", ""),
            "body": c.get("body", "") or "",
            "created_at": c.get("created_at", ""),
        }
        for c in comments
    ]


def is_issue_claimed(comments: list[dict], assignees: list[str]) -> bool:
    """Heuristic: is this issue likely already being worked on?

    Signals:
    - Has assignees (other than the issue author)
    - A comment in the last 30 days saying "I'll work on" / "working on this" / "assign me"
    """
    if assignees:
        return True
    claim_phrases = (
        "i'll work on", "i will work on", "i'm working on", "working on this",
        "assign me", "assign this to me", "/assign", "pr coming", "i'll take",
        "i'd like to work on", "may i work on", "can i take this",
    )
    cutoff = datetime.now(timezone.utc).timestamp() - 30 * 86400
    for c in comments:
        try:
            created = datetime.fromisoformat(c["created_at"].replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        if created < cutoff:
            continue
        body = (c.get("body") or "").lower()
        if any(p in body for p in claim_phrases):
            return True
    return False


def fetch_file_content(repo: str, path: str, ref: str = "main") -> Optional[str]:
    """Fetch raw file content from a repo. Returns None on 404."""
    url = f"https://raw.githubusercontent.com/{repo}/{ref}/{path}"
    try:
        resp = requests.get(url, headers={"User-Agent": "oss-contribution-agent"}, timeout=DEFAULT_TIMEOUT)
        if resp.status_code == 200:
            return resp.text
        if resp.status_code == 404 and ref == "main":
            # Try master as fallback
            return fetch_file_content(repo, path, ref="master")
        return None
    except requests.RequestException as e:
        print(f"[github] Failed to fetch {repo}:{path}: {e}")
        return None


def search_repo_files(repo: str, query: str, *, per_page: int = 5) -> list[dict]:
    """Search code in a specific repo. Useful for the draft mode to find related files.

    Note: GitHub code search requires authentication.
    """
    if not os.environ.get("GITHUB_TOKEN"):
        return []
    url = f"{GITHUB_API}/search/code"
    params = {"q": f"{query} repo:{repo}", "per_page": per_page}
    try:
        results = _get(url, params=params)
    except Exception as e:
        print(f"[github] Code search failed: {e}")
        return []
    if not isinstance(results, dict):
        return []
    return [
        {
            "path": item["path"],
            "url": item["html_url"],
            "score": item.get("score", 0),
        }
        for item in results.get("items", [])
    ]


def fetch_user_open_prs(username: str) -> list[dict]:
    """Fetch the user's currently-open PRs across all repos.

    Used by the 'review' mode to find PRs to critique.
    """
    url = f"{GITHUB_API}/search/issues"
    params = {
        "q": f"is:pr is:open author:{username}",
        "per_page": 20,
        "sort": "updated",
        "order": "desc",
    }
    try:
        results = _get(url, params=params)
    except Exception as e:
        print(f"[github] Failed to fetch user PRs: {e}")
        return []
    if not isinstance(results, dict):
        return []
    return [
        {
            "repo": "/".join(item["repository_url"].rsplit("/", 2)[-2:]),
            "number": item["number"],
            "title": item["title"],
            "url": item["html_url"],
            "state": item["state"],
            "created_at": item["created_at"],
            "updated_at": item["updated_at"],
        }
        for item in results.get("items", [])
    ]


def fetch_pr_diff(repo: str, pr_number: int) -> Optional[str]:
    """Fetch the unified diff for a PR. Used by the review mode."""
    url = f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}"
    headers = _headers()
    headers["Accept"] = "application/vnd.github.v3.diff"
    try:
        resp = requests.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
        if resp.status_code == 200:
            return resp.text
    except requests.RequestException as e:
        print(f"[github] Failed to fetch PR diff: {e}")
    return None


def fetch_user_merged_prs(username: str, since_days: int = 365) -> list[dict]:
    """Fetch the user's merged PRs in the last N days. Powers the dashboard."""
    url = f"{GITHUB_API}/search/issues"
    cutoff = (datetime.now(timezone.utc).timestamp() - since_days * 86400)
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).strftime("%Y-%m-%d")
    params = {
        "q": f"is:pr is:merged author:{username} merged:>={cutoff_iso}",
        "per_page": 30,
        "sort": "updated",
        "order": "desc",
    }
    try:
        results = _get(url, params=params)
    except Exception as e:
        print(f"[github] Failed to fetch merged PRs: {e}")
        return []
    if not isinstance(results, dict):
        return []
    return [
        {
            "repo": "/".join(item["repository_url"].rsplit("/", 2)[-2:]),
            "number": item["number"],
            "title": item["title"],
            "url": item["html_url"],
            "created_at": item["created_at"],
            "closed_at": item.get("closed_at", ""),
        }
        for item in results.get("items", [])
    ]
