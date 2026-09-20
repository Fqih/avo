"""DAG trace graph constructor and serializer for Avo Web Cockpit."""

from __future__ import annotations

from typing import Any


def build_run_dag(trace_data: dict[str, Any]) -> dict[str, Any]:
    """Construct a directed acyclic graph (DAG) representation of a run trace."""
    entries = trace_data.get("entries", [])
    run_id = trace_data.get("run_id", "unknown")

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    last_node_id: str | None = None

    for entry in entries:
        seq = entry.get("sequence", 0)
        ev_type = entry.get("event_type", "unknown")
        node_id = f"node_{seq}"

        summary = entry.get("summary", ev_type)
        dur = entry.get("duration_ms")
        err = entry.get("error")

        node_kind = "event"
        if "tool" in ev_type:
            node_kind = "tool"
        elif "model" in ev_type:
            node_kind = "model"
        elif "state" in ev_type:
            node_kind = "state"
        elif err or "error" in ev_type:
            node_kind = "error"

        nodes.append(
            {
                "id": node_id,
                "sequence": seq,
                "event_type": ev_type,
                "kind": node_kind,
                "label": summary,
                "duration_ms": dur,
                "error": err,
                "timestamp": entry.get("created_at"),
            }
        )

        if last_node_id is not None:
            edges.append(
                {
                    "source": last_node_id,
                    "target": node_id,
                    "label": f"step {seq}",
                }
            )

        last_node_id = node_id

    # Generate Mermaid flow diagram
    mermaid_lines = ["graph TD"]
    for node in nodes:
        clean_label = node["label"].replace('"', "'").replace("\n", " ")
        if len(clean_label) > 40:
            clean_label = clean_label[:37] + "..."
        if node["kind"] == "tool":
            mermaid_lines.append(f'    {node["id"]}["🔧 {clean_label}"]')
        elif node["kind"] == "model":
            mermaid_lines.append(f'    {node["id"]}["🧠 {clean_label}"]')
        elif node["kind"] == "error":
            mermaid_lines.append(f'    {node["id"]}["❌ {clean_label}"]')
        else:
            mermaid_lines.append(f'    {node["id"]}["📍 {clean_label}"]')

    for edge in edges:
        mermaid_lines.append(f"    {edge['source']} --> {edge['target']}")

    return {
        "run_id": run_id,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
        "mermaid": "\n".join(mermaid_lines),
    }
