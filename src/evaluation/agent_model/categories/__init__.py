"""Category executor stubs for the 8 agent model risk types.

Each module provides a run_* function that takes a case, runs the
evaluation pipeline, and returns a ThreeLayerResult.
"""

from .cascade import run_cascade
from .consensus import run_consensus
from .rumor import run_rumor
from .norm import run_norm
from .incentive import run_incentive
from .judgment import run_judgment
from .discussion import run_discussion
from .agency import run_agency

__all__ = [
    "run_cascade", "run_consensus", "run_rumor", "run_norm",
    "run_incentive", "run_judgment", "run_discussion", "run_agency",
]
