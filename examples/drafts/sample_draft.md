# Draft patch for confident-ai/deepeval#1547

**Issue**: [FaithfulnessMetric raises on empty retrieval_context](https://github.com/confident-ai/deepeval/issues/1547)

**Generated**: 2026-05-25 09:27 UTC

## Approach

Add a guard clause at the start of FaithfulnessMetric.measure() that handles empty or None retrieval_context by returning 0.0 with a clear warning log. Add tests covering both edge cases.

## Files changed

### `deepeval/metrics/faithfulness/faithfulness.py` (modify)

```python
# In FaithfulnessMetric.measure() method, near the top:
def measure(self, test_case: LLMTestCase) -> float:
    # ASSUMPTION: based on issue #1547, callers can pass empty lists when their
    # RAG pipeline filters out all retrieved chunks. Returning 0.0 (no support)
    # is more useful than crashing.
    if not test_case.retrieval_context:
        logger.warning(
            f"FaithfulnessMetric received empty retrieval_context for test case. "
            f"Returning score=0.0 (no supporting context to verify against)."
        )
        self.score = 0.0
        self.reason = "Empty retrieval_context — no chunks to verify faithfulness against."
        self.success = False
        return self.score

    # ... existing implementation continues here
```

## Tests to add

# In tests/test_faithfulness.py:
import pytest
from deepeval.metrics import FaithfulnessMetric
from deepeval.test_case import LLMTestCase

@pytest.mark.parametrize("context", [[], None])
def test_faithfulness_empty_context(context):
    metric = FaithfulnessMetric(threshold=0.7, async_mode=False)
    case = LLMTestCase(input="Q", actual_output="A", retrieval_context=context)
    score = metric.measure(case)
    assert score == 0.0
    assert metric.success is False
    assert "empty" in metric.reason.lower()

## Review checklist (before submitting)

- [ ] Run pytest tests/test_faithfulness.py locally and confirm it passes
- [ ] Run the full DeepEval test suite to confirm no regressions
- [ ] Verify the warning log message matches the existing logging style in this file
- [ ] Check that the success=False behavior matches how other metrics handle edge cases
- [ ] Confirm the issue is still open and unclaimed before opening the PR
- [ ] Read the last 3 merged PRs to deepeval to match commit message + PR description style

## Caveats / assumptions

- ASSUMPTION: "success=False" is the right state for empty context. Verify against other metrics.
- ASSUMPTION: deepeval uses standard Python logging. Confirmed only by reading the imports at the top of faithfulness.py — the agent did not see the actual logger config.
- The exact location of the guard clause may need adjusting depending on what code is already at the top of measure(). Adapt to fit the existing structure.

---

> ⚠️ This patch was drafted by an LLM. Before submitting:
> 1. Read every line of the diff.
> 2. Run the project's test suite locally.
> 3. Verify the issue is still open and unclaimed.
> 4. Verify maintainer-approved approach (required for LangChain).
> 5. Match your commit + PR style to recent merged PRs in the repo.