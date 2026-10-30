# Contributing

Open an issue for bugs or proposed changes, or submit a focused pull request.
For vulnerabilities, follow [SECURITY.md](SECURITY.md) instead of opening an issue.
Be respectful and constructive in issues and reviews.

## Local checks

Install Python 3.12 or newer and uv, then run:

```sh
uv sync --frozen
uv run ruff check src tests
uv run mypy src
uv run pytest -q
```

Include tests for behavior changes. Tests must use sanitized fixtures and must
not contact a real robot or iRobot account. Never commit credentials or personal
data. Follow [tests/fixtures/README.md](tests/fixtures/README.md) for captures.

Preserve the environment-only write gates, authenticated non-loopback HTTP,
and the rule that timed-out writes are never replayed on another transport.
Describe the change, its safety impact, and validation in your pull request.
