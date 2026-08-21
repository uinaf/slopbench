.PHONY: \
	FORCE corpus hardening issue7-corpus profiles reference-configurations \
	release-candidate review-corpus task-set tracer verify verify-contracts \
	verify-full verify-generated verify-lanes verify-static verify-sync verify-tests

VERIFY_JOBS ?= 4
VERIFY_STATE_DIR := artifacts/verify
VERIFY_INPUT_DIR := $(VERIFY_STATE_DIR)/inputs
VERIFY_STAMP_DIR := $(VERIFY_STATE_DIR)/stamps

SWE_V1_TASKS = \
	tasks/diagnosis/lease-expiry \
	tasks/diagnosis/query-cache-key \
	tasks/domain/fulfillment-plan \
	tasks/domain/pricing-adjustments \
	tasks/feature/event-pagination \
	tasks/feature/idempotency-registry \
	tasks/restraint/config-overrides \
	tasks/restraint/header-lookup \
	tasks/review/archive-extractor \
	tasks/review/webhook-dispatch \
	tasks/state/sync-command \
	tasks/state/watch-subscription

verify:
	+@$(MAKE) --no-print-directory --jobs=$(VERIFY_JOBS) verify-lanes

verify-full:
	+@$(MAKE) --no-print-directory --always-make --jobs=$(VERIFY_JOBS) verify-lanes

verify-lanes: verify-static verify-tests verify-contracts verify-generated

verify-sync:
	uv sync --locked --all-groups

$(VERIFY_INPUT_DIR) $(VERIFY_STAMP_DIR):
	mkdir -p $@

$(VERIFY_INPUT_DIR)/%.sha256: FORCE | verify-sync $(VERIFY_INPUT_DIR)
	@uv run python -m slopbench.repository_verify fingerprint $* $@

$(VERIFY_STAMP_DIR)/static: $(VERIFY_INPUT_DIR)/static.sha256 | $(VERIFY_STAMP_DIR)
	uv run ruff format --check --no-cache .
	uv run ruff check --no-cache .
	uv run mypy src
	@touch $@

$(VERIFY_STAMP_DIR)/tests: $(VERIFY_INPUT_DIR)/tests.sha256 | $(VERIFY_STAMP_DIR)
	uv run pytest --cov=slopbench --cov-report=term-missing -q
	@touch $@

$(VERIFY_STAMP_DIR)/contracts: $(VERIFY_INPUT_DIR)/contracts.sha256 | $(VERIFY_STAMP_DIR)
	uv run python -m slopbench.repository_verify contracts
	@touch $@

$(VERIFY_STAMP_DIR)/generated: $(VERIFY_INPUT_DIR)/generated.sha256 | $(VERIFY_STAMP_DIR)
	@task_set="$$(mktemp)"; \
	trap 'rm -f "$$task_set"' EXIT; \
	uv run python scripts/generate-task-set.py \
		--task-set-id slopbench-swe-v1-dev \
		--version 0.3.0 \
		--visibility public \
		"$$task_set" $(SWE_V1_TASKS); \
	diff -u datasets/slopbench-swe-v1-dev.json "$$task_set"; \
	uv run slopbench task-set "$$task_set" --root .
	@schema_dir="$$(mktemp -d)"; \
	trap 'rm -rf "$$schema_dir"' EXIT; \
	uv run slopbench schema export "$$schema_dir"; \
	diff -ru schemas "$$schema_dir"
	@release_dir="$$(mktemp -d)"; \
	trap 'rm -rf "$$release_dir"' EXIT; \
	uv run python scripts/generate-release-evidence.py \
		"$$release_dir/slopbench-swe-v1-dev-evidence.json"; \
	uv run slopbench release audit \
		--manifest "$$release_dir/slopbench-swe-v1-dev-evidence.json" \
		--project-root . \
		--output "$$release_dir/slopbench-swe-v1-dev-readiness.json"; \
	diff -u release/slopbench-swe-v1-dev-evidence.json \
		"$$release_dir/slopbench-swe-v1-dev-evidence.json"; \
	diff -u release/slopbench-swe-v1-dev-readiness.json \
		"$$release_dir/slopbench-swe-v1-dev-readiness.json"
	@touch $@

verify-static: $(VERIFY_STAMP_DIR)/static

verify-tests: $(VERIFY_STAMP_DIR)/tests

verify-contracts: $(VERIFY_STAMP_DIR)/contracts

verify-generated: $(VERIFY_STAMP_DIR)/generated

FORCE:

task-set:
	uv run python scripts/generate-task-set.py \
		--task-set-id slopbench-swe-v1-dev \
		--version 0.3.0 \
		--visibility public \
		datasets/slopbench-swe-v1-dev.json $(SWE_V1_TASKS)
	uv run slopbench task-set datasets/slopbench-swe-v1-dev.json --root .

profiles:
	@for profile in profiles/*.json; do \
		uv run slopbench validate profile "$$profile"; \
	done

reference-configurations:
	@for configuration in reference-configurations/*.json; do \
		uv run slopbench validate reference-configuration "$$configuration"; \
	done

release-candidate:
	uv run python scripts/generate-release-evidence.py \
		release/slopbench-swe-v1-dev-evidence.json
	uv run slopbench release audit \
		--manifest release/slopbench-swe-v1-dev-evidence.json \
		--project-root . \
		--output release/slopbench-swe-v1-dev-readiness.json

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
