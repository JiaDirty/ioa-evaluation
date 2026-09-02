#!/usr/bin/env python
"""Run cases that carry the production declarative scoring contract."""

from run_business_agent_suite import main


if __name__ == "__main__":
    raise SystemExit(main(default_dataset_profile="generic_expandable", require_data=True))
