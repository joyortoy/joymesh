# Alerts

Recommended thresholds (starting points, not SLAs):

| Alert | Signal | Default |
|-------|--------|---------|
| Service not ready | readiness.ready=false | 2m |
| Listener unbound | listener_bound=false | 1m |
| Signature failures | signature_failures_total rate | >5/5m |
| Tenant mismatches | tenant_mismatch_total | >0/5m |
| Outbox growth | outbox_depth | >100 for 10m |
| Oldest outbox age | oldest_outbox_age_seconds | >300 |
| Reconciliation backlog | reconciliation_required_count | >10 |
| DB growth | database_size_bytes | >80% of max |
| Restart loops | systemd NRestarts | >3/15m |

Use `scripts/production/run_qualification.py` samples plus intake/delivery health JSON as scrape inputs.
