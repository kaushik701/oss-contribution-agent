# OSS Contribution Scout

> A human-in-the-loop AI agent that finds high-quality open-source contribution opportunities in the AI/ML ecosystem, scores them against my skill profile, drafts code patches for the top picks, and reviews my own open PRs before I ask for maintainer review — all delivered as a single daily email at 8 AM PT.

[![Daily Scout](https://github.com/USERNAME/oss-contribution-agent/actions/workflows/scout.yml/badge.svg)](https://github.com/USERNAME/oss-contribution-agent/actions/workflows/scout.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## What this does (and explicitly does not do)

**It does**:
- Polls a curated watchlist of ~18 AI/ML projects (LangChain, LlamaIndex, FastMCP, MCP SDK, DeepEval, Chroma, etc.) for new issues every morning
- Scores each issue using a deterministic, explainable rubric (skill match + recency + label quality + effort estimate)
- Drafts code patches for the top 2 picks using Llama 3.3 70B via Groq
- Reviews my currently-open PRs and tells me what a maintainer is likely to flag before they do
- Emails me a single ranked report at 8 AM PT and commits the same report to this repo for a public archive

**It explicitly does NOT**:
- ❌ Submit PRs on my behalf
- ❌ Comment on issues
- ❌ Interact with maintainers in any way
- ❌ Auto-apply patches

Every PR I submit to an OSS project is **read, tested, and authored by me personally.** The agent is a research and drafting assistant, not a contribution bot.

## Why human-in-the-loop is the only ethical design

OSS maintainers in 2026 are explicitly hostile to AI-generated drive-by PRs. LangChain's contributing guide is explicit:

> *"All pull requests must link to an issue or discussion where a solution has been approved by a maintainer."*
> *"Low-effort drive-by contributions—regardless of how they are produced—often miss the mark in terms of contextual relevance, accuracy, and quality."*

So this agent treats maintainer norms as first-class constraints. It never drafts a patch for an issue whose suggested approach hasn't been blessed by the maintainer when the project requires it. It surfaces "ask the maintainer this first" questions before code. And the human owns every submission.

## Architecture

```
┌────────────────────────────┐
│  GitHub Actions cron       │  15:00 + 16:00 UTC (8 AM PT, DST-safe)
└─────────────┬──────────────┘
              │
              ▼
┌────────────────────────────┐    ┌──────────────────────────┐
│  src/main.py (orchestrator)│───▶│  watchlist.yaml (18 repos)│
│                            │    └──────────────────────────┘
│                            │    ┌──────────────────────────┐
│                            │───▶│  profile.yaml (skills)   │
│                            │    └──────────────────────────┘
└─────────────┬──────────────┘
              │
              ├─▶ github_client.py ──▶ GitHub REST API (read-only)
              │       ├─ fetch open issues by label
              │       ├─ fetch issue comments (claim detection)
              │       ├─ fetch user's open PRs (review mode)
              │       └─ fetch raw file contents (draft mode)
              │
              ├─▶ scoring.py ──▶ deterministic ranker
              │       ├─ label boost + recency + repo priority
              │       ├─ skill match against profile.yaml
              │       ├─ exclusion check (TS-only, Rust core, etc.)
              │       └─ effort estimation
              │
              ├─▶ analyzer.py ──▶ Groq (Llama 3.3 70B)
              │       ├─ analyze_issue() — plain-English summary + approach
              │       ├─ draft_patch() — full code patch for top 2 picks
              │       └─ review_pr() — critique user's own open PRs
              │
              ├─▶ state.py (SQLite) ──▶ data/state.db
              │       ├─ dedup: don't re-recommend same issue within 7d
              │       └─ aggregate stats for dashboard
              │
              └─▶ reporter.py ──▶ HTML email + Markdown archive
                      ├─ send via Gmail SMTP → Gmail
                      ├─ commit to examples/reports/YYYY-MM-DD_report.md
                      └─ commit drafts to examples/drafts/...
```

## The three modes

The agent supports three modes via the `--mode` flag:

| Mode | What it does | When to use |
|---|---|---|
| `full` (default) | Scout + Draft + Review — everything | Daily 8 AM cron |
| `scout` | Find and analyze issues, no code patches, no PR reviews | Cheaper run; pre-scouting before a long work block |
| `--skip-drafts` | Scout + Review but no code patches | When you want lighter reading |
| `--skip-reviews` | Scout + Draft but skip PR critiques | When you have no open PRs |

## The watchlist

18 projects across 6 tiers:

| Tier | Examples |
|---|---|
| AI agents / orchestration | LangChain, LangGraph, LlamaIndex |
| MCP ecosystem | FastMCP, MCP Python SDK, MCP TypeScript SDK, reference servers |
| RAG / vector DBs | Chroma, Qdrant Python client, Weaviate Python client |
| Evals / observability | DeepEval, Inspect AI, Langfuse, Arize Phoenix |
| Model serving | vLLM, Transformers |
| Adjacent tooling | Pydantic AI, Promptfoo |

Each repo has its own etiquette notes (e.g., FastMCP rejects out-of-scope features; LangChain requires maintainer pre-approval). Fully editable in [`watchlist.yaml`](watchlist.yaml).

## The skill profile

Skills are bucketed into four confidence tiers (`proficient` / `intermediate` / `learning` / `awareness_only`) with associated weights. Exclusions (TypeScript-only, Rust core, frontend, mobile) are hard rejects. See [`profile.yaml`](profile.yaml).

## Scoring rubric

Every issue gets a score with this breakdown:

| Component | Range | Source |
|---|---|---|
| Repo priority | 0.5 – 2.0 | watchlist.yaml |
| Label match | 0 or 3.0 | "good first issue", "help wanted", "documentation" |
| Recency | 0 – 2.0 | Updated within 14 days = full credit |
| No existing PR | 0 or 2.0 | Reduces duplicate work |
| Skill match | 0 – ~6.0 | Matches against profile.yaml, weighted by confidence tier |
| Exclusion penalty | 0 or -10 | TS-only / Rust / frontend = effectively skip |
| Effort adjustment | -1.5 to +1.0 | Bias toward "well-documented bug fixes", away from RFCs and typos |

The full breakdown shows up in every recommendation — no black-box magic.

## Sample output

Every daily email + committed report looks like [`examples/reports/`](examples/reports/) — full Markdown archive of every scouting run.

## Roadmap to v2 (MCP architecture)

Identical pattern to the [AI Daily Tutor](https://github.com/USERNAME/ai-daily-tutor) v2 plan: refactor the scoring + analysis layer into a custom FastMCP server (`oss-scout-mcp`) exposing:

- Resources: `watchlist://repos`, `state://recommendations/{n}`, `dashboard://stats`
- Tools: `score_issue(repo, number)`, `draft_patch(issue_id)`, `review_pr(repo, pr)`
- Prompts: `daily_scout`, `pr_review`

Add DeepEval scoring on the agent's recommendations (was the pick actually good? = the user submitted within 14 days), and Langfuse traces for every LLM call.

## Setup

See [`SETUP.md`](SETUP.md) — the 15-minute checklist.

## Cost

| Component | Cost |
|---|---|
| Groq API (Llama 3.3 70B, ~5 issues × 2 LLM calls × ~1500 tokens) | $0 (free tier) |
| GitHub API | $0 (5000 reqs/hr with token; we use ~30/day) |
| Gmail SMTP | $0 (free, no quota for self-emails) |
| GitHub Actions | $0 (~2 min/day vs 2000 min/month free tier) |
| **Total** | **$0** |

## Security model

- **Read-only GitHub token**: the GITHUB_TOKEN scope is `public_repo` read only. Cannot create issues, comments, or PRs even if compromised.
- **No write to OSS repos**: the agent has no code path that calls `POST /repos/.../issues` or `POST /repos/.../pulls`. Even if a future bug introduced one, the token lacks the scope.
- **Secrets in GitHub Secrets only**: never in code or commit history.
- **Recipient hardcoded**: `RECIPIENT_EMAIL` is a single Gmail address; the agent has no way to send anywhere else.
- **Workflow scope**: `contents: write` only on this repo, for committing the archive.

## What I'd do with another month

- Build the v2 MCP architecture above
- Add a DeepEval suite that scores recommendations against my submission history (precision @ N over 30 days)
- Cross-encode the issue + my profile for better skill matching
- Track time-to-merge on PRs I submit from agent recommendations vs. baseline
- Public dashboard at `scout.kaushik.dev` showing the live PR funnel

## License

MIT
