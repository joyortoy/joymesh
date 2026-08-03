# Fault injection validation

Implemented fail-closed cases:

* production missing signing key → RuntimeError
* production unsigned config → validation error

Full matrix (SIGKILL mid-commit, disk full, outbox corruption, etc.): **pending**.
