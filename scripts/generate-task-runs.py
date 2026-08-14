from __future__ import annotations

import argparse
import importlib.metadata
import re
from pathlib import Path

from harbor.models.task.task import Task as HarborTask

from slopbench.contracts import PhaseMode, RunManifest
from slopbench.hashing import validate_task, write_model

ROOT = Path(__file__).resolve().parents[1]
IMAGE_PATTERN = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)", re.IGNORECASE)


def image_references(task_dir: Path) -> list[str]:
    references: set[str] = set()
    for dockerfile in task_dir.rglob("Dockerfile"):
        for line in dockerfile.read_text().splitlines():
            match = IMAGE_PATTERN.match(line)
            if match:
                references.add(match.group(1))
    return sorted(references)


def generate(task_dir: Path, output_dir: Path) -> None:
    task_dir = task_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    task, contract_sha, task_digest = validate_task(task_dir)
    task_path = task_dir.relative_to(ROOT).as_posix()
    immutable = {item.path: item.sha256 for item in task.immutable_inputs}
    instruction_layers = [
        {
            "name": phase.name,
            "path": f"{task_path}/{phase.instruction_path}",
            "sha256": immutable[phase.instruction_path],
        }
        for phase in task.phases
    ]
    slug = task.task_id.removeprefix("slopbench/").replace("/", "-")
    common = {
        "schema_version": "slopbench.run.v1",
        "task": {
            "contract_path": f"{task_path}/slopbench-task.json",
            "contract_sha256": contract_sha,
            "task_digest": task_digest,
            "task_id": task.task_id,
            "task_version": task.version,
            "harbor_task_checksum": HarborTask(task_dir).checksum,
        },
        "runtime": {
            "harbor_version": importlib.metadata.version("harbor"),
            "environment_provider": "docker",
            "environment_provider_version": "29.5.2",
            "images": [
                {"role": "fixture-base", "reference": reference}
                for reference in image_references(task_dir)
            ],
            "cpus": task.environment.cpus,
            "memory_mb": task.environment.memory_mb,
            "storage_mb": task.environment.storage_mb,
        },
        "limits": {
            "agent_timeout_sec": 300,
            "agent_setup_timeout_sec": 120,
            "verifier_timeout_sec": 120,
            "max_tokens": None,
            "max_cost_usd": 0.0,
        },
        "retry_policy": {"max_attempts": 1, "retryable_reasons": []},
    }
    variants = [
        ("oracle", "oracle", {"SLOPBENCH_VARIANT": "oracle"}, None),
        ("alternate", "oracle", {"SLOPBENCH_VARIANT": "alternate"}, None),
        ("invalid", "oracle", {"SLOPBENCH_VARIANT": "invalid"}, None),
        ("nop", "nop", {}, None),
    ]
    if task.attack_fixtures:
        variants.append(
            (
                "attack",
                "oracle",
                {},
                task.attack_fixtures[0].id,
            )
        )
    for filename, harness, environment, attack_fixture_id in variants:
        suffix = f"attack-{attack_fixture_id}" if attack_fixture_id is not None else filename
        run_id = f"{slug}-{suffix}"
        agent_environment = dict(environment)
        if task.phase_mode == PhaseMode.SEQUENTIAL and filename in {"attack", "invalid"}:
            agent_environment["SLOPBENCH_TARGET_PHASE"] = task.phases[-1].name
        payload = {
            **common,
            "run_id": run_id,
            "agent": {
                "harness": harness,
                "harness_version": "1.0.0",
                "model": None,
                "effort_tier": "not-applicable",
                "settings": {},
                "environment": agent_environment,
                "tools": (
                    []
                    if harness == "nop"
                    else [{"name": "python", "version": "3.13.15", "settings": {}}]
                ),
                "instruction_layers": instruction_layers,
                "credential_env": [],
            },
            "trial": {"id": run_id, "attempt": 1, "seed": 1},
            "attack_fixture_id": attack_fixture_id,
        }
        write_model(output_dir / f"{filename}.json", RunManifest.model_validate(payload))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    generate(args.task_dir, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
