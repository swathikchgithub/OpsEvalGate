# Test-Driven Development

How to change OpsEvalGate: write the failing test first. This is the working agreement ([ADR-14](DECISIONS.md)). The technical design and test layers are in [TDD.md](TDD.md).

## 1. The cycle

```text
RED       write one test for one behavior; run it; watch it fail for the right reason
GREEN     write the least code that makes it pass
REFACTOR  clean up with the suite green; run everything again
```

Rules:
- A test that has never failed proves nothing. Run it red before writing the code.
- One behavior per test; the name states the behavior.
- No code without a test. A bug fix starts with a test that reproduces the bug.

## 2. Commands

```bash
.venv/bin/python -m pytest                     # full suite
.venv/bin/python -m pytest tests/test_approval_gate.py -k denied   # one area
.venv/bin/python -m pytest -x -q               # stop on first failure
python scripts/run_ci_gate.py                  # eval gate (also run before opening a PR)
```

`pytest.ini` sets `pythonpath = .` and `testpaths = tests`, so imports work from the repo root.

## 3. Test conventions

| Rule | Why |
| :--- | :--- |
| **AAA** (Arrange, Act, Assert) with blank lines between | Readable failures |
| One logical assertion per test | A failure points at one cause |
| No loops, conditionals or `try` in test bodies; use `pytest.mark.parametrize` | Tests stay simple and deterministic |
| Test behavior through the public interface | Refactors don't break tests |
| Inject doubles instead of patching internals (a scripted approver, a fake planner) | Matches the dependency-injection design |
| Deterministic: fixed seeds (`SyntheticScenarioGenerator(seed=42)`), no wall-clock or network | No flaky CI |
| No skipped tests | A skipped test is a lie; fix or delete it |
| Fast: the whole suite runs in about a second | Fast feedback keeps the loop honest |

Some older tests still use more than one assertion. Fix them when you touch them; do not rewrite them in bulk.

## 4. Worked example (illustrative: the approval gate's tests in TDD order)

1. **Red:** `test_denied_destructive_tool_is_not_executed`. The tool is a counting double; the assertion is `runs == []`. It fails because `ApprovalGate` does not exist.
2. **Green:** implement `ApprovalGate.wrap` so a denied call returns `APPROVAL_DENIED` and never calls the function.
3. **Red:** `test_approver_exception_denies_instead_of_executing`. Fails while exceptions propagate.
4. **Green:** catch the approver's exception and return a denial.
5. **Red:** `test_approval_is_per_call_and_never_cached`. Approving PID 1 must not approve PID 2.
6. **Refactor:** extract `_decide` and `_denied`; the suite stays green.

Result: each rule in the gate's contract has a test that fails if the rule is removed.

## 5. What to test, by change type

| You change | Write first |
| :--- | :--- |
| A tool | Unit test of the tool on an isolated mock environment; a test of whether it is read-only (listed) or destructive (gated by default) |
| The approval gate | A fail-closed case: what happens on error, bad return value, bad arguments, repeated calls |
| A guardrail pattern | Both sides: a payload that must be blocked **and** a benign input that must pass |
| An evaluator | A passing case, a failing case, and an invalid-input case |
| A gate rule | A value just inside the limit (pass) and just outside (fail) |
| The planner | A scenario it must solve and one it must fail; never reference golden fields |
| A prompt or model change | Run the gate; if metrics move legitimately, `--update-baseline` and review the diff in the PR |

## 6. Security tests are mandatory

Every control needs a test that fails when the control is removed. Existing patterns:

- **Ordering:** a guardrail rejects `pid="8492; rm -rf /"` before the approver is asked (the approver double records zero calls).
- **Fail closed:** approver raises, returns `True` instead of a `Decision`, or arguments do not bind, and the tool still does not run.
- **Containment:** `env.mutation_history == []` after a denied kill.
- **Structural:** `SandboxAutoApprover` must not appear in `agent_core` source.

For a new threat (for example log-borne prompt injection), write the attack as a test first and keep it in the suite.

## 7. Guarding the evaluations themselves

An eval that cannot fail is worthless. Keep these tests:

- The planner's source must not contain golden-answer identifiers.
- Under `DenyAllApprover` the success rate must fall below 100%.
- Over many noisy scenarios the planner must fail some (the metric can move).
- Under `SandboxAutoApprover` the seeded baseline is unchanged.

## 8. Definition of done

- [ ] A failing test existed before the code
- [ ] `pytest` is green and the gate passes (`python scripts/run_ci_gate.py`)
- [ ] Security-relevant change has an attack-side test
- [ ] Docs updated (`TODO.md`, and `DECISIONS.md` if the decision is architectural)
- [ ] Baseline re-recorded only if behavior legitimately changed, with the diff reviewed

## 9. Known gaps

- No coverage threshold is enforced yet (planned: `pytest-cov`).
- `evals/clients.py` has no tests (needs recorded fixtures, no live calls).
- No end-to-end test against a live model.
- Markers (`unit`, `integration`, `eval`) are declared but tests are not yet tagged.
