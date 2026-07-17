from .base import build_default_rubrics

RUBRIC = next(r for r in build_default_rubrics() if r.attack_type == "ecosystem_consensus")
