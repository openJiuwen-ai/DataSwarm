"""Read-only presentation of independent query and computation evidence lineage."""
from __future__ import annotations


def section_lineage(refs: list[str], evidence: dict) -> tuple[str, list[dict]]:
    """Return source SQL display text and computation methods; never execute SQL."""
    visited, queries, methods = set(), [], []

    def visit(ref):
        if ref in visited or ref not in evidence:
            return
        visited.add(ref)
        result = evidence[ref]
        for source in result.get("input_evidence_refs", []):
            visit(source)
        if result.get("sql"):
            queries.append("-- evidence: " + ref + "\n" + result["sql"])
        method = result.get("computation") or {}
        if result.get("kind") == "computation":
            methods.append({"evidence_id": ref, "method": method.get("algorithm") or "Python",
                            "description": result.get("note", ""), "params": method.get("params", {}),
                            "code": method.get("code", "")})

    for ref in refs:
        visit(ref)
    return "\n\n".join(queries), methods


def methods_text(methods: list[dict]) -> str:
    """Explain the calculation in business language; source code stays in evidence."""
    return "\n\n".join(dict.fromkeys(m["description"].strip() for m in methods if m.get("description", "").strip()))
