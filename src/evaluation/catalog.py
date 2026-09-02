"""Single machine-readable catalog for the ten evaluation categories."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CATALOG_PATH = PROJECT_ROOT / "data" / "evaluation_catalog.yaml"


class EvaluationCategory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order: int = Field(ge=1, le=10)
    code: str = Field(pattern=r"^[A-Z]{3}$")
    slug: str = Field(pattern=r"^[a-z][a-z0-9_]+$")
    name_zh: str = Field(min_length=2)
    mechanism: str = Field(min_length=8)
    protocol_case_ids: list[str] = Field(min_length=1)
    online_status: Literal["online_evaluated", "offline_validated"]


class EvaluationCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    formal_track: Literal["ten_item_business_protocol"]
    formal_runner: str
    formal_case_factory: str
    formal_case_loader: str
    formal_result_fields: list[str]
    categories: list[EvaluationCategory] = Field(min_length=10, max_length=10)

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
        if len(self.formal_result_fields) != len(
            set(self.formal_result_fields)
        ):
            raise ValueError("formal result fields must be unique")
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

    def code_for_name_zh(self, name_zh: str) -> str:
        for item in self.categories:
            if item.name_zh == name_zh:
                return item.code
        raise KeyError(name_zh)


@lru_cache(maxsize=1)
def load_evaluation_catalog(
    path: str | Path = DEFAULT_CATALOG_PATH,
) -> EvaluationCatalog:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return EvaluationCatalog.model_validate(payload)


TEN_CATEGORY_CODES = load_evaluation_catalog().category_codes
TEN_CATEGORY_NAMES_ZH = load_evaluation_catalog().category_names_zh
