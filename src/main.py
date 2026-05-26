"""
main.py — Entry point invoked by the GitHub Actions cron at 8 AM PT.

Pipeline:
  1. Fetch the user's open + recently merged PRs (for dashboard + review mode)
  2. For each repo in the watchlist, fetch open issues matching good labels
  3. Score every issue deterministically
  4. Take top N candidates
  5. For each candidate: LLM-analyze (always) + LLM-draft (top 2 only)
  6. (Optional review mode) Critique up to N of the user's open PRs
  7. Render HTML email + Markdown archive
  8. Send via Resend, commit archive + state DB

CLI flags:
  --dry-run        : do everything except send the email
  --skip-drafts    : analyze but don't generate code patches (cheaper run)
  --skip-reviews   : skip the PR review mode
  --mode=scout     : just find issues (no drafts, no reviews)
  --mode=full      : default — scout + drafts + reviews
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime, timezone
from typing import Optional

from .analyzer import analyze_issue, draft_patch, review_pr, IssueAnalysis, CodePatch
from .config import load_profile, load_watchlist, RepoConfig
from .github_client import (
    Issue,
    fetch_file_content,
    fetch_issue_comments,
    fetch_open_issues,
    fetch_pr_diff,
    fetch_user_merged_prs,
    fetch_user_open_prs,
    is_issue_claimed,
)
from .reporter import (
    archive_draft,
    archive_report,
    format_draft_markdown,
    render_html,
    render_markdown,
    report_to_plain_text,
    send_email,
)
from .scoring import ScoredIssue, rank_issues, score_issue
from .state import (
    get_recently_recommended,
    get_stats,
    record_pr_review,
    record_recommendation,
    record_report,
)


# ─── Pipeline stages ───────────────────────────────────────────────────────


def collect_candidates(watchlist, profile) -> tuple[list[ScoredIssue], dict]:
    """Stage 1: pull issues + score them. Returns (ranked, telemetry)."""
    all_scored: list[ScoredIssue] = []
    repos_scanned = 0
    candidates_seen = 0
    recently_recommended = get_recently_recommended(days=7)

    for repo_cfg in watchlist.repositories:
        print(f"[main] Scanning {repo_cfg.repo} (priority={repo_cfg.priority})...")
        issues = fetch_open_issues(repo_cfg.repo, repo_cfg.good_labels)
        repos_scanned += 1
        candidates_seen += len(issues)

        for issue in issues:
            # Skip if label hit a skip-list
            if set(l.lower() for l in issue.labels) & set(l.lower() for l in repo_cfg.skip_labels):
                continue
            # Skip duplicates from the last 7 days
            if (repo_cfg.repo, issue.number) in recently_recommended:
                continue

            scored = score_issue(
                issue,
                repo_priority=repo_cfg.priority,
                weights=watchlist.weights,
                skills_profile=profile.skills,
                exclusions=profile.exclusions,
            )
            all_scored.append(scored)

    max_results = int(watchlist.scoring.get("max_issues_per_report", 5))
    top = rank_issues(all_scored, max_results=max_results * 3, min_score=2.0)

    # Now check for claim status on the top candidates (one API call each, expensive).
    confirmed: list[ScoredIssue] = []
    for s in top:
        comments = fetch_issue_comments(s.issue.repo, s.issue.number, limit=10)
        if is_issue_claimed(comments, s.issue.assignees):
            print(f"[main] Skipping {s.issue.repo}#{s.issue.number} — appears claimed.")
            continue
        # Attach comments so we don't refetch in analysis
        s.issue.body = s.issue.body  # noop, just to be explicit we're not mutating
        s.flag_reasons.append(f"Comments fetched: {len(comments)}")
        confirmed.append(s)
        if len(confirmed) >= max_results:
            break

    telemetry = {
        "considered": candidates_seen,
        "repos_scanned": repos_scanned,
        "after_filter": len(confirmed),
    }
    return confirmed, telemetry


def analyze_picks(picks: list[ScoredIssue], repo_notes_map: dict[str, str]) -> list[dict]:
    """Stage 2: LLM-analyze each pick. Returns a list of pick dicts for the template."""
    analyzed: list[dict] = []
    for s in picks:
        issue = s.issue
        # Refetch comments — small cost, big benefit (analyzer needs them).
        comments = fetch_issue_comments(issue.repo, issue.number, limit=5)
        try:
            analysis: IssueAnalysis = analyze_issue(
                repo=issue.repo,
                number=issue.number,
                title=issue.title,
                body=issue.body,
                labels=issue.labels,
                url=issue.html_url,
                comments=comments,
                repo_notes=repo_notes_map.get(issue.repo, ""),
            )
        except Exception as e:
            print(f"[main] Analysis failed for {issue.repo}#{issue.number}: {e}")
            # Fall through with stub analysis so the report still renders
            analysis = IssueAnalysis(
                plain_english_summary="(Analysis failed; see raw issue.)",
                why_it_exists="(Analysis failed)",
                suggested_approach="(Analysis failed — read the issue directly)",
                files_likely_involved=[],
                questions_to_ask=[],
                estimated_time="unknown",
                risk_flags=[f"Agent analysis failed: {str(e)[:200]}"],
            )

        analyzed.append({
            "scored": s,
            "issue": issue,
            "analysis": analysis,
        })
    return analyzed


def generate_drafts(analyzed: list[dict], n_drafts: int) -> list[Optional[CodePatch]]:
    """Stage 3: generate code patches for the top N picks. Returns aligned list (None for skipped)."""
    drafts: list[Optional[CodePatch]] = []
    for i, item in enumerate(analyzed):
        if i >= n_drafts:
            drafts.append(None)
            continue

        issue = item["issue"]
        analysis: IssueAnalysis = item["analysis"]

        # Skip drafting if the analysis flagged it as needing maintainer discussion first
        if any("maintainer" in q.lower() or "approval" in q.lower() for q in analysis.questions_to_ask):
            print(f"[main] Skipping draft for {issue.repo}#{issue.number} — needs maintainer discussion first.")
            drafts.append(None)
            continue
        if analysis.estimated_time and "day" in analysis.estimated_time.lower():
            print(f"[main] Skipping draft for {issue.repo}#{issue.number} — estimated multi-day work.")
            drafts.append(None)
            continue

        # Fetch up to 3 of the likely-involved files
        file_snippets: dict[str, str] = {}
        for path in analysis.files_likely_involved[:3]:
            content = fetch_file_content(issue.repo, path)
            if content:
                file_snippets[path] = content

        try:
            patch = draft_patch(
                repo=issue.repo,
                number=issue.number,
                title=issue.title,
                body=issue.body,
                url=issue.html_url,
                file_snippets=file_snippets,
                repo_notes="",
            )
            drafts.append(patch)
        except Exception as e:
            print(f"[main] Draft failed for {issue.repo}#{issue.number}: {e}")
            drafts.append(None)

    return drafts


def review_user_prs(username: str, max_reviews: int = 3) -> list[dict]:
    """Stage 4: critique the user's open PRs. Returns list of review dicts for template."""
    if not username or username == "YOUR_GITHUB_USERNAME":
        print("[main] Skipping PR reviews — github_username not set in profile.yaml.")
        return []

    open_prs = fetch_user_open_prs(username)
    if not open_prs:
        print("[main] No open PRs to review.")
        return []

    reviews: list[dict] = []
    for pr in open_prs[:max_reviews]:
        try:
            diff = fetch_pr_diff(pr["repo"], pr["number"]) or ""
            if not diff:
                continue
            review = review_pr(
                repo=pr["repo"],
                number=pr["number"],
                title=pr["title"],
                url=pr["url"],
                diff=diff,
            )
            reviews.append({
                "repo": pr["repo"],
                "number": pr["number"],
                "title": pr["title"],
                "url": pr["url"],
                "assessment": review.overall_assessment,
                "strengths": review.strengths,
                "concerns": review.concerns,
                "suggestions": review.suggested_changes,
                "likelihood": review.likelihood_of_merge,
            })
            record_pr_review(repo=pr["repo"], pr_number=pr["number"], likelihood=review.likelihood_of_merge)
        except Exception as e:
            print(f"[main] PR review failed for {pr['repo']}#{pr['number']}: {e}")
            traceback.print_exc()

    return reviews


