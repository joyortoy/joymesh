# JoyMesh

JoyMesh is a mission-oriented orchestration runtime that manages multiple AI
harnesses, agents, tools, and human participants through a persistent mission
graph with verification-driven execution.

## Project status

JoyMesh is at the foundation stage. The repository currently defines its Codex
development environment and engineering expectations; the runtime architecture
and implementation are still to be designed.

## Codex environment

Open this repository in the Codex desktop app and trust the project to load its
shared configuration. New worktrees automatically run `./scripts/setup.sh`.

The environment provides a **Check** action in the Codex toolbar. You can run
the same validation from a terminal:

```sh
./scripts/check.sh
```

The repository includes:

- `AGENTS.md` for durable project and review guidance
- `.codex/config.toml` for project-scoped approval, sandbox, and network defaults
- `.codex/environments/environment.toml` for worktree setup and Codex actions
- `scripts/setup.sh` for reproducible workspace initialization
- `scripts/check.sh` for repository validation

No language runtime or package manager is required yet. Add stack-specific
installation and validation commands when the first implementation is chosen.
