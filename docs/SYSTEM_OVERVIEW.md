# System Overview

## Mission

Bob is a file-driven multi-agent software builder. Bob acts as the central coordinator and personality, while worker agents perform specialized work within bounded roles.

## System Shape

The system has three main layers:

1. `Coordination layer`
   Keeps project state outside chat memory through docs, task records, stage tracking, and handoffs.
2. `Control layer`
   Bob decides what should happen next, chooses the correct worker, tracks progress, and enforces rules.
3. `Execution layer`
   Worker agents perform research, planning, implementation, verification, website maintenance, or response work.

## Design Principles

- File-driven over memory-driven.
- Narrow chats over sprawling context.
- Stage-based progress over ad hoc edits.
- Explicit scope before implementation.
- Persistent summaries and handoffs after each serious task.

## Bob Responsibilities

Bob should:

- classify incoming work,
- map work to the right stage,
- choose the right worker,
- maintain task state,
- guard boundaries,
- communicate in Bob's style,
- keep the project organized.

## Worker Responsibilities

- `Researcher`: gathers candidates, signals, and possible work items.
- `Planner`: produces a structured implementation packet.
- `Coder`: makes bounded code or documentation changes.
- `Verifier`: checks behavior, criteria, and regressions.
- `Website Agent`: maintains public-facing pages and blog content.
- `Responder`: replies to PRs, issues, and follow-up messages in Bob's voice.

## Operating Model

Each serious chat should start by establishing:

- stage,
- exact goal,
- files to read first,
- allowed modifications,
- out-of-scope boundaries,
- definition of done.

Each serious chat should end with:

- a useful artifact,
- an updated coordination trail,
- a clear next step.

## Expected Long-Term Components

- task and state system,
- research pipeline,
- planner packet format,
- coding workflow,
- verification workflow,
- orchestrator event model,
- GitHub interaction flow,
- website publishing flow,
- human feedback loop,
- concurrency and queue rules.

## Current Assumption

This repository is the umbrella workspace for Bob project planning and coordination. Code may eventually live in subdirectories such as `bob-core/` and `bob-site/`, or be split into dedicated repos later if needed.