# ─── Compose context for templates ─────────────────────────────────────────


def build_context(
    *,
    analyzed: list[dict],
    drafts: list[Optional[CodePatch]],
    open_prs: list[dict],
    merged_prs: list[dict],
    reviews: list[dict],
    telemetry: dict,
    github_repo: str,
) -> dict:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    picks: list[dict] = []

    for i, item in enumerate(analyzed):
        s: ScoredIssue = item["scored"]
        issue: Issue = item["issue"]
        a: IssueAnalysis = item["analysis"]
        patch: Optional[CodePatch] = drafts[i] if i < len(drafts) else None

        # Score breakdown summary (top 2 components by magnitude)
        comps = sorted(s.components.items(), key=lambda kv: -abs(kv[1]))[:3]
        score_breakdown = ", ".join(f"{k}={v:+.1f}" for k, v in comps if v != 0)

        draft_path: Optional[str] = None
        if patch and patch.files_changed:
            md = format_draft_markdown(
                repo=issue.repo,
                issue_number=issue.number,
                issue_url=issue.html_url,
                title=issue.title,
                patch=patch,
            )
            draft_path = archive_draft(repo=issue.repo, issue_number=issue.number, content=md)

        picks.append({
            "repo": issue.repo,
            "number": issue.number,
            "title": issue.title,
            "url": issue.html_url,
            "score": s.score,
            "score_breakdown": score_breakdown,
            "estimated_time": a.estimated_time,
            "summary": a.plain_english_summary,
            "why_it_exists": a.why_it_exists,
            "approach": a.suggested_approach,
            "files": a.files_likely_involved,
            "questions": a.questions_to_ask,
            "risk_flags": a.risk_flags + s.flag_reasons,
            "skill_matches": s.skill_matches,
            "has_draft": draft_path is not None,
            "draft_path": draft_path or "",
        })

        # Record in DB
        record_recommendation(
            repo=issue.repo,
            issue_number=issue.number,
            title=issue.title,
            url=issue.html_url,
            score=s.score,
        )

    stats = {
        "considered": telemetry["considered"],
        "repos_scanned": telemetry["repos_scanned"],
        "drafts": sum(1 for d in drafts if d and d.files_changed),
        "reviews": len(reviews),
    }

    persistent = get_stats()
    dashboard = {
        "open_prs": len(open_prs),
        "merged_prs": len(merged_prs),
        "total_reports": (persistent.get("total_reports_generated") or 0) + 1,
    }

    headline = f"{len(picks)} issue{'s' if len(picks) != 1 else ''} matched today"
    if reviews:
        headline += f" • {len(reviews)} PR review{'s' if len(reviews) != 1 else ''}"

    return {
        "date": date_str,
        "subject": f"OSS Scout {date_str}: {headline}",
        "headline": headline,
        "stats": stats,
        "dashboard": dashboard,
        "open_prs": open_prs,
        "picks": picks,
        "reviews": reviews,
        "github_repo": github_repo,
    }


