# Project structure

```
├── pyproject.toml              # Build config, tools (ruff, mypy, pytest, mutmut)
├── lictor.json                 # Quality gate profiles and control configurations
├── .importlinter               # Architectural contract definitions
├── examples/                   # demo-iva, and prestamos-biblioteca (adopting a project with code)
├── src/lictor/
│   ├── domain/                 # Pure domain: model.py, rules.py, routing.py, errors.py
│   ├── application/
│   │   ├── ports.py            # Protocols (Executor, EventLog, Clock, Vcs, Runner, etc.)
│   │   ├── anchor.py           # What the checkpoint records for a resume to compare
│   │   ├── run_files.py        # Evidence paths under runs/, the agent's copy and the clean tree
│   │   ├── spec_history.py     # Green commits and the spec hash of each in its own tree
│   │   ├── verification_env.py # Verification environment: build, hash, rebuild
│   │   └── usecases/           # Application use case interactors
│   ├── adapters/
│   │   ├── agents/             # Agent executors (Claude CLI, Fake, etc.)
│   │   ├── vcs/                # Version control adapters (git CLI)
│   │   ├── runner/             # Subprocess and command runners
│   │   ├── readers/            # Report parsers (exit_code, junit)
│   │   ├── spec/               # Gherkin parser adapters
│   │   ├── store/              # Event store & clock adapters
│   │   └── config/             # Profile loader and Pydantic schemas
│   ├── orchestration/          # LangGraph state machine & workflows
│   ├── prompts/                # Agent system prompt templates
│   ├── bootstrap.py            # Composition root / dependency injection wire-up
│   └── cli.py                  # Typer CLI entrypoint
└── tests/
    ├── unit/                   # Fast isolated unit tests
    └── integration/            # Architecture tests and cross-layer integration tests
```
