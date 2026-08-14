from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from slopbench.contracts import (
    AgentReport,
    ResultBundle,
    RunManifest,
    TaskContract,
    VerificationEvidence,
)
from slopbench.hashing import ContractError, load_model

FIXTURES = Path(__file__).parent / "fixtures" / "contracts"
MODELS: dict[str, type[BaseModel]] = {
    "task": TaskContract,
    "run": RunManifest,
    "report": AgentReport,
    "verification": VerificationEvidence,
    "result": ResultBundle,
}


@pytest.mark.parametrize(("name", "model"), MODELS.items())
def test_valid_schema_fixture(name: str, model: type[BaseModel]) -> None:
    assert isinstance(load_model(FIXTURES / "valid" / f"{name}.json", model), model)


@pytest.mark.parametrize(("name", "model"), MODELS.items())
def test_malformed_schema_fixture(name: str, model: type[BaseModel]) -> None:
    with pytest.raises(ContractError, match=f"invalid {model.__name__}"):
        load_model(FIXTURES / "malformed" / f"{name}.json", model)


@pytest.mark.parametrize(("name", "model"), MODELS.items())
def test_schema_version_is_required(name: str, model: type[BaseModel], tmp_path: Path) -> None:
    payload = json.loads((FIXTURES / "valid" / f"{name}.json").read_text())
    del payload["schema_version"]
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(ContractError, match="schema_version"):
        load_model(path, model)
    assert "schema_version" in model.model_json_schema()["required"]
