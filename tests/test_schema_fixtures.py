from __future__ import annotations

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
