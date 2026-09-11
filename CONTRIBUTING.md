# Contributing to JoyMesh

Thanks for helping make coding-agent tools easier to integrate. Contributions
of all sizes are useful, including connector metadata, tests, documentation,
examples, and bug reports.

## Before you start

- For a small fix, open a pull request directly.
- For a larger feature or architectural change, open an issue first so the
  intended behavior and scope can be agreed before substantial work begins.
- Never include provider credentials, session data, proprietary prompts, or
  private workspace content in an issue, fixture, log, or pull request.

## Development setup

JoyMesh requires Python 3.12 and
[uv](https://docs.astral.sh/uv/getting-started/installation/).

```sh
git clone https://github.com/joyortoy/joymesh.git
cd joymesh
uv sync --extra dev
```

Run the validation suite before submitting a pull request:

```sh
uv run pytest
uv run ruff check .
uv run mypy src
```

## Good first contributions

- Improve a connector definition in `src/joymesh/connectors/catalogue/`.
- Add or clarify connector documentation in `docs/connectors/`.
- Add a focused regression test for an existing behavior.
- Turn a manual setup or troubleshooting step into a reproducible example.

Connector changes should follow
[the connector contribution guide](docs/connectors/adding-a-connector.md) and
must not claim certification without version-specific evidence.

## Pull requests

Keep each pull request focused. Include:

- the problem being solved;
- the behavior that changed;
- the checks you ran;
- any security, compatibility, or migration considerations.

New behavior should include tests. User-facing changes should update the
relevant documentation. By contributing, you agree that your contribution is
licensed under the repository's Apache License 2.0.
