# Setup Checklist

15 minutes from zero to first email. Follow in order.

If you already set up the AI Daily Tutor, the good news: **you can reuse most of the secrets**. Specifically:
- `GROQ_API_KEY` — same value
- `GMAIL_ADDRESS` — same value
- `GMAIL_APP_PASSWORD` — same value (Google allows reuse across multiple apps)
- `RECIPIENT_EMAIL` — same value

Only `GITHUB_TOKEN` is new and OSS-agent-specific.

---

## ✅ Step 1: Create the GitHub repo (3 min)

1. [github.com/new](https://github.com/new) → repo name: `oss-contribution-agent` → **Public** → Create
2. Locally:
   ```bash
   git init
   git add .
   git commit -m "feat: initial oss-contribution-agent v1"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/oss-contribution-agent.git
   git push -u origin main
   ```

## ✅ Step 2: Edit your profile.yaml (1 min)

**Important**: open `profile.yaml` and change:

```yaml
candidate:
  github_username: "YOUR_GITHUB_USERNAME"   # <-- your real GitHub handle
```

Without this, the PR review mode will be skipped (since it has no PRs to look up).

Commit and push:
```bash
git add profile.yaml && git commit -m "config: set github username" && git push
```

## ✅ Step 3: Generate a Gmail App Password (skip if you have one)

If you already have a Gmail App Password from the AI Daily Tutor setup, **reuse it** — skip to Step 4.

Otherwise:

### 3.1 Make sure 2-Step Verification is on

App Passwords require 2FA. Visit [myaccount.google.com/signinoptions/two-step-verification](https://myaccount.google.com/signinoptions/two-step-verification) and enable it if needed (~2 min, needs your phone).

### 3.2 Generate the App Password

1. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
2. **App name**: `oss-contribution-agent` (or reuse the one named `ai-daily-tutor` — either works)
3. Click **Create**
4. **Copy the 16-character password** (looks like `abcd efgh ijkl mnop`)

⚠️ This is NOT your normal Gmail password.

## ✅ Step 4: Get a GitHub Personal Access Token (3 min)

This lifts the GitHub API rate limit from 60 req/hr to 5,000 req/hr — necessary because we scan 18 repos.

1. [github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new) → **Generate new token (fine-grained)**
2. Token name: `oss-contribution-agent`
3. Expiration: 1 year (set a calendar reminder to rotate)
4. Repository access: **Public Repositories (read-only)**
5. Permissions: leave everything at defaults (no extra perms needed)
6. **Generate token** → **copy the `ghp_...` token**

## ✅ Step 5: Add GitHub Secrets (3 min)

In your repo: **Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
|---|---|
| `GROQ_API_KEY` | `gsk_...` (reuse from AI Daily Tutor if you have one) |
| `GMAIL_ADDRESS` | Your full Gmail address |
| `GMAIL_APP_PASSWORD` | The 16-char App Password from Step 3 (spaces optional) |
| `RECIPIENT_EMAIL` | Where to deliver (usually same as `GMAIL_ADDRESS`) |
| `GITHUB_TOKEN` | The `ghp_...` token from Step 4 |

Optional: `SENDER_NAME` (display name in From header, defaults to "OSS Contribution Scout").

> **Important note about `GITHUB_TOKEN`**: GitHub auto-provides a built-in token to Actions runs, but the built-in token has a 1000/hr rate limit and is scoped only to *your* repo. We need to scan public repos owned by others, so we use our own PAT with the higher 5000/hr limit. The Actions runner will prefer your secret if both exist.

## ✅ Step 6: Enable Actions and run a test (3 min)

1. **Actions** tab → enable workflows if prompted
2. Click **Daily OSS Scout** in the left sidebar
3. **Run workflow** → set `dry_run` to `true` for the first test → **Run workflow**
4. Wait ~30-60 seconds, refresh, click into the run, watch the logs

A successful dry run looks like:
```
[main] Scanning langchain-ai/langchain (priority=high)...
[main] Scanning langchain-ai/langgraph (priority=high)...
...
[main] Considered 47 issues; 4 passed all filters.
[main] Stage 2: LLM analysis...
[main] Stage 3: generating code patches...
[main] Stage 4: fetching PRs and generating reviews...
[main] Archived to examples/reports/2026-05-25_report.md and ...
[main] --dry-run set; skipping send.
```

Open the committed `examples/reports/2026-05-25_report.md` file in the repo — that's your report.

## ✅ Step 7: Run for real

Run the workflow again with `dry_run = false`. The email should land in your inbox in ~60 seconds.

Tomorrow at 8 AM PT, it'll run automatically.

---

## Troubleshooting

**Gmail SMTP authentication failed**: 90% of cases. See AI Daily Tutor's SETUP.md troubleshooting section — identical fixes apply here.

**"No issues passed all filters"**: Normal on some days. AI/ML projects don't always have new beginner-friendly issues every morning. Wait 24 hours.

**`KeyError: 'GROQ_API_KEY'`**: Secret name is wrong. Copy-paste exactly.

**`403 rate limit exceeded` from GitHub**: Either your `GITHUB_TOKEN` is missing or you've exhausted the unauthenticated 60/hr quota. Make sure you added the token from Step 4.

**Reviews are empty even though I have open PRs**: Did you set `github_username` in `profile.yaml`? Make sure it's your exact handle, no `@` prefix.

**Drafts are weak / hallucinate file paths**: Llama 3.3 70B is good but not as strong as Claude/GPT for code. Try:
  - Set `GROQ_MODEL` secret to `qwen-2.5-32b`
  - Or use the `--skip-drafts` flag — the issue analysis is what matters most

**I want to add/remove a repo**: edit `watchlist.yaml`, commit, push.

**I want to change the schedule**: edit `.github/workflows/scout.yml`, change the two `cron:` lines (UTC).

---

## How to actually use the daily reports

**Morning (8:00–8:45 AM PT)**:
1. Read the email over coffee
2. Pick ONE issue from "Today's Picks" — usually the highest-scoring one with no risk flags
3. Open the linked issue on GitHub, read it fully, read the comments
4. If the agent flagged "ask maintainer first" questions, **comment on the issue** asking those questions; move on, come back tomorrow

**During the work block (anytime)**:
1. If the agent generated a draft patch, open `examples/drafts/...` and read every line
2. Clone the OSS repo locally, apply the patch manually (don't just copy-paste blindly)
3. Run the project's test suite
4. Verify the patch matches the project's style by looking at 2-3 recent merged PRs in the repo
5. If it all looks good, push a branch and open a PR with **your own commit message**

**Evening (optional)**:
1. Check the PR review section of tomorrow's report — if you have open PRs, you'll get a critique

Over 30 days you should submit 8-15 PRs and get 3-6 merged. Worth more on a resume than any side project.
