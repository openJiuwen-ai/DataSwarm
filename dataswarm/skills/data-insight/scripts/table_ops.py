# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Composable operations on arbitrary columns; no database or generated code."""
from __future__ import annotations

import json
import math
import time

import numpy as np
import pandas as pd


def _columns(frame: pd.DataFrame, names: list[str]) -> None:
    missing = [name for name in names if name not in frame.columns]
    if missing:
        raise ValueError(f"missing columns {missing}; available={list(frame.columns)}")


def _names(value: object) -> list[str]:
    if (not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value)
            or len(set(value)) != len(value)):
        raise ValueError("expected a list of unique nonempty column names")
    return value


def _number(values: pd.Series) -> pd.Series:
    parsed = pd.to_numeric(values, errors="coerce")
    if (parsed.isna() & values.notna()).any():
        raise ValueError(f"column {values.name!r} contains nonnumeric values; cast explicitly or fix input")
    if np.isinf(parsed.to_numpy(dtype=float, na_value=np.nan)).any():
        raise ValueError(f"column {values.name!r} contains nonfinite numbers")
    return parsed


def _numeric(values: pd.Series) -> pd.Series:
    if values.notna().any() and not pd.api.types.is_numeric_dtype(values):
        raise ValueError(f"column {values.name!r} requires cast(type=number) before numeric operations")
    return _number(values)


def _target(frame: pd.DataFrame, name: str) -> None:
    if not isinstance(name, str) or not name or name in frame.columns:
        raise ValueError(f"output alias must be nonempty and not overwrite an existing column: {name!r}")


def _sort(frame: pd.DataFrame, keys: list[dict]) -> pd.DataFrame:
    if not isinstance(keys, list) or not keys or any(not isinstance(key, dict) or "column" not in key for key in keys):
        raise ValueError('order_by requires objects, e.g. [{"column":"actual_column","direction":"asc"}]')
    _names([key["column"] for key in keys])
    _columns(frame, [key["column"] for key in keys])
    result = frame
    for key in reversed(keys):
        direction, nulls = key.get("direction", "asc"), key.get("nulls", "last")
        if direction not in {"asc", "desc"} or nulls not in {"first", "last"}:
            raise ValueError("direction must be asc/desc and nulls first/last")
        result = result.sort_values(key["column"], ascending=direction == "asc", na_position=nulls, kind="stable")
    return result.reset_index(drop=True)


def _reduce(series: pd.Series, measure: dict) -> object:
    method = measure["function"]
    policy = measure.get("nulls", "skip")
    if policy not in {"skip", "zero", "error"}:
        raise ValueError("nulls must be skip, zero or error")
    if policy == "error" and series.isna().any():
        raise ValueError(f"null cells in {series.name!r}")
    if method == "size":
        return len(series)
    if method == "count":
        return int(series.notna().sum())
    if method == "nunique":
        include_null = measure.get("include_null", False)
        if not isinstance(include_null, bool):
            raise ValueError("include_null must be boolean")
        return int(series.nunique(dropna=not include_null))
    if method not in {"sum", "mean", "median", "min", "max"}:
        raise ValueError(f"unknown aggregate function {method!r}")
    if method in {"sum", "mean", "median"}:
        series = _numeric(series)
    if policy == "zero":
        series = series.fillna(0)
    if not series.notna().any():
        return None
    return getattr(series, method)()


def _aggregate(frame: pd.DataFrame, step: dict) -> pd.DataFrame:
    by = _names(step.get("group_by", []))
    _columns(frame, by)
    measures = step["measures"]
    if not isinstance(measures, list) or not measures:
        raise ValueError("measures must be nonempty")
    aliases = _names([m["as"] for m in measures])
    if set(aliases) & set(by):
        raise ValueError("aggregate output aliases conflict with group_by")
    for measure in measures:
        if measure["function"] != "size":
            _columns(frame, [measure["column"]])
    groups = frame.groupby(by, dropna=False, sort=False) if by else [((), frame)]
    rows = []
    for key, members in groups:
        key = key if isinstance(key, tuple) else (key,)
        row = list(key)
        for measure in measures:
            series = (pd.Series(index=members.index, dtype=object) if measure["function"] == "size"
                      else members[measure["column"]])
            row.append(_reduce(series, measure))
        rows.append(row)
    return pd.DataFrame(rows, columns=by + aliases)


