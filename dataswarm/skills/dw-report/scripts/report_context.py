"""Report purpose and evidence intent shared by validation and rendering."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

_COMMON = Path(__file__).resolve().parents[2] / "_dw_common" / "scripts"
if str(_COMMON) not in sys.path:
    sys.path.insert(0, str(_COMMON))
for _parent in Path(__file__).resolve().parents:
    if (_parent / "jiuwenswarm" / "__init__.py").is_file():
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        break

import dw_paths  # noqa: E402

REPORT_TYPES = ("analysis", "standalone")


def has_evidence_references(value: Any) -> bool:
    """Detect evidence references throughout a report, including nested blocks."""
    if isinstance(value, dict):
        return any(
            (key in {"evidence_refs", "data_evidence_ref"} and bool(item)) or has_evidence_references(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(has_evidence_references(item) for item in value)
    return False


def context_errors(spec: dict[str, Any]) -> list[str]:
    """Reject ambiguous report types and standalone reports claiming analysis evidence."""
    kind = spec.get("report_type")
    if kind is not None and kind not in REPORT_TYPES:
        return [f"report_type: must be one of {list(REPORT_TYPES)}"]
    if kind == "standalone" and (
        spec.get("analysis_id")
        or spec.get("manifest_path")
        or spec.get("mode") in ("open", "closed")
        or has_evidence_references(spec)
        or "report_gate" in spec
    ):
        return ["standalone report cannot include analysis identity, mode, report_gate or evidence references"]
    return []


def requires_evidence(spec: dict) -> bool:
    return bool(
        spec.get("analysis_id")
        or spec.get("manifest_path")
        or spec.get("mode") in ("open", "closed")
        or spec.get("report_type") == "analysis"
        or has_evidence_references(spec)
    )


def bind_analysis_context(spec: dict, *, allow_unbound: bool = False) -> dict:
    """Read-only identity resolution shared by validation and rendering.

    Explicit paths may select an earlier analysis; both ownership checks still apply.
    Invalid pointers/manifests fail rather than silently becoming standalone reports.
    """
    errors = context_errors(spec)
    if errors:
        raise ValueError("; ".join(errors))
    bound = copy.deepcopy(spec)
    if bound.get("report_type") == "standalone":
        return bound
    sid = os.environ.get("JIUWENSWARM_SESSION_ID", "").strip()
    if sid:
        root = Path(os.environ.get("JIUWENSWARM_ARTIFACT_DIR") or dw_paths.artifact_root() / "analysis")
        pointer = root.expanduser() / "by-session" / (hashlib.sha256(sid.encode()).hexdigest() + ".json")
        if pointer.exists():
            binding = _read_object(pointer)
            raw = binding.get("manifest_path")
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError(f"session analysis pointer is missing manifest_path: {pointer}")
            bound["manifest_path"] = bound.get("manifest_path") or raw
    raw = bound.get("manifest_path")
    if not raw:
        if requires_evidence(bound) and not allow_unbound:
            raise ValueError("analysis report requires manifest_path")
        return bound
    path = Path(str(raw)).expanduser()
    identity = _read_object(path)
    aid = identity.get("analysis_id")
    if not isinstance(aid, str) or not aid.strip():
        raise ValueError(f"analysis manifest is missing analysis_id: {path}")
    if bound.get("analysis_id") and bound["analysis_id"] != aid:
        raise ValueError("report analysis_id does not match manifest")
    wid = os.environ.get("JIUWENSWARM_WORKSPACE_ID", "").strip()
    if wid and identity.get("workspace_id") != wid:
        raise ValueError("analysis manifest belongs to another workspace or has no verified owner")
    if sid and identity.get("session_id") != sid:
        raise ValueError("analysis manifest belongs to another session")
    bound.update(analysis_id=aid, manifest_path=str(path))
    return bound


def _read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: 必须是 JSON 对象")
    return value
