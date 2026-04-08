# bob-core

`bob-core` contains Bob's Stage 2 planner runtime.

Stage 2 now focuses on one thing end to end:

- accept a task or GitHub issue,
- inspect the local repository snapshot,
- call the OpenAI Responses API directly,
- generate a structured implementation plan,
- save that plan to the run folder,
- pause for human approval,
- resume cleanly without regenerating the plan.

No coding, verification, or publishing happens in this stage.

## Quick Start

1. Install the package:

```powershell
pip install -e .[dev]
```

2. Set credentials and optional planner config:

- `OPENAI_API_KEY` (required)
- `BOB_OPENAI_MODEL` (optional, defaults to `gpt-5.4-mini`)
- `BOB_OPENAI_TIMEOUT_SECONDS` (optional, defaults to `60`)
- `BOB_OPENAI_MAX_RETRIES` (optional, defaults to `3`)
- `BOB_LLM_MODEL` may still be used as a fallback alias if `BOB_OPENAI_MODEL` is unset
- GitHub auth for `gh` if you want issue-backed runs

3. Start a Stage 1 planner run:

```powershell
bob stage1 run --repo owner/name --path C:\path\to\checkout --task "Fix the flaky test"
```

Or with a GitHub issue:

```powershell
bob stage1 run --repo owner/name --path C:\path\to\checkout --issue 123
```

4. Review the saved plan again later without changing state:

```powershell
bob stage1 resume --run <run-id>
```

5. Approve the saved plan:

```powershell
bob stage1 resume --run <run-id> --approve-plan
```

Run artifacts are stored under `bob-core/runs/<run-id>/`.

## Saved Artifacts

Each run stores:

- `task_brief.json`
- `planner_prompt.md`
- `planner_response.json`
- `planner_result.json`
- `ledger.json`

## Notes

- Stage 2 uses the OpenAI API directly through the official Python SDK and defaults to `gpt-5.4-mini`.
- The planner prompt is stored in `src/bob/stage1/prompts.py`.
- `resume` without `--approve-plan` is intentionally read-only while the run is awaiting approval.
- The repository must still be clean before Bob will create a saved plan.
