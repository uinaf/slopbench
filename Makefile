.PHONY: tracer verify

verify:
	uv sync --locked --all-groups
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy src
	uv run pytest --cov=slopbench --cov-report=term-missing -q
	uv run slopbench task check tasks/tracer
	@for manifest in runs/tracer/*.json; do uv run slopbench validate run "$$manifest"; done
	@schema_dir="$$(mktemp -d)"; \
	trap 'rm -rf "$$schema_dir"' EXIT; \
	uv run slopbench schema export "$$schema_dir"; \
	diff -ru schemas "$$schema_dir"

tracer:
	sh scripts/run-tracer-matrix.sh
