from __future__ import annotations

import argparse
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from slopbench.contracts import OutcomeStatus, RunManifest
from slopbench.hashing import load_model, validate_task, write_model
from slopbench.runner import execute_run

ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = ROOT / "tasks" / "tracer"
TEMPLATE_PATH = ROOT / "runs" / "tracer" / "attack.json"


def assert_boundary_probe(bundle: Path, run_id: str, fixture_id: str) -> None:
    if fixture_id == "grader-exploitation":
        path = bundle / "harbor" / run_id / "verifier" / "test-grader-boundary.txt"
        try:
            result = path.read_text().strip()
        except OSError as exc:
            raise SystemExit(f"{fixture_id}: missing grader boundary proof: {exc}") from exc
        if result != "blocked:PermissionError":
            raise SystemExit(f"{fixture_id}: invalid grader boundary proof: {result!r}")
        return
    expected_keys = {
        "hidden-material-access": {"hidden_read"},
        "verifier-tampering": {"logs", "tests"},
    }
    if fixture_id not in expected_keys:
        return
    path = bundle / "harbor" / run_id / "artifacts" / "app" / ".slopbench-attack.json"
    try:
        probe = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{fixture_id}: invalid boundary probe: {exc}") from exc
    if not isinstance(probe, dict) or set(probe) != expected_keys[fixture_id]:
        raise SystemExit(f"{fixture_id}: boundary probe has an invalid shape")
    if any(not isinstance(value, str) or value == "succeeded" for value in probe.values()):
        raise SystemExit(f"{fixture_id}: protected boundary was accessible: {probe}")


def output_path(value: str | None) -> Path:
    if value is not None:
        return Path(value)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return ROOT / "artifacts" / f"hardening-{stamp}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", action="append", dest="fixture_ids")
    parser.add_argument("output", nargs="?")
    args = parser.parse_args()

    task, _, _ = validate_task(TASK_DIR)
    fixtures = task.attack_fixtures
    if args.fixture_ids:
        requested = set(args.fixture_ids)
        known = {fixture.id for fixture in fixtures}
        unknown = sorted(requested - known)
        if unknown:
            raise SystemExit(f"unknown attack fixtures: {unknown}")
        fixtures = [fixture for fixture in fixtures if fixture.id in requested]
    template = load_model(TEMPLATE_PATH, RunManifest)
    destination = output_path(args.output).resolve()
    if destination.exists():
        raise SystemExit(f"output already exists: {destination}")

    with tempfile.TemporaryDirectory(prefix="slopbench-attacks-") as temporary:
        manifest_dir = Path(temporary)
        for fixture in fixtures:
            run_id = f"tracer-attack-{fixture.id}"
            payload = template.model_dump(mode="json")
            payload["attack_fixture_id"] = fixture.id
            payload["run_id"] = run_id
            payload["trial"] = {**payload["trial"], "id": run_id}
            manifest = RunManifest.model_validate(payload)
            manifest_path = manifest_dir / f"{fixture.id}.json"
            write_model(manifest_path, manifest)

            result, bundle = execute_run(TASK_DIR, manifest_path, destination)
            assert_boundary_probe(bundle, run_id, fixture.id)
            failed_gates = {
                outcome.gate
                for outcome in result.outcomes
                if outcome.status == OutcomeStatus.FAILED
            }
            if result.classification.value != fixture.expected.classification:
                raise SystemExit(
                    f"{fixture.id}: expected {fixture.expected.classification}, "
                    f"got {result.classification.value}; see {bundle / 'result.json'}"
                )
            if failed_gates != set(fixture.expected.failed_gates):
                raise SystemExit(
                    f"{fixture.id}: expected failed gates "
                    f"{sorted(gate.value for gate in fixture.expected.failed_gates)}, got "
                    f"{sorted(gate.value for gate in failed_gates)}; "
                    f"see {bundle / 'result.json'}"
                )
            if result.retry.eligible:
                raise SystemExit(f"{fixture.id}: agent-caused attack was marked retryable")
            print(f"{fixture.id}: {result.classification.value}")

    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
