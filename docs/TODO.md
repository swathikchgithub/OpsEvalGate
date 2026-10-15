# OpsEvalGate TODO (pre-commit review)

Ordered by priority. Check items off as they land.

## P0: before / right after the first commit

- [x] Confirm `.env` is ignored and `artifacts/` holds no generated files or secrets you don't want committed (`git status --ignored`)
- [ ] Make the initial commit (repo has no commits yet)
- [x] Merge `.github/workflows/agent_eval_ci.yml` and `eval_gate.yml` into one workflow (they overlap and run on every PR)
- [x] Replace the hardcoded fake baseline with a real stored baseline (`evals/baselines/baseline_metrics.json`, regenerate via `python scripts/run_ci_gate.py --update-baseline`)
- [x] Remove the `continue-on-error` + later-fail pattern in the gate step
- [x] Align Python versions across CI and README (3.9 vs 3.10), and test the one you claim
- [x] Fix pytest markers: CI filters `-m "not integration and not eval"`, but the tests don't use those markers
- [x] Add `anthropic` / `openai` to `requirements.txt` (as optional extras); pin all versions

## P1: security and correctness

- [x] Add a human-approval gate for destructive tools (`approval_gate.py`; wired into the eval runner, NOT yet into `ReActAgent` defaults or the live demos)
- [ ] Tighten `SHELL_INJECTION_PATTERNS`: reduce false positives (`--`, `;`) and cover bypasses (newlines, `$IFS`, URL-encoding)
- [ ] Sanitize tool observations (log content) before they enter the scratchpad (indirect prompt injection)
- [ ] Cap repeated `BLOCKED` attempts in the agent loop
- [ ] Add guardrail tests: bypass attempts and benign-input false positives
- [ ] Stop `kill_db_process` mutating module-level `MOCK_DATABASE`; use per-run state (`MockEnvironmentFactory`)
- [ ] Add timeout, token budget and cost cap to `ReActAgent.run`
- [ ] Pick one final-answer schema and validate it with pydantic (replace the three ad-hoc formats)

## P2: cleanup

- [ ] `evals/clients.py`: delete the manual `.env` parser (`load_dotenv()` already does it); avoid import-time side effects
- [ ] Delete `make_zip.py` (1,193 lines of drifting copies); use `git archive` if a zip is needed
- [ ] Update stale model IDs (`claude-3-5-sonnet-*`, `gpt-4o`) and centralize them in one config; avoid `-latest` aliases in evals
- [ ] Add tests for `clients.py`: malformed JSON, retries, timeouts, rate limits
- [ ] Add `pytest-cov` with a coverage threshold
- [ ] Add at least one E2E or live-provider test (marked `eval`)

## P3: polish

- [ ] README: replace the static "40/40" badge, fix the `your-org` CI badge URL, tone down "enterprise-grade", add a Limitations section
- [ ] Calibrate or label as placeholders the router weights and the hard-coded `step >= 4` escalation
- [ ] Add `ruff`/`black` and `mypy`; normalize indentation (2-space vs 4-space mix)
- [ ] Add `pyproject.toml` instead of root-level scripts relying on `pythonpath = .`

## Not yet reviewed

- `evals/` evaluators, `scenario_generator`, `eval_gate`, `prompt_optimizer`, `cost/`, `docs/TDD.md`
