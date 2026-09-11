# Workspace permissions

Workspace grants are node-enforced roots with independent read, write, and shell
permissions. The browser selects a registered workspace identity; it does not
send arbitrary absolute paths as authority.

Before access, the node resolves the configured root and candidate path. It
rejects `..` traversal, absolute-path escape, and symlink escape, including a
symlink in an existing parent of a not-yet-created file. Revoked grants fail
closed.

Shell capability does not imply unrestricted execution. Harness launch
specifications, lifecycle plans, and task approvals remain distinct controls.
