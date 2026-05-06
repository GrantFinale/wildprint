# Testing — wildprint

Phase 0.7 set up the test harness. This doc tells you how to use it.

## Quick start

```bash
make install                 # one-time: deps + dev deps
make test                    # run unit tests (fast, hermetic, no services)
make all                     # lint + typecheck + test (what CI runs)
```

## Unit vs integration tests

The suite splits along one axis:

- **Unit tests** are the default. They run in <30s, talk to no network,
  use `fakeredis` for Redis and in-memory SQLite for Postgres.
- **Integration tests** are gated behind `@pytest.mark.integration` and
  are **skipped by default**. They need a real Postgres at
  `$DATABASE_URL` and/or a real Redis at `$REDIS_URL`.

Run unit only (default):

```bash
pytest
# or
make test
```

Run integration tests too (requires services running):

```bash
DATABASE_URL=postgresql+psycopg://localhost/wildprint_test \
REDIS_URL=redis://localhost:6379/15 \
pytest --integration
# or
make test-integration
```

Run a single test file or test:

```bash
pytest tests/queue/test_queue.py
pytest tests/db/test_db_scaffold.py::test_uuid7_generates_valid_v7 -v
```

## Coverage

```bash
make cov           # terminal report, fails build under 60%
make cov-html      # also produces htmlcov/index.html
```

The 60% gate (`fail_under = 60` in `pyproject.toml`) applies to **new
modules**. The legacy monolith `review_app/app.py` is excluded from
coverage entirely; the `poster_layout/` renderer is also excluded.

Add new excludes via `[tool.coverage.run] omit = [...]` in
`pyproject.toml` if you ship code that genuinely cannot be unit-tested.

## Adding new tests

1. Mirror the source layout under `tests/`. For `review_app/foo/bar.py`,
   put tests in `tests/foo/test_bar.py`.
2. Add a `tests/foo/__init__.py` (empty) so pytest discovers the package.
3. If your test needs Postgres, Redis, or the network, mark it:

   ```python
   @pytest.mark.integration
   def test_real_postgres(...):
       ...
   ```

4. Use the fixtures provided in `tests/conftest.py`:
   - `app` — the Flask app (calls `create_app(testing=True)`)
   - `client` — Flask test client
   - `db_session` — SAVEPOINT-rolled-back SQLAlchemy session
   - `db_engine` — session-scoped engine (in-memory SQLite by default)
   - `fake_redis` — clean `fakeredis.FakeRedis` per test
   - `mock_user` / `mock_admin_user` — Flask-Login compatible stubs

5. Mark tests >1 s with `@pytest.mark.slow` so reviewers know.

## Markers reference

| Marker                       | Purpose                                          |
|-----------------------------|--------------------------------------------------|
| `@pytest.mark.integration`  | Needs real DB/Redis/network. Skipped by default. |
| `@pytest.mark.slow`         | Takes >1s. Run normally; flag for triage.        |

`--strict-markers` is on, so any unregistered marker will fail collection.
Register new markers in `[tool.pytest.ini_options] markers = [...]`.

## Static analysis

```bash
make lint        # ruff check on review_app/
make typecheck   # mypy strict on review_app/ (legacy app.py is excluded)
```

mypy strict applies to every module in `review_app/` **except**:

- `review_app.app` (legacy monolith — opted out via `[[tool.mypy.overrides]]`)
- `poster_layout/*` (the renderer — opted out)
- `tests/*` (fixtures use `Any` liberally)

When you add a new sub-package under `review_app/`, you do **not** need
to touch the mypy config — strict already applies to everything new.

## CI

Phase 0.8 adds a GitHub Actions workflow that runs `make all` plus a
Docker build on every push and PR. This file describes the local
contract; CI just enforces it.
