#!/usr/bin/env python
"""Run a mixed legacy/generic dataset through one migration entry point."""

from run_business_agent_suite import main


if __name__ == "__main__":
    raise SystemExit(main(default_dataset_profile="unified", require_data=True))
