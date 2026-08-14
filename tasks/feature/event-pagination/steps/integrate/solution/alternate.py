from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskNode:
    name: str
    command: tuple[str, ...]
    needs: tuple[str, ...] = ()


def _task_graph() -> tuple[TaskNode, ...]:
    leaves = {
        "tests": ("python", "-m", "unittest", "discover", "-s", "tests", "-v"),
        "types": ("python", "-m", "compileall", "-q", "src", "tests", "tools"),
    }
    return (
        *(TaskNode(name, command) for name, command in leaves.items()),
        TaskNode("verify", (), tuple(leaves)),
    )


TASK_GRAPH = _task_graph()


def resolve_tasks(target: str, graph: tuple[TaskNode, ...] = TASK_GRAPH) -> tuple[TaskNode, ...]:
    nodes: dict[str, TaskNode] = {}
    for node in graph:
        if node.name in nodes:
            raise ValueError("task names must be unique")
        nodes[node.name] = node

    ordered: list[TaskNode] = []
    active: set[str] = set()
    complete: set[str] = set()

    def append(name: str) -> None:
        if name in active:
            raise ValueError("task graph contains a cycle")
        if name in complete:
            return
        node = nodes.get(name)
        if node is None:
            raise ValueError(f"unknown task: {name}")
        active.add(name)
        for dependency in node.needs:
            append(dependency)
        active.remove(name)
        complete.add(name)
        ordered.append(node)

    append(target)
    return tuple(ordered)
