"""Documentation API routes for the IOA console."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/docs", tags=["docs"])

PROJECT_ROOT = Path(__file__).parent.parent.parent
RISK_TEST_DOC = PROJECT_ROOT / "docs" / "18个风险测试具体实现详解.md"


@router.get("/risk-tests")
async def get_risk_test_flow_doc() -> dict:
    """Return the detailed 18-risk-test implementation document."""
    if not RISK_TEST_DOC.exists():
        raise HTTPException(status_code=404, detail="Risk test flow document not found")

    markdown = RISK_TEST_DOC.read_text(encoding="utf-8")
    stat = RISK_TEST_DOC.stat()
    return {
        "title": "18 个 IoA 风险测试具体实现详解",
        "path": str(RISK_TEST_DOC),
        "markdown": markdown,
        "line_count": len(markdown.splitlines()),
        "updated_at": stat.st_mtime,
    }
