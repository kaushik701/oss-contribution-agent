"""
state.py — SQLite-backed state for the contribution agent.

Tracks:
- Issues recommended (so we don't re-recommend the same one daily)
- PRs the user has opened (cross-referenced against dashboard data)
- Daily report archive

The DB lives at data/state.db and is committed by GitHub Actions on each run.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "data" / "state.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS recommended_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    first_recommended_utc TEXT NOT NULL,
    last_recommended_utc TEXT NOT NULL,
    times_recommended INTEGER NOT NULL DEFAULT 1,
    score REAL NOT NULL,
    user_action TEXT,  -- 'skipped' | 'drafted' | 'submitted' | 'merged' | NULL
    notes TEXT,
    UNIQUE(repo, issue_number)
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at_utc TEXT NOT NULL,
    mode TEXT NOT NULL,  -- 'daily' | 'manual' | 'review'
    n_issues_considered INTEGER,
    n_issues_reported INTEGER,
    n_drafts_generated INTEGER,
    n_prs_reviewed INTEGER,
    report_path TEXT
);

CREATE TABLE IF NOT EXISTS pr_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    reviewed_at_utc TEXT NOT NULL,
    likelihood_of_merge TEXT,
    UNIQUE(repo, pr_number, reviewed_at_utc)
);
"""


@contextmanager
def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_recently_recommended(*, days: int = 7) -> set[tuple[str, int]]:
    """Return (repo, issue_number) pairs recommended in the last N days, to deduplicate."""
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT repo, issue_number FROM recommended_issues WHERE last_recommended_utc >= ?",
            (cutoff_iso,),
        ).fetchall()
        return {(r["repo"], r["issue_number"]) for r in rows}


def record_recommendation(*, repo: str, issue_number: int, title: str, url: str, score: float):
    """Insert or update a recommendation record."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id, times_recommended FROM recommended_issues WHERE repo = ? AND issue_number = ?",
            (repo, issue_number),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE recommended_issues
                SET last_recommended_utc = ?, times_recommended = ?, score = ?
                WHERE id = ?
                """,
                (now, existing["times_recommended"] + 1, score, existing["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO recommended_issues
                  (repo, issue_number, title, url, first_recommended_utc, last_recommended_utc, score)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (repo, issue_number, title, url, now, now, score),
            )


def record_report(
    *,
    mode: str,
    n_considered: int,
    n_reported: int,
    n_drafts: int,
    n_reviews: int,
    report_path: Optional[str],
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO reports
              (generated_at_utc, mode, n_issues_considered, n_issues_reported, n_drafts_generated, n_prs_reviewed, report_path)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (datetime.now(timezone.utc).isoformat(), mode, n_considered, n_reported, n_drafts, n_reviews, report_path),
        )
        return cur.lastrowid


def record_pr_review(*, repo: str, pr_number: int, likelihood: str):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO pr_reviews (repo, pr_number, reviewed_at_utc, likelihood_of_merge)
            VALUES (?, ?, ?, ?)
            """,
            (repo, pr_number, datetime.now(timezone.utc).isoformat(), likelihood),
        )


def get_stats() -> dict:
    """Aggregate stats for the dashboard section of the report."""
    with get_conn() as conn:
        total_recs = conn.execute("SELECT COUNT(*) AS n FROM recommended_issues").fetchone()["n"]
        total_reports = conn.execute("SELECT COUNT(*) AS n FROM reports").fetchone()["n"]
        first_report = conn.execute("SELECT MIN(generated_at_utc) AS d FROM reports").fetchone()["d"]
    return {
        "total_issues_recommended": total_recs,
        "total_reports_generated": total_reports,
        "first_report_at": first_report,
    }
