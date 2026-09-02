#!/usr/bin/env python
"""Run an expandable dataset whose cases carry generic scoring contracts."""

from run_business_agent_suite import main


if __name__ == "__main__":
    raise SystemExit(main(default_dataset_profile="generic_expandable", require_data=True))
