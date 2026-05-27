"""FastAPI 主应用。"""

from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录在 path 中
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import experiments, feedback, agents, docs

app = FastAPI(title="IOA 测评控制台 API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(experiments.router)
app.include_router(feedback.router)
app.include_router(agents.router)
app.include_router(docs.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "ioa-console-api"}
