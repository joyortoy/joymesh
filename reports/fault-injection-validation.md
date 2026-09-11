# Fault injection validation

Updated: 2026-08-03T19:17:23Z

## Summary

**25/25 pass** on production qualification path (Linux x86-64 Lima prod-qual for FI-25; prior cases retained).

| ID | Status | Notes |
|----|--------|-------|
| FI-01..FI-24 | pass | Prior packaged evidence in fault-injection.json |
| FI-25 | **pass** | SIGKILL of live joycli-runtime-intake MainPID on prod-qual; systemd Restart=on-failure respawned; SQLite integrity ok. Mid-commit in-flight write was **not** forced in this brief attempt. |

Evidence: reports/data/production/fi25-result.json, fault-injection.json.

Caveat: unit had to avoid JOYCLI_RUNTIME_SOCKET env + intake-serve --socket double-bind (composition auto-starts listener when env is set).
