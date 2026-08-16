# Fault injection validation

Last updated: 2026-08-03T14:39:25Z

Matrix: **25 cases** in `reports/data/production/fault-injection.json`.

Summary: 24 pass, 0 fail, 1 skip.

| ID | Status | Evidence |
|------|--------|----------|
| FI-01 | pass | tests/test_fault_injection_production.py::test_production_missing_signing_key_fails |
| FI-02 | pass | tests/test_fault_injection_production.py::test_invalid_signature_rejected_by_verifier |
| FI-03 | pass | tests/test_fault_injection_production.py::test_outbox_restore_checksum_mismatch |
| FI-04 | pass | tests/test_fault_injection_production.py::test_backup_interrupt_corrupt_manifest |
| FI-05 | pass | tests/test_fault_injection_production.py::test_outbox_max_entries_from_production_config |
| FI-06 | pass | tests/test_fault_injection_production.py::test_revoked_key_rejected_on_joycli_side |
| FI-07 | pass | tests/test_fault_injection_intake.py::test_commit_durable_before_ack_semantics |
| FI-08 | pass | tests/test_fault_injection_intake.py::test_receive_without_commit_not_visible |
| FI-09 | pass | tests/test_fault_injection_intake.py::test_listener_rejects_oversized_frame |
| FI-10 | pass | tests/test_fault_injection_intake.py::test_restore_rejects_future_schema_version |
| FI-11 | pass | tests/test_fault_injection_intake.py::test_restore_refuses_overwrite_without_force |
| FI-12 | pass | tests/test_fault_injection_intake.py::test_revoked_publisher_key_rejected |
| FI-13 | pass | tests/test_production_readiness.py::test_production_validate_requires_signing_key |
| FI-14 | pass | tests/test_production_readiness.py::test_publisher_fails_closed_in_production |
| FI-15 | pass | tests/test_production_readiness.py::test_key_generate_never_returns_private |
| FI-16 | pass | tests/test_production_readiness.py::test_delivery_backup_restore |
| FI-17 | pass | tests/test_node_production_path.py::test_challenge_signature_roundtrip |
| FI-18 | pass | tests/test_node_production_path.py::test_inline_refused_in_production |
| FI-19 | pass | tests/test_node_production_path.py::test_node_journal_survives_restart_and_blocks_duplicate |
| FI-20 | pass | tests/test_node_production_path.py::test_connector_envelope_signing |
| FI-21 | pass | tests/test_multitenancy_negatives.py::test_foreign_org_cannot_use_wrong_tenant_key |
| FI-22 | pass | tests/test_multitenancy_negatives.py::test_cross_tenant_publish_rejected |
| FI-23 | pass | tests/test_multitenancy_negatives.py::test_list_harnesses_org_scoped_projection_filter |
| FI-24 | pass | tests/test_multitenancy_negatives.py::test_key_id_alone_does_not_authorize_without_matching_org |
| FI-25 | skip | docs/production-deployment.md#walkthrough; requires live systemd intake — not executed on prod-qual |

FI-25 remains **skip** until live Linux systemd intake fault injection is executed.
