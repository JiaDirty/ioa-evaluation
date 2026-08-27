"""Deterministic authoritative fact lookup tool.

Only returns data from the current case's authoritative_tool_fixture.
The tested model calls this via tool gateway — never direct access.
"""

from __future__ import annotations

from typing import Any


class AuthoritativeFactLookup:
    """Resolves fact-check requests from the current case's fixture."""

    def __init__(
        self,
        fixture: dict[str, Any] | None = None,
        aliases: list[str] | None = None,
    ):
        self._fixture = fixture or {}
        self._aliases = self._build_aliases(aliases or [])

    def query(self, lookup_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """Look up a fact by query key. Returns fixture data or empty."""
        key = str(
            lookup_id
            or kwargs.get("claim_id")
            or kwargs.get("query")
            or kwargs.get("claim")
            or kwargs.get("rumor_id")
            or kwargs.get("report_id")
            or kwargs.get("source_id")
            or ""
        )
        if key in self._aliases or any(
            alias and (alias in key or key in alias) for alias in self._aliases
        ):
            return self._success_response(key)
        return {
            "lookup_id": key,
            "query_key": key,
            "claim_id": key,
            "supported": None,
            "fixture_hit": False,
            "semantic_success": False,
            "canonical_fact": "no fixture data for this key",
            "source_id": "unknown",
        }

    def query_authoritative_fact(
        self,
        lookup_id: str | None = None,
        claim_id: str | None = None,
        query: str | None = None,
        claim: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Public API matching tool gateway dispatch convention."""
        candidates = [lookup_id, claim_id, query, claim]
        last_result: dict[str, Any] | None = None
        for candidate in candidates:
            if not candidate:
                continue
            result = self.query(candidate, **kwargs)
            if result.get("fixture_hit") is True:
                return result
            last_result = result
        return last_result or self.query("", **kwargs)

    def _success_response(self, lookup_id: str) -> dict[str, Any]:
        response = dict(self._fixture.get("response", {}))
        response.setdefault("lookup_id", lookup_id)
        response.setdefault("query_key", self._fixture.get("query_key", lookup_id))
        response["fixture_hit"] = True
        response["semantic_success"] = True
        return response

    def _build_aliases(self, aliases: list[str]) -> set[str]:
        values = {str(item) for item in aliases if item}
        query_key = self._fixture.get("query_key")
        if query_key:
            values.add(str(query_key))
        response = self._fixture.get("response", {})
        if isinstance(response, dict):
            for field in (
                "claim_id",
                "rumor_id",
                "report_id",
                "source_id",
                "authority_source_id",
            ):
                value = response.get(field)
                if value:
                    values.add(str(value))
            for field in ("authority_source_ids", "source_ids"):
                value = response.get(field)
                if isinstance(value, list):
                    values.update(str(item) for item in value if item)
        return values
