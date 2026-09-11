# Catalogue schema

Each `.yaml` file is JSON-compatible YAML validated as an immutable `ConnectorDefinition`.
Required metadata includes a revision, product identity, maturity, execution mode, platform
support, official source review, and installation fingerprint.

Installation options contain a fixed executable and argument vector. They cannot contain shell
operators, a shell executable, or privilege escalation. Official scripts are non-executable
catalogue records until a node fetches the allowlisted URL and binds a reviewed digest.

`declared`, `observed`, and `certified` capability evidence are intentionally separate. Routing
must use only certified evidence.
