# State Machine

## Purpose

This file is the baseline task state draft for Bob. It is intentionally lightweight for Stage 0 and should be refined in Stage 2.

## Core States

- `DISCOVERED`
- `SELECTED`
- `PLANNING`
- `CODING`
- `VERIFYING`
- `PR_OPEN`
- `AWAITING_FEEDBACK`
- `DONE`
- `ABORTED`

## State Intent

### `DISCOVERED`

A possible task has been identified but not chosen.

### `SELECTED`

Bob has chosen the task for active processing.

### `PLANNING`

The Planner is turning the selected task into an actionable packet.

### `CODING`

The Coder is implementing the approved work.

### `VERIFYING`

The Verifier is checking outcomes against requirements.

### `PR_OPEN`

The implementation exists in a pull request or equivalent review state.

### `AWAITING_FEEDBACK`

The task is blocked on human or reviewer input.

### `DONE`

The work is complete and accepted.

### `ABORTED`

The task has been intentionally stopped or invalidated.

## Baseline Transition Flow

Typical happy path:

`DISCOVERED -> SELECTED -> PLANNING -> CODING -> VERIFYING -> PR_OPEN -> AWAITING_FEEDBACK -> DONE`

Possible fallback loops:

- `PLANNING -> ABORTED`
- `CODING -> PLANNING`
- `VERIFYING -> CODING`
- `PR_OPEN -> CODING`
- `AWAITING_FEEDBACK -> CODING`
- `AWAITING_FEEDBACK -> DONE`

## Transition Rules

- A task should only move forward when the output for the current state exists in files.
- A task should not skip directly from `DISCOVERED` to `CODING` unless the task is tiny and explicitly allowed.
- Failed verification should send work back to `CODING`.
- Human or reviewer requests should be captured before re-entering `CODING`.
- Any abandoned or invalid task should end in `ABORTED` with a reason recorded.

## Future Stage 2 Work

Stage 2 should expand this file with:

- state entry criteria,
- state exit criteria,
- required artifacts for each transition,
- task history format,
- ownership rules,
- retry and timeout handling.
