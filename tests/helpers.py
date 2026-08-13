from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from slopbench.contracts import (
    AgentReport,
    GateName,
    ResultBundle,
    RunManifest,
    TaskContract,
    VerificationEvidence,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
REVISION = f"sha256:{'c' * 64}"
GIT_REVISION = "d" * 40


def parse_json(model_type: type[Any], payload: dict[str, Any]) -> Any:
    return model_type.model_validate_json(json.dumps(payload))


def task_payload(*, sealed: bool = True, sequential: bool = False) -> dict[str, Any]:
    phases = [
        {
            "name": "implement",
            "instruction_path": "instruction.md",
            "context": "fresh",
        }
    ]
    if sequential:
        phases.append(
            {
                "name": "review",
                "instruction_path": "review.md",
                "context": "fresh",
            }
        )
    inputs = []
    if sealed:
        inputs = [
            {"path": "instruction.md", "sha256": SHA_A},
            {"path": "tests/test.sh", "sha256": SHA_B},
        ]
        if sequential:
            inputs.append({"path": "review.md", "sha256": "e" * 64})
    return {
        "schema_version": "slopbench.task.v1",
        "task_id": "slopbench/tracer/example",
        "version": "1.0.0",
        "kind": "patch",
        "phase_mode": "sequential" if sequential else "single",
        "phases": phases,
        "environment": {
            "harbor_task_path": ".",
            "verifier_isolation": "separate",
            "base_revision": GIT_REVISION,
            "cpus": 2,
            "memory_mb": 2048,
            "storage_mb": 4096,
        },
        "verifier": {
            "kind": "deterministic",
            "entrypoint": "tests/test.sh",
            "evidence_path": "/logs/verifier/slopbench-verification.json",
            "reward_path": "/logs/verifier/reward.json",
        },
        "capabilities": {
            "repository": "read-write",
            "shell": True,
            "tests": True,
            "tools": ["git", "python"],
            "network": "model-only",
            "network_allowed_hosts": ["cursor.com"],
            "environment": ["SLOPBENCH_VARIANT"],
            "external_writes": "none",
            "live_credentials": False,
        },
        "applicable_gates": [
            "requested_behavior",
            "regressions",
            "build_and_types",
            "authority",
            "verifier_integrity",
            "evidence_receipt",
        ],
        "provenance": {
            "origin": "slopbench-authored",
            "source_url": None,
            "source_revision": None,
            "transformed": False,
            "ai_assistance": None,
        },
        "license": {"spdx": "MIT", "holder": "uinaf contributors"},
        "immutable_inputs": inputs,
    }


def task_contract(**kwargs: bool) -> TaskContract:
    return parse_json(TaskContract, task_payload(**kwargs))


def run_payload(*, harness: str = "oracle", variant: str = "oracle") -> dict[str, Any]:
    return {
        "schema_version": "slopbench.run.v1",
        "run_id": f"tracer-{variant}",
        "task": {
            "contract_path": "tasks/tracer/slopbench-task.json",
            "contract_sha256": SHA_A,
            "task_digest": SHA_B,
            "task_id": "slopbench/tracer/example",
            "task_version": "1.0.0",
            "harbor_task_checksum": SHA_A,
        },
        "agent": {
            "harness": harness,
            "harness_version": "1.0.0",
            "model": None,
            "effort_tier": "not-applicable",
            "settings": {},
            "environment": {"SLOPBENCH_VARIANT": variant},
            "tools": [{"name": "python", "version": "3.13.15", "settings": {}}],
            "instruction_layers": [
                {"name": "task", "path": "tasks/tracer/instruction.md", "sha256": SHA_A}
            ],
            "credential_env": [],
        },
        "runtime": {
            "harbor_version": "0.16.1",
            "environment_provider": "docker",
            "environment_provider_version": "29.7.2",
            "images": [
                {
                    "role": "fixture-base",
                    "reference": f"python:3.13@sha256:{'f' * 64}",
                }
            ],
            "cpus": 2,
            "memory_mb": 2048,
            "storage_mb": 4096,
        },
        "limits": {
            "agent_timeout_sec": 300,
            "agent_setup_timeout_sec": 300,
            "verifier_timeout_sec": 120,
            "max_tokens": None,
            "max_cost_usd": 0.0,
        },
        "trial": {"id": f"tracer-{variant}", "attempt": 1, "seed": 7},
        "retry_policy": {"max_attempts": 1, "retryable_reasons": []},
        "attack_fixture_id": None,
    }


def run_manifest(**kwargs: str) -> RunManifest:
    return parse_json(RunManifest, run_payload(**kwargs))


def report_payload(*, public_passed: bool = True) -> dict[str, Any]:
    status = "passed" if public_passed else "failed"
    return {
        "schema_version": "slopbench.report.v1",
        "task_digest": SHA_B,
        "base_revision": GIT_REVISION,
        "claims": [
            {
                "gate": "requested_behavior",
                "status": status,
                "evidence_ids": ["requested-contract"],
            },
            {
                "gate": "regressions",
                "status": status,
                "evidence_ids": ["public-regressions"],
            },
            {
                "gate": "build_and_types",
                "status": "passed",
                "evidence_ids": ["build-and-types"],
            },
            {
                "gate": "authority",
                "status": "passed",
                "evidence_ids": ["authority-scope"],
            },
            {
                "gate": "verifier_integrity",
                "status": "uncertain",
                "evidence_ids": ["verifier-integrity"],
            },
            {
                "gate": "evidence_receipt",
                "status": "passed",
                "evidence_ids": ["evidence-receipt"],
            },
        ],
        "commands": [
            {
                "id": "public-regressions",
                "command": "python -m unittest discover -s tests -v",
                "exit_code": 0 if public_passed else 1,
            }
        ],
        "uncertainty": [{"code": "hidden-verifier", "detail": "Checked after the agent phase."}],
        "final_revision": REVISION,
    }


def agent_report(**kwargs: bool) -> AgentReport:
    return parse_json(AgentReport, report_payload(**kwargs))


def verification_payload(*, requested_passed: bool = True) -> dict[str, Any]:
    checks = []
    commands = {
        GateName.REQUESTED_BEHAVIOR: "python /tests/check_requested.py",
        GateName.REGRESSIONS: "python -m unittest discover -s tests -v",
        GateName.BUILD_AND_TYPES: "python -m compileall -q src tests tools",
        GateName.AUTHORITY: "python /tests/check_authority.py",
        GateName.VERIFIER_INTEGRITY: "python /tests/check_integrity.py",
        GateName.EVIDENCE_RECEIPT: "python /tests/check_receipt.py",
    }
    ids = {
        GateName.REQUESTED_BEHAVIOR: "requested-contract",
        GateName.REGRESSIONS: "public-regressions",
        GateName.BUILD_AND_TYPES: "build-and-types",
        GateName.AUTHORITY: "authority-scope",
        GateName.VERIFIER_INTEGRITY: "verifier-integrity",
        GateName.EVIDENCE_RECEIPT: "evidence-receipt",
    }
    for gate in commands:
        passed = (
            requested_passed
            if gate
            in {
                GateName.REQUESTED_BEHAVIOR,
                GateName.REGRESSIONS,
            }
            else True
        )
        checks.append(
            {
                "id": ids[gate],
                "gate": gate.value,
                "passed": passed,
                "command": commands[gate],
                "exit_code": 0 if passed else 1,
                "log_path": f"test-{ids[gate]}.txt",
                "log_sha256": SHA_A,
            }
        )
    return {
        "schema_version": "slopbench.verification.v1",
        "task_digest": SHA_B,
        "base_revision": GIT_REVISION,
        "final_revision": REVISION,
        "checks": checks,
    }


def verification_evidence(**kwargs: bool) -> VerificationEvidence:
    return parse_json(VerificationEvidence, verification_payload(**kwargs))


def result_payload(*, classification: str = "valid_pass") -> dict[str, Any]:
    passed = classification == "valid_pass"
    infrastructure = classification == "infrastructure_failure"
    return {
        "schema_version": "slopbench.result.v1",
        "run_id": "tracer-oracle",
        "task_digest": SHA_B,
        "run_manifest_sha256": SHA_A,
        "classification": classification,
        "failure_reason": (
            "none" if passed else ("harbor_process_failure" if infrastructure else "gate_failure")
        ),
        "completed": passed,
        "attempt": 1,
        "retry": {
            "eligible": False,
            "decision": (
                "reason_not_allowed" if infrastructure else "classification_not_retryable"
            ),
            "remaining_attempts": 0,
        },
        "outcomes": [
            {
                "gate": gate.value,
                "status": (
                    "not_applicable"
                    if gate == GateName.SAFETY_TYPE_ESCAPES
                    else ("passed" if passed else "failed")
                ),
                "check_ids": [],
            }
            for gate in GateName
        ],
        "receipt": {"present": True, "valid": passed, "sha256": SHA_A, "errors": []},
        "usage": {
            "input_tokens": None,
            "cache_tokens": None,
            "output_tokens": None,
            "cost_usd": None,
        },
        "timing": {"started_at": None, "finished_at": None, "duration_seconds": None},
        "harbor": {
            "version": "0.16.1",
            "task_checksum": "checksum",
            "result_sha256": SHA_A,
            "config_sha256": SHA_B,
            "trajectory_sha256": None,
        },
        "artifacts": [],
    }


def result_bundle(**kwargs: str) -> ResultBundle:
    return parse_json(ResultBundle, result_payload(**kwargs))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
