# Bob Master Plan

## Purpose

This file is the baseline operating plan for the Bob project. It exists so future Codex chats can rely on files instead of message memory.

## Core Idea

Bob is the main decision maker.

Bob should:

- manage the overall flow,
- decide what worker should do each task,
- keep track of current work,
- handle human interaction,
- keep the project organized,
- act in Bob's personality.

Worker agents:

- `Researcher`: finds useful work to pursue.
- `Planner`: turns selected work into a step-by-step plan.
- `Coder`: implements or fixes code.
- `Verifier`: checks whether code works and matches the goal.
- `Website Agent`: maintains Bob's website and blog pages.
- `Responder`: handles PR comments, issue replies, and follow-up responses in Bob's style.

## Project Rules

1. One chat should only focus on one clear task.
2. One stage can have multiple chats, but each chat must stay inside that stage.
3. Important project knowledge must be written into files.
4. Do not mix planning, coding, website work, and architecture work unless the task is very small.
5. Every finished chat should leave behind something useful.
6. The project must stay file-driven, not memory-driven.

## Required Chat Frame

Each future serious chat should define:

- `Stage`
- `Goal`
- `Read First`
- `Allowed Changes`
- `Out of Scope`
- `Done When`

## Recommended Project Structure

At the root of this workspace:

- `docs/`
- `coordination/`
- `bob-core/`
- `bob-site/`
- `bob-sandbox/`

## Folder Purposes

### `docs/`

Long-term project knowledge:

- architecture,
- stage plans,
- system behavior,
- agent role definitions,
- important decisions,
- templates,
- operating rules.

Expected core files:

- `MASTER_PLAN.md`
- `SYSTEM_OVERVIEW.md`
- `ROADMAP.md`
- `AGENT_ROLES.md`
- `STATE_MACHINE.md`
- `EVENT_FLOW.md`

### `coordination/`

Project continuity between chats:

- current stage,
- active tasks,
- blockers,
- next actions,
- handoffs,
- summaries.

Expected core files:

- `CURRENT_STAGE.md`
- `CURRENT_PRIORITIES.md`
- `ACTIVE_TASKS.md`
- `BLOCKERS.md`
- `NEXT_ACTIONS.md`

### `bob-core/`

Main Bob system code:

- orchestrator,
- workers,
- event handling,
- queues,
- database logic,
- GitHub logic,
- memory logic,
- utilities.

### `bob-site/`

Website repo:

- homepage,
- about page,
- project pages,
- blog pages,
- public content,
- site data.

### `bob-sandbox/`

Temporary testing and scratch work.

## Chat Types

- `Architecture chats`: define how the system should work.
- `Stage implementation chats`: build a stage.
- `Fix/debug chats`: fix issues in existing work.
- `Website/content chats`: only for site and blog work.

## Chat Brief Format

Each serious chat should leave or update a brief with:

- Title
- Stage
- Purpose
- Read first
- Allowed to modify
- Do not modify
- Deliverables
- Done when
- Notes for next chat

Store these in `coordination/chat-briefs/`.

## Stage Plan

### Stage 0 - Foundation

Define project identity, agents, stage order, file structure, and chat organization rules.

### Stage 1 - Workspace Setup

Create the actual folder and repo layout.

### Stage 2 - Task and State System

Define how work is tracked, including task schema, IDs, states, history, handoffs, and summaries.

### Stage 3 - Research Flow

Define how Bob finds and ranks work.

### Stage 4 - Planning Flow

Define how selected tasks become an actionable plan.

### Stage 5 - Coding Flow

Define coder inputs, boundaries, branch handling, results, and summaries.

### Stage 6 - Verification Flow

Define how finished work is checked and when retries are required.

### Stage 7 - Orchestrator Flow

Define event types, routing, queue logic, locks, and task transitions.

### Stage 8 - GitHub Flow

Define repo creation, fork, PR, follow-up, and issue/comment linking.

### Stage 9 - Website Flow

Define homepage, project pages, blog structure, and publishing workflow.

### Stage 10 - Human Feedback Flow

Define comment intake, reply classification, follow-up task creation, and response style.

### Stage 11 - Concurrency and Queue Flow

Define safe parallelism, locking, priorities, and queue behavior.

## Task Record Fields

Meaningful tasks should track:

- `task_id`
- `title`
- `type`
- `stage`
- `current_state`
- `summary`
- `priority`
- `related_files`
- `related_repo`
- `acceptance_criteria`
- `blockers`
- `next_action`

Store task cards in `coordination/task-cards/`.

## End of Chat Expectations

Every serious chat should leave:

- what changed,
- files touched,
- current result,
- remaining work,
- recommended next chat.

## Final Direction

Build Bob like this:

1. Organize first.
2. Build one stage at a time.
3. Keep chats narrow.
4. Keep files clean.
5. Save important decisions.
6. Track active work.
7. Always leave a clear next step.
