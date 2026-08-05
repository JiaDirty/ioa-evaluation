# Production IoA Network Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the current local IoA evaluation testbed into a production-grade distributed Internet of Agents network with real service boundaries, durable identity, protocol gateways, observability, policy enforcement, and deployment topology.

**Architecture:** Keep the existing experiment runner and risk tests as the evaluation layer, but split the runtime into production services: Global Registry, Sub-IoA Gateway, Agent Runtime Service, Protocol Gateway, Audit/Provenance Service, Marketplace, and Control Plane API. Use HTTP initially with mTLS-ready service boundaries, PostgreSQL persistence, signed AgentCards, protocol adapters, and OpenTelemetry-compatible tracing.

**Tech Stack:** Python 3.11+, FastAPI, PostgreSQL, SQLAlchemy or SQLModel, Pydantic v2, Docker Compose, OpenAI-compatible LLM endpoints, structured JSON logs, pytest/unittest, optional Redis/NATS for async task dispatch after the synchronous service boundary is stable.

---

## Scope Decision

The existing project is a strong local testbed, but "完整生产级 IoA 网络" spans multiple independent subsystems. Implement it in six separately testable increments:

1. Runtime service split and Docker topology.
2. Durable Registry with signed AgentCards.
3. Real Gateway-to-Agent HTTP transport with production request contracts.
4. Cross-domain routing and Marketplace orchestration.
5. Audit/provenance persistence and trace export.
6. Security hardening: authN/authZ, rate limits, policy, secrets, and deployment profile.

This plan intentionally preserves the current `run_experiment.py` flow while introducing production services beside it. The testbed should continue to run during migration.

---

## File Structure

Create:
- `src/runtime/service.py`: FastAPI app exposing Agent Runtime endpoints.
- `src/runtime/contracts.py`: Request/response models for runtime execution.
- `src/runtime/runner.py`: Adapter from HTTP request to existing per-Agent AG2/LLM runtime.
- `src/storage/database.py`: Database engine/session management.
- `src/storage/models.py`: SQL persistence models for AgentCards, audit entries, tasks, artifacts.
- `src/storage/repositories.py`: Repository layer used by Registry/Audit/Marketplace.
- `src/security/identity.py`: AgentCard signing, verification, key-id handling.
- `src/security/auth.py`: Request authentication primitives.
- `src/security/policy.py`: Shared production policy decisions.
- `deploy/docker-compose.prod.yml`: Multi-service production-like local deployment.
- `deploy/env.example`: Environment variables for production-like deployment.
- `tests/test_runtime_service_contracts.py`: Runtime API contract tests.
- `tests/test_signed_agent_registry.py`: Signed AgentCard registry tests.
- `tests/test_gateway_runtime_delivery.py`: Gateway-to-runtime HTTP delivery tests.
- `tests/test_persistent_audit.py`: Audit persistence and trace retrieval tests.

Modify:
- `src/core/data_models.py`: Add production fields without breaking existing seed data.
- `src/registry/registry.py`: Add repository-backed mode while preserving in-memory mode.
- `src/gateway/gateway.py`: Use production runtime contract and auth headers.
- `src/protocol/adapters.py`: Keep A2A/MCP/Private API shapes, add retries and auth header support.
- `src/audit/audit_logger.py`: Add repository-backed mode.
- `src/experiment/runner.py`: Allow service endpoint discovery from config.
- `api/main.py`: Expose production control-plane health/status endpoints.
- `docker-compose.yml`: Keep testbed compose; point users to `deploy/docker-compose.prod.yml` for production-like mode.
- `requirements.txt`: Add `sqlalchemy`, `psycopg[binary]`, `cryptography`, `opentelemetry-api`, `opentelemetry-sdk`.

---

## Task 1: Runtime Service Boundary

**Files:**
- Create: `src/runtime/contracts.py`
- Create: `src/runtime/runner.py`
- Create: `src/runtime/service.py`
- Test: `tests/test_runtime_service_contracts.py`

- [ ] **Step 1: Add contract tests**

Create `tests/test_runtime_service_contracts.py`:

```python
from src.runtime.contracts import RuntimeExecuteRequest, RuntimeExecuteResponse


def test_runtime_execute_request_requires_agent_and_task():
    request = RuntimeExecuteRequest(
        trace_id="trace-1",
        message_id="msg-1",
        source_agent_id="finance-gw",
        target_agent_id="agent-1",
        protocol="a2a",
        task="Assess a company",
        payload={"risk": "medium"},
    )

    assert request.target_agent_id == "agent-1"
    assert request.protocol == "a2a"
    assert request.payload["risk"] == "medium"


def test_runtime_execute_response_shape():
    response = RuntimeExecuteResponse(
        status="completed",
        content="analysis complete",
        source_agent_id="agent-1",
        source_sub_ioa_id="finance",
        trace_id="trace-1",
        message_id="msg-1",
    )

    assert response.status == "completed"
    assert response.content == "analysis complete"
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_runtime_service_contracts.py -v
```

