"""Single machine-readable catalog: ten categories, eleven leaf branches, quotas.

This module is the only source of truth for the eleven evaluation branches and
the per-branch release quota.  No other production module may hardcode branch
IDs, names, ordering or quotas.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CATALOG_PATH = PROJECT_ROOT / "data" / "catalog" / "evaluation_catalog.yaml"


class EvaluationCategory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=1, le=10)
    code: str = Field(pattern=r"^[A-Z]{3}$")
    slug: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    name_zh: str = Field(min_length=2)
    mechanism: str = Field(min_length=8)
    protocol_case_ids: list[str] = Field(min_length=1)
    online_status: Literal["online_evaluated", "offline_validated"]


class EvaluationBranch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branch_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,40}$")
    category: str = Field(pattern=r"^[A-Z]{3}$")
    subtype: str | None = Field(default=None, max_length=40)
    name_zh: str = Field(min_length=2)
    quota: int = Field(default=5, ge=1)


class EvaluationCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1"] = "1.1"
    formal_track: Literal["ten_item_business_protocol"]
    formal_runner: str
    formal_case_loader: str
    formal_result_fields: list[str]
    categories: list[EvaluationCategory] = Field(min_length=10, max_length=10)
    branches: list[EvaluationBranch] = Field(min_length=11, max_length=11)

    @model_validator(mode="after")
    def validate_unique_catalog(self) -> "EvaluationCatalog":
        orders = [item.order for item in self.categories]
        codes = [item.code for item in self.categories]
        slugs = [item.slug for item in self.categories]
        case_ids = [
            case_id
            for item in self.categories
            for case_id in item.protocol_case_ids
        ]
        if orders != list(range(1, 11)):
            raise ValueError("category order must be exactly 1 through 10")
        if len(codes) != len(set(codes)):
            raise ValueError("category codes must be unique")
        if len(slugs) != len(set(slugs)):
            raise ValueError("category slugs must be unique")
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("protocol case IDs must be unique")
        if len(self.formal_result_fields) != len(set(self.formal_result_fields)):
            raise ValueError("formal result fields must be unique")
        branch_ids = [item.branch_id for item in self.branches]
        if len(branch_ids) != len(set(branch_ids)):
            raise ValueError("branch ids must be unique")
        unknown_categories = sorted(
            {item.category for item in self.branches} - set(codes)
        )
        if unknown_categories:
            raise ValueError(
                f"branches reference unknown categories: {unknown_categories}"
            )
        return self

    @property
    def category_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.categories)

    @property
    def protocol_case_ids(self) -> tuple[str, ...]:
        return tuple(
            case_id
            for item in self.categories
            for case_id in item.protocol_case_ids
        )

    @property
    def category_names_zh(self) -> tuple[str, ...]:
        return tuple(item.name_zh for item in self.categories)

    @property
    def branch_ids(self) -> tuple[str, ...]:
        return tuple(item.branch_id for item in self.branches)

    @property
    def release_quota(self) -> dict[str, int]:
        return {item.branch_id: item.quota for item in self.branches}

    @property
    def total_release_quota(self) -> int:
        return sum(item.quota for item in self.branches)

    def code_for_name_zh(self, name_zh: str) -> str:
        for item in self.categories:
            if item.name_zh == name_zh:
                return item.code
        raise KeyError(name_zh)

    def branch_for_id(self, branch_id: str) -> EvaluationBranch:
        for item in self.branches:
            if item.branch_id == branch_id:
                return item
        raise KeyError(f"unknown branch id: {branch_id}")

    def branches_for_category(self, code: str) -> list[EvaluationBranch]:
        return [item for item in self.branches if item.category == code]

    def branch_for_case(self, category: str, subtype: str | None = None) -> EvaluationBranch:
        matches = [
            item
            for item in self.branches
            if item.category == category and item.subtype == subtype
        ]
        if len(matches) == 1:
            return matches[0]
        plain = [
            item
            for item in self.branches
            if item.category == category and item.subtype is None
        ]
        if len(plain) == 1:
            return plain[0]
        raise KeyError(
            f"no branch for category={category!r} subtype={subtype!r}"
        )


@lru_cache(maxsize=1)
def load_evaluation_catalog(
    path: str | Path = DEFAULT_CATALOG_PATH,
) -> EvaluationCatalog:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return EvaluationCatalog.model_validate(payload)


TEN_CATEGORY_CODES = load_evaluation_catalog().category_codes
TEN_CATEGORY_NAMES_ZH = load_evaluation_catalog().category_names_zh
ELEVEN_BRANCH_IDS = load_evaluation_catalog().branch_ids


__all__ = [
    "DEFAULT_CATALOG_PATH",
    "ELEVEN_BRANCH_IDS",
    "EvaluationBranch",
    "EvaluationCatalog",
    "EvaluationCategory",
    "TEN_CATEGORY_CODES",
    "TEN_CATEGORY_NAMES_ZH",
    "load_evaluation_catalog",
]
