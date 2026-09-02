"""Removed migration name; use scripts/migrations/migrate_scenario_tasks_v1.py."""
from scripts.migrations.migrate_scenario_tasks_v1 import *
from scripts.migrations.migrate_scenario_tasks_v1 import _task_id, _historical_tasks, _candidate_tasks

if __name__ == "__main__":
    from scripts.migrations.migrate_scenario_tasks_v1 import main
    raise SystemExit(main())
