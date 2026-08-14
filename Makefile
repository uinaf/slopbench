.PHONY: corpus hardening issue7-corpus review-corpus tracer verify

verify:
	uv sync --locked --all-groups
	uv run ruff format --check --no-cache .
	uv run ruff check --no-cache .
	uv run mypy src
	uv run pytest --cov=slopbench --cov-report=term-missing -q
	@for contract in $$(find tasks -name slopbench-task.json); do \
		uv run slopbench task check "$$(dirname "$$contract")"; \
	done
	@for manifest in $$(find runs -name '*.json'); do \
		uv run slopbench validate run "$$manifest"; \
	done
	@schema_dir="$$(mktemp -d)"; \
	trap 'rm -rf "$$schema_dir"' EXIT; \
	uv run slopbench schema export "$$schema_dir"; \
	diff -ru schemas "$$schema_dir"

tracer:
	sh scripts/run-tracer-matrix.sh

hardening:
	uv run python scripts/run-hardening-matrix.py

corpus:
	uv run python scripts/run-corpus-matrix.py

review-corpus:
	uv run python scripts/run-corpus-matrix.py \
		--task tasks/review/archive-extractor \
		--task tasks/review/webhook-dispatch

issue7-corpus:
	uv run python scripts/run-corpus-matrix.py \
		--task tasks/diagnosis/query-cache-key \
		--task tasks/diagnosis/lease-expiry \
		--task tasks/feature/idempotency-registry \
		--task tasks/feature/event-pagination \
		--task tasks/restraint/header-lookup \
		--task tasks/restraint/config-overrides
