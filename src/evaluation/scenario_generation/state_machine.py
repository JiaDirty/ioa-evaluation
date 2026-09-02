"""Strict pipeline stages and transition validation."""
from .orchestrator import PipelineStage, validate_transition

__all__ = ["PipelineStage", "validate_transition"]
