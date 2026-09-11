# Harness installation and authentication

Browser onboarding requests plans from the paired node. Approval is bound to the
exact user, browser session, node, harness, executable, argv, working directory,
filtered environment, risk level, plan hash, and expiry. Any difference makes
the plan stale. Execution uses argv directly without a shell and never elevates
with implicit `sudo`; failure for one selected harness does not stop unrelated
plans.

Normal discovery and routing never install, upgrade, uninstall, or authenticate
software.

```python
plan = mesh.plan_install("gemini-cli")
# Inspect plan.argv, plan.source, and plan.notes.

approved = ApprovalToken(
    action=plan.action,
    harness_id=plan.harness_id,
    approved=True,
    nonce="consumer-generated-unique-value",
)
result = await mesh.execute_lifecycle_plan(
    plan.model_copy(update={"dry_run": False}),
    approval=approved,
)
```

Plans use argument vectors, never shell strings. Shells, command separators, and
automatic privilege escalation are rejected. The execution result redacts
secrets and invalidates discovery caches. Login planning uses the same approval
boundary and never reads credential stores or returns raw tokens.

The CLI prints plans by default:

```text
joymesh harness install gemini-cli
joymesh harness upgrade codex
```

Only `--approve` executes an install or upgrade. Login and certification remain
plan-only until a consumer explicitly implements the corresponding interactive
approval experience.
