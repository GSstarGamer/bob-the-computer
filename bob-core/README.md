# bob-core

`bob-core` contains Bob's runtime code.

Stage 1 implements a narrow local orchestrator around the OpenAI Agents SDK:

- one local checkout plus one issue or task,
- repo and issue research,
- a short implementation plan,
- explicit human approval before any write action,
- Codex-based implementation,
- verification and a PR-ready summary.

## Quick Start

1. Install the package:

```powershell
pip install -e .[dev]
```

2. Set any credentials Bob needs:

- `OPENAI_API_KEY` for both the Agents SDK and the Codex CLI when Bob runs with `--provider openai`
- GitHub auth for `gh` if you want issue-backed runs

3. Run Stage 1:

```powershell
bob stage1 run --repo owner/name --path C:\path\to\checkout --task "Fix the flaky test"
```

Or with a GitHub issue:

```powershell
bob stage1 run --repo owner/name --path C:\path\to\checkout --issue 123
```

4. Approve the write phase:

```powershell
bob stage1 resume --run <run-id> --approve-write
```

Run artifacts are stored under `bob-core/runs/<run-id>/`.

## Notes

- Stage 1 stops before commit, push, or PR creation.
- Research uses Codex without tool access and only on the task brief plus repo snapshot.
- Write mode delegates to Codex with an explicit approval gate and a bounded work order.
- The Codex wrapper pins `--provider openai` and `gpt-5.4-mini` by default. This model is a currently valid OpenAI API model, so Stage 1 stays API-only without relying on local models or ChatGPT/Codex account auth.
- You can override models with `BOB_LLM_MODEL`, `BOB_CODEX_MODEL`, `BOB_CODEX_RESEARCH_MODEL`, and `BOB_CODEX_WRITE_MODEL`.