Expected: FAIL because `src.runtime.contracts` does not exist.

- [ ] **Step 3: Add runtime contracts**

Create `src/runtime/contracts.py`:

```python
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class RuntimeExecuteRequest(BaseModel):
    trace_id: str
    message_id: str
    source_agent_id: str
    target_agent_id: str
    protocol: Literal["a2a", "mcp", "private_api"]
    task: str
    payload: dict[str, Any] = Field(default_factory=dict)


class RuntimeExecuteResponse(BaseModel):
    status: Literal["completed", "failed"]
    content: Any = None
    source_agent_id: str
    source_sub_ioa_id: str
    trace_id: str
    message_id: str
    error: str | None = None
```

- [ ] **Step 4: Add runtime runner adapter**

Create `src/runtime/runner.py`:

```python
from __future__ import annotations

from collections.abc import Callable

from .contracts import RuntimeExecuteRequest, RuntimeExecuteResponse


class RuntimeRunner:
    def __init__(
        self,
        run_agent_task: Callable[[str, str, str], str],
        sub_ioa_lookup: Callable[[str], str | None],
    ) -> None:
        self.run_agent_task = run_agent_task
        self.sub_ioa_lookup = sub_ioa_lookup

    def execute(self, request: RuntimeExecuteRequest) -> RuntimeExecuteResponse:
        sub_ioa_id = self.sub_ioa_lookup(request.target_agent_id)
        if not sub_ioa_id:
            return RuntimeExecuteResponse(
                status="failed",
                content=None,
                source_agent_id=request.target_agent_id,
                source_sub_ioa_id="",
                trace_id=request.trace_id,
                message_id=request.message_id,
                error=f"unknown agent endpoint: {request.target_agent_id}",
            )

        prompt = self._build_prompt(request)
        content = self.run_agent_task(sub_ioa_id, request.target_agent_id, prompt)
        return RuntimeExecuteResponse(
            status="completed",
            content=content,
            source_agent_id=request.target_agent_id,
            source_sub_ioa_id=sub_ioa_id,
            trace_id=request.trace_id,
            message_id=request.message_id,
        )

    @staticmethod
    def _build_prompt(request: RuntimeExecuteRequest) -> str:
        return (
            f"[Protocol: {request.protocol}]\n"
            f"[Task ID: {request.trace_id}]\n"
            f"[Message ID: {request.message_id}]\n\n"
            f"{request.task}\n\n"
            f"Payload:\n{request.payload}"
        )
```

- [ ] **Step 5: Add FastAPI runtime service**

Create `src/runtime/service.py`:

```python
from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .contracts import RuntimeExecuteRequest, RuntimeExecuteResponse
from .runner import RuntimeRunner


def create_runtime_app(runner: RuntimeRunner) -> FastAPI:
    app = FastAPI(title="IoA Agent Runtime Service", version="1.0.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "ioa-agent-runtime"}

    @app.post("/agents/{agent_id}/execute", response_model=RuntimeExecuteResponse)
    async def execute(agent_id: str, request: RuntimeExecuteRequest) -> RuntimeExecuteResponse:
        if request.target_agent_id != agent_id:
            raise HTTPException(status_code=400, detail="path agent_id does not match request target_agent_id")
        return runner.execute(request)

    return app
```

- [ ] **Step 6: Run contract tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_runtime_service_contracts.py -v
```

Expected: PASS.

---

## Task 2: Signed AgentCard Identity

**Files:**
- Create: `src/security/identity.py`
- Modify: `src/core/data_models.py`
- Test: `tests/test_signed_agent_registry.py`

- [ ] **Step 1: Add identity tests**

Create `tests/test_signed_agent_registry.py`:

```python
from src.core.data_models import AgentCard
from src.security.identity import AgentCardSigner


def test_agent_card_signature_roundtrip():
    signer = AgentCardSigner(secret=b"dev-secret", key_id="dev-key")
    card = AgentCard(
        display_name="Finance Analyst",
        provider="finance-org",
        sub_ioa_id="finance",
        declared_capabilities=["financial_analysis"],
        certificate=None,
    )

    signed = signer.sign(card)

    assert signed.certificate is not None
    assert signer.verify(signed)


