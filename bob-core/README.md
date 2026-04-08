# bob-core

`bob-core` is Bob's unified planning runtime.

The current runtime does one autonomous planning loop end to end:

- accept a direct task, GitHub issue, or candidate task file,
- inspect the local repository snapshot,
- normalize and score candidate work with a configured LLM backend,
- reject poor candidates automatically,
- create a structured implementation plan for the best viable task,
- save typed artifacts to a session folder,
- resume later from saved state without regenerating work.

This milestone stops at `READY_FOR_EXECUTION`. It does not start coding, verification, or publishing yet.

## LLM Backends

Bob supports two backend modes:

- `openai`
- `openai_compatible`

`openai` is still supported through the official OpenAI API. `openai_compatible` is intended for remote `vLLM` or similar endpoints that expose an OpenAI-style `/v1` API.

The built-in default is now Bob's public server-local endpoint:

- provider: `openai_compatible`
- base URL: `https://llm.rionnag.net/gpt-oss/v1`
- model: `gpt-oss-20b`

### Default Models

- OpenAI default: `gpt-5.4-mini`
- Local OpenAI-compatible default: `gpt-oss-20b`

`gpt-oss-20b` is the current practical default for the audited `ssh.rionnag.net` server because it has a single 24 GB RTX 3090. Heavier specialist models are not enabled by default on that hardware.

## Requirements

- Python 3.11+
- one of:
  - no API key for the default public local endpoint
  - `OPENAI_API_KEY` for `openai`
  - optional `BOB_LLM_API_KEY` for protected `openai_compatible` endpoints
- GitHub auth for `gh` if you want issue-backed sessions

## Install

```powershell
pip install -e .[dev]
```

## Configuration

Shared environment variables:

- `BOB_LLM_PROVIDER`
- `BOB_LLM_BASE_URL`
- `BOB_LLM_API_KEY` (optional)
- `BOB_LLM_MODEL`
- `BOB_LLM_TIMEOUT_SECONDS`
- `BOB_LLM_MAX_RETRIES`

Role-specific overrides:

- `BOB_EVALUATOR_MODEL`
- `BOB_EVALUATOR_BASE_URL`
- `BOB_PLANNER_MODEL`
- `BOB_PLANNER_BASE_URL`
- `BOB_CODER_MODEL`
- `BOB_CODER_BASE_URL`

OpenAI compatibility aliases retained from the earlier runtime:

- `OPENAI_API_KEY`
- `BOB_OPENAI_MODEL`
- `BOB_OPENAI_TIMEOUT_SECONDS`
- `BOB_OPENAI_MAX_RETRIES`

Resolution order is:

1. CLI override
2. role-specific environment variable
3. shared environment variable
4. built-in default

## CLI

Run Bob on a direct task with the default local backend:

```powershell
bob run --repo owner/name --path C:\path\to\checkout --task "Build the unified planning flow"
```

Run Bob against OpenAI explicitly:

```powershell
bob run `
  --repo owner/name `
  --path C:\path\to\checkout `
  --task "Build the unified planning flow" `
  --provider openai `
  --model gpt-5.4-mini
```

Run Bob on a GitHub issue:

```powershell
bob run --repo owner/name --path C:\path\to\checkout --issue 123
```

Run Bob on a JSON candidate file:

```powershell
bob run --repo owner/name --path C:\path\to\checkout --candidate-file C:\path\to\candidates.json
```

Resume a saved session without changing state:

```powershell
bob resume --session <session-id>
```

Show or export the saved implementation plan:

```powershell
bob show-plan --session <session-id>
bob show-plan --session <session-id> --format markdown --output C:\path\to\plan.md
```

Probe the configured LLM backend:

```powershell
bob llm-probe
bob llm-probe --provider openai --model gpt-5.4-mini
```

## Candidate File Format

Bob accepts either a JSON array or an object with a `candidates` array. Each candidate should include:

- `title`
- `summary`
- optional `candidate_id`
- optional `details`
- optional `source_type`
- optional `source_id`
- optional `labels`
- optional `issue`

Example:

```json
{
  "candidates": [
    {
      "candidate_id": "candidate-auth",
      "title": "Stabilize auth retries",
      "summary": "Reduce flaky retry behavior in the auth client.",
      "details": "Start from the auth runtime and existing retry wrapper."
    }
  ]
}
```

## Saved Artifacts

Successful planning sessions store:

- `candidate_tasks.json`
- `task_evaluations.json`
- `selection_result.json`
- `planner_prompt.md`
- `planner_response.json`
- `planner_result.json`
- `ledger.json`

Rejected sessions store:

- `candidate_tasks.json`
- `task_evaluations.json`
- `selection_result.json`
- `ledger.json`

Sessions are stored under `bob-core/runs/<session-id>/`.

## Remote Local-LLM Deployment

The repo includes a small deployment bundle under `ops/remote_llm/` for the SSH-hosted `vLLM` server setup. The deployed shape now includes:

- `https://llm.rionnag.net/gpt-oss/v1`
- no API key requirement by default
- Bob configured with `--provider openai_compatible`

The audited `ssh.rionnag.net` hardware currently supports the primary `gpt-oss-20b` planner/evaluator endpoint cleanly. Extra coding or deep-thinking endpoints should be treated as optional follow-up work unless the server gains more GPU memory.

The runtime now sends a neutral Bob-specific `User-Agent`, which avoids the Cloudflare browser-signature block that affected the default SDK signature on this hostname.

For the first server bootstrap only, connect interactively over SSH and type the password at the prompt:

```powershell
ssh girish@ssh.rionnag.net
```

Do not save the password in scripts, env files, committed config, or service units. After the first login, set up SSH keys and use key-based auth for follow-up automation.

An SSH port-forward helper is still included as a troubleshooting fallback:

```powershell
.\ops\remote_llm\start_ssh_tunnel.ps1
```

Use it only if you need to bypass the public edge temporarily while debugging.

## Tests

Default unit tests are mocked and do not require live model access:

```powershell
pytest -q
```

Opt-in live tests are marked `local_llm` and only run when the required environment variables are present:

```powershell
pytest -m local_llm -q
```
