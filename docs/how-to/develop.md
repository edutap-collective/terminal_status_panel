# Work on the panel

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev,test]'
ruff check src tests
python -m pytest
```

CI (`.github/workflows/ci.yml`) runs ruff and the test suite on every push and
pull request across Python 3.11–3.13.