def _expression(frame: pd.DataFrame, node: dict, warnings: list[str], depth: int = 0) -> pd.Series:
    if depth > 16 or not isinstance(node, dict):
        raise ValueError("expression must be a bounded arithmetic tree")
    if set(node) == {"column"}:
        _columns(frame, [node["column"]])
        return _numeric(frame[node["column"]])
    if set(node) == {"constant"}:
        value = node["constant"]
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError("constant must be a finite number")
        return pd.Series(value, index=frame.index)
    op = node.get("op")
    if op == "sum":
        column = node["column"]
        _columns(frame, [column])
        values = _numeric(frame[column])
        by = _names(node.get("group_by", []))
        _columns(frame, by)
        if by:
            return values.groupby([frame[c] for c in by], dropna=False).transform(lambda v: v.sum(min_count=1))
        return pd.Series(values.sum(min_count=1), index=frame.index)
    if op not in {"add", "subtract", "multiply", "divide"}:
        raise ValueError(f"unknown expression operator {op!r}")
    left = _expression(frame, node["left"], warnings, depth + 1)
    right = _expression(frame, node["right"], warnings, depth + 1)
    if op == "divide":
        if (right == 0).any():
            warnings.append("zero denominator: result is null")
        return left / right.replace(0, np.nan)
    return {"add": left.add, "subtract": left.sub, "multiply": left.mul}[op](right)


def _join(frame: pd.DataFrame, step: dict, inputs: dict[str, pd.DataFrame], max_rows: int) -> pd.DataFrame:
    reference = step["input"]
    if reference not in inputs:
        raise ValueError(f"join input {reference!r} is unavailable; available_inputs={list(inputs)}")
    right = inputs[reference].copy().replace("", None)
    if "on" in step:
        if "left_on" in step or "right_on" in step:
            raise ValueError("join uses on or left_on/right_on, not both")
        left_keys = right_keys = _names(step["on"])
    else:
        left_keys, right_keys = _names(step["left_on"]), _names(step["right_on"])
    if not left_keys or len(left_keys) != len(right_keys):
        raise ValueError("join requires equally sized nonempty key lists")
    _columns(frame, left_keys)
    _columns(right, right_keys)
    if "columns" in step:
        columns = _names(step["columns"])
        _columns(right, columns)
        right = right[list(dict.fromkeys(right_keys + columns))]
    how, validate = step.get("how", "left"), step.get("validate", "many_to_one")
    if how not in {"left", "inner"} or validate not in {
            "one_to_one", "one_to_many", "many_to_one", "many_to_many"}:
        raise ValueError("join how is left/inner; validate describes one_to_one/one_to_many/many_to_one/many_to_many")
    # SQL-style keys: null never matches null. Check fanout before materializing rows.
    right = right.dropna(subset=right_keys)
    # Internal column names must not collide with user-provided key names.
    axes = list(range(len(left_keys)))
    left_counts = frame.dropna(subset=left_keys).groupby(left_keys, sort=False).size().rename_axis(axes)
    right_counts = right.groupby(right_keys, sort=False).size().rename_axis(axes)
    left_counts = left_counts.reset_index(name="_left_count")
    right_counts = right_counts.reset_index(name="_right_count")
    counts = left_counts.merge(right_counts, on=list(range(len(left_keys))), how="left")
    matches = counts["_right_count"].fillna(1 if how == "left" else 0)
    expected = int((counts["_left_count"] * matches).sum())
    if how == "left":
        expected += int(frame[left_keys].isna().any(axis=1).sum())
    if expected > max_rows:
        raise ValueError(f"join would produce {expected} rows, exceeding max_rows={max_rows}; aggregate inputs first")
    suffixes = step.get("suffixes", ["", "_right"])
    if not isinstance(suffixes, list) or len(suffixes) != 2 or any(not isinstance(s, str) for s in suffixes):
        raise ValueError("join suffixes must contain two strings")
    return frame.merge(right, left_on=left_keys, right_on=right_keys, how=how,
                       validate=validate, suffixes=tuple(suffixes), sort=False)


