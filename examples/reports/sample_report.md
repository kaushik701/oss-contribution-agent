# OSS Contribution Scout Report — 2026-05-26

> 3 issues matched • 1 PR review

## Summary

- **Issues considered**: 67 across 18 repos
- **Issues recommended**: 2
- **Drafts generated**: 2
- **PRs reviewed**: 1


## Your Contribution Dashboard

| Metric | Value |
|---|---|
| Open PRs | 1 |
| Merged PRs (1y) | 0 |
| Total reports sent | 1 |



## Your Open PRs

- [jlowin/fastmcp#1234](https://github.com/jlowin/fastmcp/pull/1234) — *Fix: handle None in tool argument validation* — updated 2026-05-25



## Today's Picks




### Pick #1 — [confident-ai/deepeval#1547](https://github.com/confident-ai/deepeval/issues/1547)

**FaithfulnessMetric raises on empty retrieval_context**

- **Score**: 9.7
- **Estimated time**: 2-4 hours
- **Score breakdown**: skill_match=+4.2, label_match=+3.0, recent_activity=+2.0

**Summary**: FaithfulnessMetric crashes with IndexError when retrieval_context is an empty list. Should either return 0.0 with a warning or raise a clear ValueError.

**Why it exists**: The metric code assumes at least one context chunk exists and indexes into it without checking. RAG pipelines that filter out all results legitimately hit this.

**Suggested approach**:
1. Reproduce locally with empty list
2. Add guard clause in metric.measure()
3. Add test covering both empty list and None
4. Update docstring to mention the behavior


**Files likely involved**:

- `deepeval/metrics/faithfulness/faithfulness.py`

- `tests/test_faithfulness.py`








**Matched skills**: Python (proficient), evals (intermediate), DeepEval (intermediate)



**📝 Draft patch available**: [`examples/drafts/2026-05-26_confident-ai_deepeval_1547.md`](examples/drafts/2026-05-26_confident-ai_deepeval_1547.md)


---

### Pick #2 — [langchain-ai/langchain#28453](https://github.com/langchain-ai/langchain/issues/28453)

**RecursiveCharacterTextSplitter: incorrect chunk_overlap behavior with separators**

- **Score**: 6.2
- **Estimated time**: half-day
- **Score breakdown**: skill_match=+3.5, label_match=+3.0, no_existing_pr=+2.0

**Summary**: When separators is a custom list, chunk_overlap is applied inconsistently.

**Why it exists**: Edge case in the recursive splitting logic when no separator matches near the chunk boundary.

**Suggested approach**:
1. Comment on issue asking for maintainer-approved approach BEFORE coding
2. Once approved: add failing test
3. Fix boundary logic in _split_text


**Files likely involved**:

- `libs/text-splitters/langchain_text_splitters/character.py`




**Questions to ask the maintainer first**:

- What is the expected behavior when no separator falls within the overlap window?




**⚠️ Risk flags**:

- LangChain requires maintainer pre-approval; do NOT draft without it




**Matched skills**: Python (proficient), LangChain (proficient)




---



## Reviews of Your Open PRs


### [jlowin/fastmcp#1234](https://github.com/jlowin/fastmcp/pull/1234) — Fix: handle None in tool argument validation

**Assessment**: The fix is on the right track but test coverage is thin. Likelihood of merge with current state is medium; with suggested changes, high.

**Likelihood of merge**: medium


**Strengths**:

- Minimal change scope

- Includes a test for the new behavior




**Concerns**:

- Test only covers None case; missing test for empty dict case




**Suggested changes**:

- Add parametrized test with both None and {} as inputs

- Improve error message to include tool name



---



## How this report was generated

This report was produced by an autonomous agent running on GitHub Actions. The agent:

1. Polls a curated [watchlist of AI/ML projects](../../watchlist.yaml) for open issues
2. Scores each issue against the [contributor's skill profile](../../profile.yaml) using a deterministic ranking function
3. Uses an LLM (Llama 3.3 70B via Groq) to analyze the top candidates and draft patches
4. **Does NOT submit any PRs** — all submissions are made by the human contributor after review

Source: [github.com/kaushik/oss-contribution-agent](https://github.com/kaushik/oss-contribution-agent)