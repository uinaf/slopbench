"""Versioned coverage of sanitized coding-agent behavior rules."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from slopbench.contracts import ContractModel, GateName, Identifier, Sha256Hex, Version
from slopbench.hashing import ContractError, load_model
from slopbench.release import (
    TaskId,
    TaskSetManifest,
    VersionBinding,
    task_set_binding,
)

COVERAGE_SCHEMA_VERSION: Literal["slopbench.coverage.v1"] = "slopbench.coverage.v1"


class CoverageDisposition(StrEnum):
    MEASURED = "measured"
    PARTIAL = "partial"
    OUT_OF_SCOPE = "out_of_scope"


class CoverageEvidence(ContractModel):
    task_ids: list[TaskId] = Field(min_length=1)
    gates: list[GateName] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_bindings(self) -> Self:
        if len(self.task_ids) != len(set(self.task_ids)):
            raise ValueError("coverage evidence task IDs must be unique")
        if len(self.gates) != len(set(self.gates)):
            raise ValueError("coverage evidence gates must be unique")
        return self


class CoverageRule(ContractModel):
    rule_id: Identifier
    description: str = Field(min_length=1)
    disposition: CoverageDisposition
    evidence: list[CoverageEvidence] = Field(default_factory=list)
    limitation: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def matching_disposition(self) -> Self:
        task_ids = [task_id for item in self.evidence for task_id in item.task_ids]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("coverage rule task IDs must be unique")
        if self.disposition == CoverageDisposition.MEASURED:
            if not self.evidence or self.limitation is not None:
                raise ValueError("measured coverage requires evidence and no limitation")
        elif self.disposition == CoverageDisposition.PARTIAL:
            if not self.evidence or self.limitation is None:
                raise ValueError("partial coverage requires evidence and a limitation")
        elif self.evidence or self.limitation is None:
            raise ValueError("out-of-scope coverage requires only a limitation")
        return self


class BenchmarkCoverageManifest(ContractModel):
    schema_version: Literal["slopbench.coverage.v1"]
    coverage_id: Identifier
    version: Version
    source_label: str = Field(min_length=1)
    source_revision: Sha256Hex
    task_set: VersionBinding
    rules: list[CoverageRule] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_rules(self) -> Self:
        rule_ids = [rule.rule_id for rule in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("coverage rule IDs must be unique")
        return self


def validate_coverage(
    path: Path,
    task_set: TaskSetManifest,
) -> BenchmarkCoverageManifest:
    coverage = load_model(path, BenchmarkCoverageManifest)
    if coverage.task_set != task_set_binding(task_set):
        raise ContractError("coverage manifest task-set binding mismatch")
    tasks = {task.task_id: task for task in task_set.tasks}
    for rule in coverage.rules:
        for evidence in rule.evidence:
            for task_id in evidence.task_ids:
                task = tasks.get(task_id)
                if task is None:
                    raise ContractError(
                        f"coverage rule {rule.rule_id} references unknown task: {task_id}"
                    )
                unsupported = set(evidence.gates) - set(task.applicable_gates)
                if unsupported:
                    rendered = sorted(gate.value for gate in unsupported)
                    raise ContractError(
                        f"coverage rule {rule.rule_id} maps non-applicable gates for "
                        f"{task_id}: {rendered}"
                    )
    return coverage
