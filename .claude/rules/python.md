---
paths: "**/*.py"
---

# Python conventions

- Package management is `uv`. Never call `pip` directly. Add dependencies
  with `uv add`, run things with `uv run`.
- Lint and format with `ruff`: `uv run ruff format .` and
  `uv run ruff check --fix .`. Do not use black, isort, flake8, or pylint.
- Tests are pytest: `uv run pytest -q`. Prefer running the single relevant
  test file over the whole suite while iterating.
- Type-annotate public functions. Use built-in generics (`list[str]`, not
  `List[str]`).
- Use `pathlib` for paths, not `os.path`. Never hardcode backslashes in
  paths. Use `httpx` for HTTP, not `requests`, unless the project already
  uses `requests`.
- Raise specific exceptions. Never catch bare `Exception` to make something
  pass.