def _step(frame: pd.DataFrame, step: dict, warnings: list[str],
          inputs: dict[str, pd.DataFrame], max_rows: int) -> pd.DataFrame:
    op = step["op"]
    fields = {
        "cast": {"column", "type", "as", "format"}, "filter": {"column", "operator", "value"},
        "select": {"columns", "rename"}, "aggregate": {"group_by", "measures"}, "sort": {"order_by"},
        "limit": {"n"}, "derive": {"as", "expression"}, "cumulative": {"group_by", "order_by", "measures"},
        "join": {"input", "on", "left_on", "right_on", "columns", "how", "validate", "suffixes"},
        "melt": {"id_vars", "value_vars", "var_name", "value_name"},
        "pivot": {"index", "column", "value"},
        "shift": {"group_by", "order_by", "periods", "measures"},
    }
    if op not in fields or set(step) - fields[op] - {"op", "emit"}:
        raise ValueError(f"unknown operation or parameters for {op!r}")
    if op == "cast":
        column = step["column"]
        _columns(frame, [column])
        target = step.get("as", column)
        if target != column:
            _target(frame, target)
        values = frame[column]
        if step["type"] == "number":
            values = _number(values)
        elif step["type"] == "text":
            values = values.astype("string")
        elif step["type"] == "date":
            values = pd.to_datetime(values, errors="raise", format=step.get("format"), utc=True)
        else:
            raise ValueError("cast type must be number/text/date")
        return frame.assign(**{target: values})
    if op == "filter":
        column, method = step["column"], step["operator"]
        _columns(frame, [column])
        series, value = frame[column], step.get("value")
        if method in {"is_null", "not_null"}:
            mask = series.isna() if method == "is_null" else series.notna()
        elif method in {"in", "not_in"}:
            if not isinstance(value, list):
                raise ValueError("in/not_in requires a value list")
            mask = series.isin(value)
            if method == "not_in":
                mask = ~mask
            mask &= series.notna()
        elif method in {"eq", "ne", "gt", "ge", "lt", "le"}:
            mask = getattr(series, method)(value) & series.notna()
        else:
            raise ValueError("unknown filter operator")
        return frame[mask.fillna(False)].reset_index(drop=True)
    if op == "select":
        columns = _names(step["columns"])
        if not columns:
            raise ValueError("select must retain at least one column")
        _columns(frame, columns)
        rename = step.get("rename", {})
        if not isinstance(rename, dict) or set(rename) - set(columns):
            raise ValueError("rename must map selected columns to output names")
        _names([rename.get(c, c) for c in columns])
        return frame[columns].rename(columns=rename)
    if op == "aggregate":
        return _aggregate(frame, step)
    if op == "sort":
        return _sort(frame, step["order_by"])
    if op == "limit":
        n = step["n"]
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise ValueError("limit n must be a nonnegative integer")
        return frame.head(n).copy()
    if op == "derive":
        _target(frame, step["as"])
        return frame.assign(**{step["as"]: _expression(frame, step["expression"], warnings)})
    if op == "join":
        return _join(frame, step, inputs, max_rows)
    if op == "melt":
        ids, values = _names(step.get("id_vars", [])), _names(step["value_vars"])
        _columns(frame, ids + values)
        if not values or set(ids) & set(values):
            raise ValueError("melt requires nonempty value_vars disjoint from id_vars")
        variable, value = step.get("var_name", "variable"), step.get("value_name", "value")
        _names(ids + [variable, value])
        if len(frame) * len(values) > max_rows:
            raise ValueError("melt output exceeds max_rows")
        return frame.melt(id_vars=ids, value_vars=values, var_name=variable, value_name=value)
    if op == "pivot":
        ids, column, value = _names(step["index"]), step["column"], step["value"]
        _names(ids + [column, value])
        _columns(frame, ids + [column, value])
        if not ids or frame.duplicated(ids + [column]).any():
            raise ValueError("pivot requires nonempty index and unique index+column keys; aggregate duplicates first")
        if frame[column].isna().any():
            raise ValueError("pivot column labels cannot be null")
        if frame[column].nunique() * (frame[ids].drop_duplicates().shape[0]) > max_rows * max(1, len(frame.columns)):
            raise ValueError("pivot output exceeds bounded table size; filter categories first")
        result = frame.pivot(index=ids, columns=column, values=value)
        result.columns = [str(label) for label in result.columns]
        _names(ids + list(result.columns))
        return result.reset_index()
    if op == "shift":
        by = _names(step.get("group_by", []))
        _columns(frame, by)
        order = step["order_by"]
        result = _sort(frame, order)
        keys = list(dict.fromkeys(by + [key["column"] for key in order]))
        if result.duplicated(keys).any():
            raise ValueError("shift requires unique group_by+order_by keys; add tie-breakers or aggregate first")
        periods = step.get("periods", 1)
        if isinstance(periods, bool) or not isinstance(periods, int) or periods < 1:
            raise ValueError("shift periods must be a positive integer (previous observations, not calendar periods)")
        measures = step["measures"]
        if not isinstance(measures, list) or not measures:
            raise ValueError("shift measures must be nonempty")
        for measure in measures:
            _target(result, measure["as"])
            _columns(result, [measure["column"]])
            values = result[measure["column"]]
            result[measure["as"]] = (values.groupby([result[c] for c in by], dropna=False).shift(periods)
                                      if by else values.shift(periods))
        return result
    if op == "cumulative":
        by = _names(step.get("group_by", []))
        _columns(frame, by)
        result = _sort(frame, step["order_by"])
        measures = step["measures"]
        if not isinstance(measures, list) or not measures:
            raise ValueError("cumulative measures must be nonempty")
        _names([m["as"] for m in measures])
        for measure in measures:
            _target(result, measure["as"])
            _columns(result, [measure["column"]])
            values = _numeric(result[measure["column"]])
            policy = measure.get("nulls", "skip")
            if policy not in {"skip", "zero", "error"}:
                raise ValueError("nulls must be skip/zero/error")
            if policy == "error" and values.isna().any():
                raise ValueError("null value in cumulative input")
            if policy == "zero":
                values = values.fillna(0)
            result[measure["as"]] = (values.groupby([result[c] for c in by], dropna=False).cumsum()
                                      if by else values.cumsum())
        return result
    raise ValueError(f"unknown table operation {op!r}; use custom code for other computations")