def test_agent_card_signature_detects_tampering():
    signer = AgentCardSigner(secret=b"dev-secret", key_id="dev-key")
    card = AgentCard(
        display_name="Finance Analyst",
        provider="finance-org",
        sub_ioa_id="finance",
        declared_capabilities=["financial_analysis"],
    )
    signed = signer.sign(card)
    tampered = signed.model_copy(update={"provider": "attacker"})

    assert not signer.verify(tampered)
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_signed_agent_registry.py -v
```

Expected: FAIL because `src.security.identity` does not exist.

- [ ] **Step 3: Implement HMAC signing**

Create `src/security/identity.py`:

```python
from __future__ import annotations

import base64
import hashlib
import hmac
import json

from src.core.data_models import AgentCard


class AgentCardSigner:
    def __init__(self, secret: bytes, key_id: str) -> None:
        self.secret = secret
        self.key_id = key_id

    def sign(self, card: AgentCard) -> AgentCard:
        payload = self._canonical_payload(card)
        digest = hmac.new(self.secret, payload, hashlib.sha256).digest()
        token = f"hmac-sha256:{self.key_id}:{base64.urlsafe_b64encode(digest).decode('ascii')}"
        return card.model_copy(update={"certificate": token})

    def verify(self, card: AgentCard) -> bool:
        if not card.certificate:
            return False
        parts = card.certificate.split(":", 2)
        if len(parts) != 3:
            return False
        alg, key_id, signature = parts
        if alg != "hmac-sha256" or key_id != self.key_id:
            return False
        expected = self.sign(card.model_copy(update={"certificate": None})).certificate
        return hmac.compare_digest(expected or "", card.certificate)

    @staticmethod
    def _canonical_payload(card: AgentCard) -> bytes:
        data = card.model_dump(mode="json")
        data["certificate"] = None
        stable = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return stable.encode("utf-8")
```

- [ ] **Step 4: Run identity tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_signed_agent_registry.py -v
```

Expected: PASS.

---

## Task 3: Persistent Storage Foundation

**Files:**
- Create: `src/storage/database.py`
- Create: `src/storage/models.py`
- Create: `src/storage/repositories.py`
- Modify: `requirements.txt`
- Test: `tests/test_persistent_audit.py`

- [ ] **Step 1: Add dependencies**

Modify `requirements.txt` and add:

```text
sqlalchemy>=2.0.0
psycopg[binary]>=3.1.0
```

- [ ] **Step 2: Add storage tests**

Create `tests/test_persistent_audit.py`:

```python
from src.core.data_models import AuditAction
from src.storage.database import create_sqlite_engine, create_session_factory, initialize_database
from src.storage.repositories import AuditRepository


def test_audit_repository_persists_trace_entries():
    engine = create_sqlite_engine(":memory:")
    initialize_database(engine)
    Session = create_session_factory(engine)

    with Session() as session:
        repo = AuditRepository(session)
        repo.log_action(
            trace_id="trace-1",
            step_index=1,
            action=AuditAction.CALL,
            agent_id="finance-gw",
            sub_ioa_id="finance",
        )
        entries = repo.list_by_trace("trace-1")

    assert len(entries) == 1
    assert entries[0].agent_id == "finance-gw"
```

- [ ] **Step 3: Run the failing test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_persistent_audit.py -v
```

Expected: FAIL because storage modules do not exist.

- [ ] **Step 4: Implement database helpers**

Create `src/storage/database.py`:

```python
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from .models import Base


def create_sqlite_engine(path: str) -> Engine:
    url = "sqlite:///:memory:" if path == ":memory:" else f"sqlite:///{path}"
    return create_engine(url, future=True)


def create_postgres_engine(database_url: str) -> Engine:
    return create_engine(database_url, future=True, pool_pre_ping=True)


