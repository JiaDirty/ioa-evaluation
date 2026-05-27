"""Experiment-layer exceptions used to preserve evaluation validity."""


class EvaluationInvalidError(RuntimeError):
    """Raised when a risk test cannot produce a valid scientific judgment."""
