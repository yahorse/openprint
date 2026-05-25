# Contributing to OpenPrint

Thanks for your interest in improving OpenPrint.

## Getting Started

```bash
git clone https://github.com/yahorse/openprint.git
cd openprint
pip install -e ".[dev]"
pytest
```

## Making Changes

1. Fork the repo and create a branch from `main`.
2. Write tests for any new functionality.
3. Run `make lint` and `make test` before submitting.
4. Open a pull request with a clear description.

## Protocol Changes

Changes to the protocol spec (`spec/openprint-protocol-v1.md`) require an issue for discussion before a PR. The protocol is designed to be stable — breaking changes need strong justification.

## Code Style

- Format with `ruff format`
- Lint with `ruff check`
- Type-check with `mypy`
- No comments unless the "why" is non-obvious