def create_session_factory(engine: Engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def initialize_database(engine: Engine) -> None:
    Base.metadata.create_all(engine)
```

- [ ] **Step 5: Implement storage models**

Create `src/storage/models.py`:

```python
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AuditEntryRow(Base):
    __tablename__ = "audit_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(128), index=True)
    step_index: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    action: Mapped[str] = mapped_column(String(64))
    agent_id: Mapped[str] = mapped_column(String(128))
    sub_ioa_id: Mapped[str] = mapped_column(String(128))
    gateway_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
```

- [ ] **Step 6: Implement audit repository**

Create `src/storage/repositories.py`:

```python
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.data_models import AuditAction

from .models import AuditEntryRow


class AuditRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def log_action(
        self,
        *,
        trace_id: str,
        step_index: int,
        action: AuditAction,
        agent_id: str,
        sub_ioa_id: str,
        gateway_id: str | None = None,
        target_agent_id: str | None = None,
        details: dict | None = None,
    ) -> AuditEntryRow:
        row = AuditEntryRow(
            trace_id=trace_id,
            step_index=step_index,
            action=action.value,
            agent_id=agent_id,
            sub_ioa_id=sub_ioa_id,
            gateway_id=gateway_id,
            target_agent_id=target_agent_id,
            details=details or {},
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def list_by_trace(self, trace_id: str) -> list[AuditEntryRow]:
        stmt = select(AuditEntryRow).where(AuditEntryRow.trace_id == trace_id).order_by(AuditEntryRow.step_index)
        return list(self.session.execute(stmt).scalars())
```

- [ ] **Step 7: Run storage tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_persistent_audit.py -v
```

Expected: PASS.

---

## Task 4: Production Gateway Delivery Contract

**Files:**
- Modify: `src/protocol/adapters.py`
- Modify: `src/gateway/gateway.py`
- Test: `tests/test_gateway_runtime_delivery.py`

- [ ] **Step 1: Add delivery test**

Create `tests/test_gateway_runtime_delivery.py`:

```python
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.core.data_models import ProtocolMessage, ProtocolType
from src.protocol.adapters import create_adapter


class RuntimeHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")
        body = json.loads(raw)
        assert self.headers["X-IoA-Protocol"] == "a2a"
        response = {
            "status": "completed",
            "content": body["params"]["task"],
            "source_agent_id": body["metadata"]["target_agent"],
            "source_sub_ioa_id": "finance",
            "trace_id": body["metadata"]["trace_id"],
            "message_id": body["id"],
        }
        encoded = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        return


@pytest.fixture
def runtime_endpoint():
    server = ThreadingHTTPServer(("127.0.0.1", 0), RuntimeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/agents/agent-1"
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


@pytest.mark.asyncio
async def test_a2a_adapter_delivers_to_http_runtime(runtime_endpoint):
    adapter = create_adapter(ProtocolType.A2A)
    message = ProtocolMessage(
        source_protocol=ProtocolType.A2A,
        target_protocol=ProtocolType.A2A,
        source_agent_id="finance-gw",
        target_agent_id="agent-1",
        trace_id="trace-1",
        method="execute_task",
        params={"task": "hello", "payload": {}},
    )

    delivered = await adapter.send_message(runtime_endpoint, message)
    decoded = adapter.decode_delivery_result(delivered)

    assert delivered["http_status"] == 200
    assert decoded["status"] == "completed"
    assert decoded["content"] == "hello"
```

- [ ] **Step 2: Run delivery test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_gateway_runtime_delivery.py -v
```

Expected: PASS with current HTTP adapter. If it fails due timeout defaults, reduce adapter test timeout by adding an optional `timeout_seconds` parameter in `_post_json_endpoint`.

- [ ] **Step 3: Add auth header support**

Modify `_post_json_endpoint` in `src/protocol/adapters.py` to accept `headers: dict[str, str] | None = None` and merge it into the request headers:

```python
def _post_json_endpoint(
    target_endpoint: str,
    encoded: str,
    protocol: str,
    timeout_seconds: int = 120,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not target_endpoint:
        raise ProtocolDeliveryError(
            f"No endpoint configured for {protocol}; refusing to simulate delivery"
        )
    request_headers = {
        "Content-Type": "application/json",
        "X-IoA-Protocol": protocol,
    }
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(
        target_endpoint,
        data=encoded.encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
```

- [ ] **Step 4: Run existing tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: all existing tests pass.

---

## Task 5: Production-Like Docker Topology

**Files:**
- Create: `deploy/docker-compose.prod.yml`
- Create: `deploy/env.example`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add production env example**

Create `deploy/env.example`:

```text
PYTHONPATH=/app
LOG_LEVEL=INFO
IOA_DATABASE_URL=postgresql+psycopg://ioa:ioa_dev@postgres:5432/ioa_registry
IOA_IDENTITY_HMAC_SECRET=change-me-in-production
IOA_IDENTITY_KEY_ID=dev-key
OPENAI_API_KEY=
OPENAI_BASE_URL=
```

- [ ] **Step 2: Add production compose**

Create `deploy/docker-compose.prod.yml`:

```yaml
version: "3.8"

services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: ioa_registry
      POSTGRES_USER: ioa
      POSTGRES_PASSWORD: ioa_dev
    ports:
      - "5432:5432"
    volumes:
      - ioa_pgdata:/var/lib/postgresql/data

  control-plane:
    build:
      context: ..
    env_file:
      - ./env.example
    environment:
      IOA_SERVICE_ROLE: control-plane
    ports:
      - "8000:8000"
    command: uvicorn api.main:app --host 0.0.0.0 --port 8000
    depends_on:
      - postgres

  finance-runtime:
    build:
      context: ..
    env_file:
      - ./env.example
    environment:
      IOA_SERVICE_ROLE: agent-runtime
      IOA_SUB_IOA_ID: finance
    ports:
      - "8101:8100"
    command: python -m src.runtime.service_main --host 0.0.0.0 --port 8100 --sub-ioa finance
    depends_on:
      - postgres

  healthcare-runtime:
    build:
      context: ..
    env_file:
      - ./env.example
    environment:
      IOA_SERVICE_ROLE: agent-runtime
      IOA_SUB_IOA_ID: healthcare
    ports:
      - "8102:8100"
    command: python -m src.runtime.service_main --host 0.0.0.0 --port 8100 --sub-ioa healthcare
    depends_on:
      - postgres

volumes:
  ioa_pgdata:
```

- [ ] **Step 3: Document compose modes in root compose**

Append this comment to `docker-compose.yml`:

```yaml
# Production-like distributed topology lives in:
#   deploy/docker-compose.prod.yml
# Start with:
#   docker compose -f deploy/docker-compose.prod.yml up --build
```

- [ ] **Step 4: Validate compose syntax**

Run:

```powershell
docker compose -f deploy/docker-compose.prod.yml config
```

Expected: compose file renders without syntax errors.

---

## Task 6: Control Plane Readiness Gates

**Files:**
- Modify: `api/main.py`
- Create: `tests/test_control_plane_health.py`

- [ ] **Step 1: Add health test**

Create `tests/test_control_plane_health.py`:

```python
from fastapi.testclient import TestClient

from api.main import app


def test_health_endpoint_reports_control_plane():
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "ioa-console-api"
```

- [ ] **Step 2: Run health test**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_control_plane_health.py -v
```

Expected: PASS.

- [ ] **Step 3: Add readiness endpoint**

Modify `api/main.py` with:

```python
@app.get("/api/readiness")
async def readiness():
    return {
        "status": "ready",
        "checks": {
            "api": "ok",
            "registry": "not_configured",
            "audit": "not_configured",
        },
    }
```

- [ ] **Step 4: Extend health test**

Append to `tests/test_control_plane_health.py`:

```python
def test_readiness_endpoint_exists():
    client = TestClient(app)

    response = client.get("/api/readiness")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
```

- [ ] **Step 5: Run health tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_control_plane_health.py -v
```

Expected: PASS.

---

## Production Definition of Done

The system should not be called "完整生产级 IoA 网络" until all checks pass:

- Agents run behind separately addressable runtime services.
- Gateway delivery crosses HTTP boundaries and fails closed when endpoints are missing.
- Registry persists AgentCards and rejects unsigned or tampered cards.
- Cross-domain routes respect topology and explicit authorization scopes.
- Audit traces persist across process restarts and can reconstruct task lineage.
- Protocol adapters support A2A-like, MCP-like, and private API shapes with explicit limitations documented.
- Secrets are passed through environment variables, not committed YAML files.
- Docker Compose can start control plane, database, and at least two Sub-IoA runtime services.
- Testbed risk tests still run after production service split.

---

## Verification Commands

Run from `D:\个人文件\学习文件\实习\IOA测评搭建`:

```powershell
.\.venv\Scripts\python.exe -m compileall src risk_tests tests run_experiment.py
.\.venv\Scripts\python.exe -m pytest tests/test_runtime_service_contracts.py tests/test_signed_agent_registry.py tests/test_gateway_runtime_delivery.py tests/test_persistent_audit.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
docker compose -f deploy/docker-compose.prod.yml config
```

---

## Self-Review

Spec coverage:
- Runtime service split: Task 1.
- Durable identity: Task 2.
- Persistence: Task 3.
- Gateway transport: Task 4.
- Deployment topology: Task 5.
- Control plane readiness: Task 6.

Known remaining sub-projects after this plan:
- Replace HMAC identity with DID/mTLS or certificate chain.
- Add async task bus with Redis/NATS.
- Add OpenTelemetry trace export.
- Implement official MCP server/client compatibility if the paper claims MCP compatibility.
- Add rate limiting and tenant isolation.
- Add migration tooling for PostgreSQL schema changes.
