"""
reporter.py — Render the daily report to HTML + Markdown and send via Gmail SMTP.

The Markdown version is committed to the repo for a public archive recruiters can
browse. The HTML version goes to the user's inbox at 8 AM PT.
"""

from __future__ import annotations

import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).parent / "templates"
REPORTS_DIR = Path(__file__).parent.parent / "examples" / "reports"
DRAFTS_DIR = Path(__file__).parent.parent / "examples" / "drafts"

# Gmail SMTP settings (fixed; no configuration needed)
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html"]),
)


def render_html(context: dict) -> str:
    return _env.get_template("report.html.j2").render(**context)


def render_markdown(context: dict) -> str:
    return _env.get_template("report.md.j2").render(**context)


def archive_report(html: str, markdown: str) -> tuple[str, str]:
    """Save HTML + Markdown copies. Returns (md_path, html_path) relative to repo root."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base = REPORTS_DIR / f"{date_str}_report"
    md_path = Path(str(base) + ".md")
    html_path = Path(str(base) + ".html")
    md_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(html, encoding="utf-8")
    repo_root = Path(__file__).parent.parent
    return (
        str(md_path.relative_to(repo_root)),
        str(html_path.relative_to(repo_root)),
    )


def archive_draft(*, repo: str, issue_number: int, content: str) -> str:
    """Save a draft patch. Returns path relative to repo root."""
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    safe_repo = repo.replace("/", "_")
    filename = f"{date_str}_{safe_repo}_{issue_number}.md"
    path = DRAFTS_DIR / filename
    path.write_text(content, encoding="utf-8")
    repo_root = Path(__file__).parent.parent
    return str(path.relative_to(repo_root))


def format_draft_markdown(*, repo: str, issue_number: int, issue_url: str, title: str, patch) -> str:
    """Format a CodePatch as a reviewable Markdown document."""
    lines = [
        f"# Draft patch for {repo}#{issue_number}",
        "",
        f"**Issue**: [{title}]({issue_url})",
        "",
        f"**Generated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Approach",
        "",
        patch.approach_summary,
        "",
        "## Files changed",
        "",
    ]
    for f in patch.files_changed:
        lines += [
            f"### `{f.get('path', 'unknown')}` ({f.get('change_type', 'modify')})",
            "",
            "```python",
            (f.get("content_or_diff") or "").strip(),
            "```",
            "",
        ]
    if patch.tests_to_add:
        lines += ["## Tests to add", "", patch.tests_to_add, ""]
    lines += [
        "## Review checklist (before submitting)",
        "",
    ]
    for item in patch.review_checklist:
        lines.append(f"- [ ] {item}")
    if patch.caveats:
        lines += ["", "## Caveats / assumptions", ""]
        for c in patch.caveats:
            lines.append(f"- {c}")
    lines += [
        "",
        "---",
        "",
        "> ⚠️ This patch was drafted by an LLM. Before submitting:",
        "> 1. Read every line of the diff.",
        "> 2. Run the project's test suite locally.",
        "> 3. Verify the issue is still open and unclaimed.",
        "> 4. Verify maintainer-approved approach (required for LangChain).",
        "> 5. Match your commit + PR style to recent merged PRs in the repo.",
    ]
    return "\n".join(lines)


def send_email(*, to: str, subject: str, html: str, plain_text: str, sender: Optional[str] = None) -> dict:
    """Send an HTML+text email via Gmail SMTP.

    Required env vars:
      - GMAIL_ADDRESS: your full Gmail address (e.g. you@gmail.com)
      - GMAIL_APP_PASSWORD: a 16-character Gmail App Password (NOT your normal password)

    Optional env vars:
      - SENDER_NAME: display name for the From header (defaults to "OSS Contribution Scout")

    Returns a dict with {status, message_id, to} for parity with the previous
    Resend interface so calling code doesn't change.
    """
    gmail_address = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not gmail_address:
        raise RuntimeError("GMAIL_ADDRESS env var is required.")
    if not app_password:
        raise RuntimeError("GMAIL_APP_PASSWORD env var is required.")

    # Strip whitespace; Gmail App Passwords are often shown with spaces every 4 chars
    # (e.g. "abcd efgh ijkl mnop"). Both forms work, but stripping is safer.
    app_password = app_password.replace(" ", "").strip()

    sender_name = os.environ.get("SENDER_NAME", "OSS Contribution Scout")
    from_header = sender or formataddr((sender_name, gmail_address))

    msg = MIMEMultipart("alternative")
    msg["From"] = from_header
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="gmail.com")

    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        try:
            server.login(gmail_address, app_password)
        except smtplib.SMTPAuthenticationError as e:
            raise RuntimeError(
                "Gmail SMTP authentication failed. Common causes:\n"
                "  1. GMAIL_APP_PASSWORD is your normal Gmail password, not a 16-char App Password.\n"
                "     Generate one at https://myaccount.google.com/apppasswords\n"
                "  2. 2-Step Verification is not enabled on the Google account.\n"
                "     App Passwords require 2FA to be on.\n"
                "  3. GMAIL_ADDRESS doesn't match the account the App Password was generated for.\n"
                f"Original error: {e}"
            ) from e
        server.sendmail(gmail_address, [to], msg.as_string())

    return {
        "status": "sent",
        "message_id": msg["Message-ID"],
        "to": to,
    }


def report_to_plain_text(context: dict) -> str:
    """Quick plain-text fallback. Less effort than a full template."""
    parts = [
        f"OSS Contribution Scout — {context['date']}",
        context['headline'],
        "",
        f"Considered {context['stats']['considered']} issues across {context['stats']['repos_scanned']} repos.",
        f"Drafts generated: {context['stats']['drafts']}",
        "",
    ]
    if context.get("picks"):
        parts.append("=" * 50)
        parts.append("TODAY'S PICKS")
        parts.append("=" * 50)
        for i, p in enumerate(context["picks"], 1):
            parts += [
                "",
                f"#{i}. {p['repo']} #{p['number']} (score {p['score']:.1f})",
                f"    {p['title']}",
                f"    {p['url']}",
                "",
                f"    Summary: {p['summary']}",
                f"    Approach: {p['approach']}",
            ]
    else:
        parts.append("No suitable issues found today.")
    parts += ["", "Full report archived in the repo."]
    return "\n".join(parts)