def _table(frame: pd.DataFrame) -> dict:
    for column in frame.columns:
        if pd.api.types.is_numeric_dtype(frame[column]) and np.isinf(
                frame[column].to_numpy(dtype=float, na_value=np.nan)).any():
            raise ValueError(f"nonfinite output in {column!r}")
    return {"columns": list(frame.columns),
            "rows": json.loads(frame.to_json(orient="values", date_format="iso", double_precision=15))}


def run(frame: pd.DataFrame, params: dict, *, inputs: dict[str, pd.DataFrame] | None = None) -> dict:
    """Execute bounded table steps atomically, preserving every explicitly emitted table.

    Args:
        frame: Full selected input, with column names and identifiers preserved.
        params: steps plus optional max_seconds/max_rows execution bounds.
        inputs: Named, already verified tables for join steps; never file paths.
    Returns:
        Status, named tables, warnings, step audit and self_check; no partial outputs on error.
    """
    steps = params.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 64:
        return {"status": "invalid_request", "error": "steps must contain 1..64 operations"}
    if not frame.columns.is_unique or any(not isinstance(c, str) or not c for c in frame.columns):
        return {"status": "invalid_request", "error": "input columns must be unique nonempty strings"}
    outputs: dict[str, dict] = {}
    warnings: list[str] = []
    audit: list[dict] = []
    current = frame.copy().replace("", None)
    index = 0
    try:
        seconds = float(params.get("max_seconds", 60))
        rows_limit = params.get("max_rows", 100000)
        if (not math.isfinite(seconds) or seconds <= 0 or isinstance(rows_limit, bool)
                or not isinstance(rows_limit, int) or rows_limit < 1):
            raise ValueError("max_seconds must be finite/positive and max_rows a positive integer")
        deadline = time.monotonic() + min(60.0, seconds)
        max_rows = min(100000, rows_limit)
        if len(current) > max_rows:
            raise ValueError("input exceeds max_rows; aggregate at the data source")
        for reference, source in (inputs or {}).items():
            if len(source) > max_rows or not source.columns.is_unique:
                raise ValueError(f"join input {reference!r} exceeds max_rows or has duplicate columns")
        for index, step in enumerate(steps):
            if time.monotonic() >= deadline:
                raise ValueError("table computation time limit exceeded")
            if not isinstance(step, dict):
                raise ValueError("each step must be an object")
            count = len(current)
            current = _step(current, step, warnings, inputs or {}, max_rows)
            if not current.columns.is_unique:
                raise ValueError("output columns must be unique")
            if len(current) > max_rows or time.monotonic() >= deadline:
                raise ValueError("table computation execution bounds exceeded")
            audit.append({"index": index, "op": step["op"], "input_rows": count, "output_rows": len(current)})
            name = step.get("emit")
            if name is not None:
                if not isinstance(name, str) or not name or name in outputs:
                    raise ValueError("emit names must be unique nonempty strings")
                outputs[name] = _table(current)
        if not steps[-1].get("emit"):
            if "result" in outputs:
                raise ValueError(
                    "emit name result is reserved for the implicit final output; name the final emit explicitly"
                )
            outputs["result"] = _table(current)
        return {"status": "ok", "tables": outputs, "warnings": list(dict.fromkeys(warnings)), "steps": audit,
                "self_check": {"passed": True, "confidence": "medium" if warnings else "high",
                               "errors": [], "warnings": list(dict.fromkeys(warnings))}}
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError) as exc:
        return {"status": "invalid_request", "step_index": index, "error": str(exc),
                "available_columns": list(current.columns)}