# ─── Entry point ───────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Generate but don't send email")
    parser.add_argument("--skip-drafts", action="store_true", help="Skip code patch generation")
    parser.add_argument("--skip-reviews", action="store_true", help="Skip PR review mode")
    parser.add_argument("--mode", choices=["scout", "full"], default="full")
    args = parser.parse_args()

    recipient = os.environ.get("RECIPIENT_EMAIL")
    if not recipient:
        print("ERROR: RECIPIENT_EMAIL env var not set.", file=sys.stderr)
        return 1

    github_repo = os.environ.get("GITHUB_REPOSITORY", "your-username/oss-contribution-agent")

    print("[main] Loading config...")
    watchlist = load_watchlist()
    profile = load_profile()
    repo_notes = {r.repo: r.notes for r in watchlist.repositories}

    print("[main] Stage 1: collecting candidates...")
    picks, telemetry = collect_candidates(watchlist, profile)
    print(f"[main]   Considered {telemetry['considered']} issues; {len(picks)} passed all filters.")

    print("[main] Stage 2: LLM analysis...")
    analyzed = analyze_picks(picks, repo_notes)

    drafts: list[Optional[CodePatch]] = [None] * len(analyzed)
    if not args.skip_drafts and args.mode == "full":
        print("[main] Stage 3: generating code patches...")
        n_drafts = int(watchlist.scoring.get("draft_top_n", 2))
        drafts = generate_drafts(analyzed, n_drafts=n_drafts)

    open_prs = []
    merged_prs = []
    reviews = []
    if not args.skip_reviews and args.mode == "full":
        print("[main] Stage 4: fetching PRs and generating reviews...")
        open_prs = fetch_user_open_prs(profile.github_username)
        merged_prs = fetch_user_merged_prs(profile.github_username, since_days=365)
        reviews = review_user_prs(profile.github_username, max_reviews=3)

    print("[main] Stage 5: building report...")
    context = build_context(
        analyzed=analyzed,
        drafts=drafts,
        open_prs=open_prs,
        merged_prs=merged_prs,
        reviews=reviews,
        telemetry=telemetry,
        github_repo=github_repo,
    )

    html = render_html(context)
    md = render_markdown(context)
    md_path, html_path = archive_report(html, md)
    plain = report_to_plain_text(context)
    print(f"[main] Archived to {md_path} and {html_path}")

    if args.dry_run:
        print("[main] --dry-run set; skipping send.")
        record_report(
            mode=args.mode, n_considered=telemetry["considered"],
            n_reported=len(context["picks"]), n_drafts=context["stats"]["drafts"],
            n_reviews=len(reviews), report_path=md_path,
        )
        return 0

    result = send_email(
        to=recipient,
        subject=context["subject"],
        html=html,
        plain_text=plain,
    )
    print(f"[main] Resend response: {result}")

    record_report(
        mode=args.mode, n_considered=telemetry["considered"],
        n_reported=len(context["picks"]), n_drafts=context["stats"]["drafts"],
        n_reviews=len(reviews), report_path=md_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
