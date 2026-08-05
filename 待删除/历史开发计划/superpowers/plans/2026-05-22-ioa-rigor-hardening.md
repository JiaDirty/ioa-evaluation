# IoA Rigor Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove false-positive and fake-delivery paths from the IoA evaluation framework so live results are scientifically defensible.

**Architecture:** Gateway dispatch must use protocol adapters and HTTP endpoints, with local endpoints backed by real AG2/LLM runtimes for the default testbed. Risk tests must treat task execution failures as invalid evidence, not defense success. Behavior tests should evaluate attack outcomes and observable external metadata instead of mechanism presence.

**Tech Stack:** Python 3.12, unittest, AG2/autogen agents, OpenAI-compatible LLM API, urllib/http.server for protocol endpoint delivery.

---

### Task 1: Protocol-backed Agent Dispatch

**Files:**
- Create: `src/protocol/local_endpoint.py`
- Modify: `src/gateway/gateway.py`
- Modify: `src/experiment/runner.py`
- Test: `tests/test_gateway_protocol_endpoint_dispatch.py`

- [ ] **Step 1: Write failing test**

Create a test that registers an AgentCard with a real local HTTP endpoint, invokes `Gateway.handle_task`, and asserts the endpoint received a protocol POST. Also test that a missing endpoint fails closed instead of falling back to an in-memory runner.

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_gateway_protocol_endpoint_dispatch -v`

- [ ] **Step 3: Implement local endpoint server**

Add a lightweight HTTP server that decodes protocol envelopes and calls `IoAEnvironment.run_agent_task(sub_ioa_id, agent_id, prompt)`.

- [ ] **Step 4: Route Gateway through adapter.send_message**

`Gateway._relay_task` should call the negotiated adapter against `target.endpoint`; it should parse the response body and build an Artifact from returned content. Missing endpoints raise a task failure.

- [ ] **Step 5: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_gateway_protocol_endpoint_dispatch -v`

### Task 2: Invalid Evaluation on Execution Failure

**Files:**
- Modify: `risk_tests/base_test.py`
- Modify: `risk_tests/interconnection/cascade_propagation.py`
- Modify: `risk_tests/interconnection/structure_exposure.py`
- Modify: `risk_tests/power_imbalance/reputation_monopoly.py`
- Test: `tests/test_risk_test_validity_guards.py`

- [ ] **Step 1: Write failing test**

Create tests proving that a failed `env.submit_task()` cannot be counted as containment/balancing/audit success.

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_risk_test_validity_guards -v`

- [ ] **Step 3: Add helper assertions**

Add `require_task_completed()` to `BaseIoARiskTest` and use it where task execution is evidence.

- [ ] **Step 4: Replace existence checks**

Change cascade, structure/behavior inference, incentive mismatch, and node manipulation checks so audit entries are evidence only unless the behavior objective is actually met.

- [ ] **Step 5: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_risk_test_validity_guards -v`

### Task 3: Knowledge Judge Strictness

**Files:**
- Modify: `src/experiment/runner.py`
- Modify: `src/core/shared_knowledge.py`
- Test: `tests/test_shared_knowledge_semantic_conflict.py`

- [ ] **Step 1: Write failing test**

Add a test showing an unavailable semantic judge marks the new knowledge entry as disputed/invalid evidence instead of silently asserting no conflict.

- [ ] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_shared_knowledge_semantic_conflict -v`

- [ ] **Step 3: Implement strict unknown handling**

If overlapping tagged claims cannot be semantically judged, mark them with `semantic_relation=unknown` metadata and avoid using them as clean active evidence.

- [ ] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_shared_knowledge_semantic_conflict -v`

### Task 4: Documentation and Verification

**Files:**
- Modify: `docs/真实性与学术严谨性审计.md`
- Modify: `docs/risk_alignment.md`
- Modify: `docs/architecture.html`
- Modify: `docs/测评框架结构与流程说明.html`

- [ ] **Step 1: Update stale wording**

Remove claims that recommend mock/dry-run for this formal framework. Replace “模拟真实 IoA” with “可控 IoA testbed” and document controlled mutation separately from fake execution.

- [ ] **Step 2: Run full verification**

Run compileall, unittest discovery, then a live LLM experiment into `results\_rigor_live_verify`.

- [ ] **Step 3: Record evidence**

Update the audit document with command outputs and any remaining boundaries that require external production endpoints.

