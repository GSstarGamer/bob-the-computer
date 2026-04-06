# Event Flow

## Purpose

This file describes the high-level event flow for Bob. It is a draft for Stage 0 and should become more concrete in Stage 7.

## High-Level Flow

1. An input arrives.
2. Bob classifies the input.
3. Bob decides whether it creates a new task, updates an existing task, or requires a reply.
4. Bob routes the work to the correct worker or records it for later.
5. The worker produces an artifact.
6. Bob updates coordination files and decides the next transition.

## Event Categories

- `candidate_found`
- `task_selected`
- `planning_requested`
- `plan_completed`
- `coding_requested`
- `coding_completed`
- `verification_requested`
- `verification_passed`
- `verification_failed`
- `pr_opened`
- `feedback_received`
- `task_completed`
- `task_aborted`

## Routing Intent

- discovery-style events should flow toward `Researcher`,
- plan-building events should flow toward `Planner`,
- implementation events should flow toward `Coder`,
- quality-check events should flow toward `Verifier`,
- site-content events should flow toward `Website Agent`,
- reply and follow-up events should flow toward `Responder`.

## Coordination Updates

After any meaningful event, Bob should update relevant files such as:

- `coordination/ACTIVE_TASKS.md`
- `coordination/NEXT_ACTIONS.md`
- task cards in `coordination/task-cards/`
- chat briefs in `coordination/chat-briefs/`

## Guardrails

- Bob should avoid routing without enough context.
- Events should map to a stage and task where possible.
- Events should leave a file-based trace.
- Human-facing responses should not replace task tracking.

## Future Stage 7 Work

Stage 7 should expand this file with:

- event payload structure,
- queue behavior,
- priorities,
- locking,
- retries,
- ownership,
- multi-repo coordination.
