from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskNode:
    name: str
    command: tuple[str, ...]
    needs: tuple[str, ...] = ()


TASK_GRAPH = (
    TaskNode(
        "tests",
        ("python", "-m", "unittest", "discover", "-s", "tests", "-v"),
    ),
    TaskNode(
        "types",
        ("python", "-m", "compileall", "-q", "src", "tests", "tools"),
    ),
)


def resolve_tasks(
    target: str, graph: tuple[TaskNode, ...] = TASK_GRAPH
) -> tuple[TaskNode, ...]:
    nodes = {node.name: node for node in graph}
    if len(nodes) != len(graph):
        raise ValueError("task names must be unique")
    ordered: list[TaskNode] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise ValueError("task graph contains a cycle")
        if name in visited:
            return
        try:
            node = nodes[name]
        except KeyError as exc:
            raise ValueError(f"unknown task: {name}") from exc
        visiting.add(name)
        for dependency in node.needs:
            visit(dependency)
        visiting.remove(name)
        visited.add(name)
        ordered.append(node)

    visit(target)
    return tuple(ordered)
