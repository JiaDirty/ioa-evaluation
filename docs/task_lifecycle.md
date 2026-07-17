# Task Lifecycle

Agentic tasks and background tasks share the same public Task APIs. The default
submission path is prompt-only:

```json
{"prompt": "自然语言任务", "constraints": {}, "execution_mode": "agentic"}
```

Internally, `AgenticOrchestrator` uses a finer-grained state machine:

```text
RECEIVED -> SPECIFYING -> PLANNING -> EXECUTING
          -> WAITING_HUMAN_INPUT -> REPLANNING
          -> SYNTHESIZING -> SECURITY_REVIEW -> COMPLETED / FAILED / CANCELLED
```

`WAITING_HUMAN_INPUT` is used when an Agent action or high-impact checkpoint
requires explicit user input. The API endpoint is:

```text
POST /api/tasks/{task_id}/human-input
```

The request carries `checkpoint_id`, `approved`, optional structured `input`,
and an optional user `comment`.

Runtime tasks use the following states.

## QUEUED

The task has been accepted and persisted, but no runner has started it yet.

## RUNNING

The background runner has started Gateway execution.

## WAITING_HUMAN_APPROVAL

Reserved for human-in-the-loop runtimes and high-risk action approval.

## COMPLETED

The task finished successfully and has result, events, artifacts, and any tool
history available through the task detail APIs.

## FAILED

The task execution raised an error or returned a failed result.

## CANCEL_REQUESTED

Cancellation has been requested for a running task. The runner will convert this
to `CANCELLED` as soon as it observes the flag.

## CANCELLED

The task was cancelled before or during execution.

## RETRYING

Reserved for richer retry strategies. The first hardening pass supports full
retry by creating a new queued task from the original request.

## APIs

- `POST /api/tasks`
- `GET /api/tasks`
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/detail`
- `GET /api/tasks/{task_id}/events`
- `GET /api/tasks/{task_id}/tool-calls`
- `GET /api/tasks/{task_id}/artifacts`
- `GET /api/tasks/{task_id}/execution-graph`
- `POST /api/tasks/{task_id}/cancel`
- `POST /api/tasks/{task_id}/retry`
