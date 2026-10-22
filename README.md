# Hal Employment

Hal Employment finds jobs that suit you, drafts each application's documents and fills in application forms
for you to check. Hal never presses Submit: you review every application and send it yourself.

This README is a stub while the app is rebuilt; the install guides arrive with the installers.

## Install (developers)

```sh
uv sync --frozen --extra browser --extra documents   # Linux; on Windows leave out --extra documents
```

## Run

```sh
uv run python -m jobhunt --help        # every command
uv run python -m jobhunt serve --open  # the web interface
```

## Test

```sh
scripts/dev/checkpoint.sh --quick   # before each commit
scripts/dev/checkpoint.sh           # every gate, before a pull request
```

[docs/dev/testing.md](docs/dev/testing.md) explains the gates, the fakes and the made-up people.
