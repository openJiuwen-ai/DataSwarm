# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""CSV schema 本地推断 —— 确定性、零网络依赖、零 LLM。

设计阶段不再依赖远端 infer 接口：探查快照一次完成，且类型规则与
渲染器自洽（推断类型直接进 DDL）。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_INT_RE = re.compile(r"^-?\d{1,18}$")
_FLOAT_RE = re.compile(r"^-?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?$|^-?\d+[eE][+-]?\d+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?$")
_BOOL_RE = re.compile(r"^(?:true|false)$", re.IGNORECASE)
_MAX_VARCHAR = 65533
_NUM_START = frozenset("0123456789+-.")
_DELIMITER_CANDIDATES = (",", "\t", ";", "|")


@dataclass
class _ScanStats:
    """全量扫描的逐列统计（一次流式扫描同时产出）。"""

    empty_flags: list[bool] = field(default_factory=list)
    max_int_digits: list[int] = field(default_factory=list)
    max_frac_digits: list[int] = field(default_factory=list)
    has_sci: list[bool] = field(default_factory=list)


def sanitize_stem(stem: str) -> str:
    """文件名 → 合法表名短名（非常规字符转下划线，数字开头加前缀）。"""
    s = re.sub(r"[^0-9A-Za-z_]", "_", stem)
    if not s or s[0].isdigit():
        s = f"t_{s}"
    return s


def _detect_dialect(path: Path) -> str:
    """嗅探 CSV 分隔符：读文件头部若干行，在候选分隔符里选
    「所有行解析列数一致且列数最大」者；无法判定（单列/空文件）时回退逗号。
    引号内的分隔符由 csv 模块正确处理，比 naive split 稳。
    """
    text = ""
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(path, "r", encoding=encoding, newline="") as f:
                text = "".join(f.readline() for _ in range(20))
            break
        except UnicodeError:
            continue
    best_delimiter, best_width = ",", 1
    for delimiter in _DELIMITER_CANDIDATES:
        try:
            rows = [row for row in csv.reader(io.StringIO(text), delimiter=delimiter) if row]
        except csv.Error:
            continue
        widths = {len(row) for row in rows}
        if len(widths) == 1:
            width = widths.pop()
            if width > best_width:
                best_delimiter, best_width = delimiter, width
    return best_delimiter


def _read_rows(
    path: Path, sample_rows: int, delimiter: str = ","
) -> tuple[list[str], list[list[str]], int, _ScanStats, str]:
    """读 header + 采样行 + 总数据行数 + 逐列全量统计 + 实际可用编码（一次流式扫描完成）。

    统计项（均为全文件事实，非采样推断）：
    - empty_flags: 每列是否出现空值。若按采样判 NOT NULL 而全量有空值，
      Stream Load 会把 NULL 进 NOT NULL 列的行静默过滤 → 数据丢失。
    - max_int_digits / max_frac_digits: 十进制列的整数/小数位数，用于推导
      DECIMAL(P,S)。金额列用 DOUBLE 会有二进制浮点累积误差（SUM 对账偏差）。
    - has_sci: 是否出现科学计数法（无法安全映射 DECIMAL → 回退 DOUBLE）。
    """
    last_err: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(path, "r", encoding=encoding, newline="") as f:
                reader = csv.reader(f, delimiter=delimiter)
                header = next(reader, None) or []
                sample: list[list[str]] = []
                stats = _ScanStats(
                    empty_flags=[False] * len(header),
                    max_int_digits=[0] * len(header),
                    max_frac_digits=[0] * len(header),
                    has_sci=[False] * len(header),
                )
                total = 0
                n_header = len(header)
                for row in reader:
                    total += 1
                    if len(sample) < sample_rows:
                        sample.append(row)
                    for idx, cell in enumerate(row):
                        if idx >= n_header:
                            break
                        if not cell.strip():
                            stats.empty_flags[idx] = True
                            continue
                        if cell[0] not in _NUM_START:
                            continue
                        if "e" in cell or "E" in cell:
                            stats.has_sci[idx] = True
                            continue
                        body = cell[1:] if cell[0] in "+-" else cell
                        dot = body.find(".")
                        if dot >= 0:
                            int_part, frac_part = body[:dot], body[dot + 1:]
                        else:
                            int_part, frac_part = body, ""
                        if (int_part == "" or int_part.isdigit()) and (
                            frac_part == "" or frac_part.isdigit()
                        ):
                            int_digits = len(int_part.lstrip("0"))
                            if int_digits > stats.max_int_digits[idx]:
                                stats.max_int_digits[idx] = int_digits
                            if len(frac_part) > stats.max_frac_digits[idx]:
                                stats.max_frac_digits[idx] = len(frac_part)
                    if len(row) < n_header:
                        for idx in range(len(row), n_header):
                            stats.empty_flags[idx] = True
            return header, sample, total, stats, encoding
        except UnicodeError as exc:
            last_err = exc
            continue
    raise RuntimeError(f"无法读取 {path}（编码不支持）：{last_err}")


def _is_empty(value: str) -> bool:
    return value is None or value.strip() == ""


def _is_leading_zero_int(value: str) -> bool:
    """'007' 这类前导零整数语义上是编码 → 字符串（'0.5' 不受影响）。"""
    return _INT_RE.match(value) is not None and len(value) > 1 and value.startswith("0")


def infer_column(name: str, values: list[str]) -> dict:
    """单列类型推断。规则确定性：空值跳过；全空采样 → VARCHAR(64)。"""
    non_empty = [v.strip() for v in values if not _is_empty(v)]
    nullable = len(non_empty) < len(values)
    if not non_empty:
        return {"name": name, "dtype": "VARCHAR(64)", "nullable": True, "note": "all_null_sample"}
    has_leading_zero = any(_is_leading_zero_int(v) for v in non_empty)
    if all(_INT_RE.match(v) for v in non_empty) and not has_leading_zero:
        return {"name": name, "dtype": "BIGINT", "nullable": nullable, "note": ""}
    if not has_leading_zero and all(_INT_RE.match(v) or _FLOAT_RE.match(v) for v in non_empty):
        if any("e" in v or "E" in v for v in non_empty):
            return {"name": name, "dtype": "DOUBLE", "nullable": nullable, "note": ""}
        # 十进制 → DECIMAL（P/S 由全量统计在 infer_csv_schema 参数化）：
        # DOUBLE 是二进制浮点，金额聚合（SUM）会产生累积误差，电商场景必须用定点 DECIMAL
        return {"name": name, "dtype": "DECIMAL", "nullable": nullable, "note": ""}
    if all(_BOOL_RE.match(v) for v in non_empty):
        return {"name": name, "dtype": "BOOLEAN", "nullable": nullable, "note": ""}
    if all(_DATE_RE.match(v) for v in non_empty):
        return {"name": name, "dtype": "DATE", "nullable": nullable, "note": ""}
    if all(_DATETIME_RE.match(v) for v in non_empty):
        return {"name": name, "dtype": "DATETIME", "nullable": nullable, "note": ""}
    max_len = max(len(v) for v in non_empty)
    if max_len <= 64:
        dtype = "VARCHAR(64)"
    elif max_len <= 255:
        dtype = "VARCHAR(255)"
    elif max_len <= _MAX_VARCHAR:
        dtype = "VARCHAR(65533)"
    else:
        dtype = "STRING"
    return {"name": name, "dtype": dtype, "nullable": nullable, "note": ""}


def infer_csv_schema(path: Path, sample_rows: int = 200) -> dict:
    """单文件推断：列（含去重）+ 总行数 + 分隔符/编码。"""
    delimiter = _detect_dialect(path)
    header, sample, total, stats, encoding = _read_rows(path, sample_rows, delimiter)
    columns: list[dict] = []
    seen: set[str] = set()
    for idx, raw_name in enumerate(header):
        name = (raw_name or f"col_{idx + 1}").strip()
        base = name or f"col_{idx + 1}"
        final = base
        suffix = 2
        while final in seen:
            final = f"{base}_{suffix}"
            suffix += 1
        seen.add(final)
        values = [(row[idx] if idx < len(row) else "") for row in sample]
        col = infer_column(final, values)
        # 全量空值扫描覆盖采样判定：NOT NULL 只基于全文件事实，不基于采样窗口
        if idx < len(stats.empty_flags) and stats.empty_flags[idx]:
            if not col["nullable"]:
                col["note"] = (col.get("note", "") + " empty_beyond_sample").strip()
            col["nullable"] = True
        # DECIMAL 参数化：P/S 由全量统计推导（采样窗口外的精度同样计入），
        # 保证本文件所有值可精确落入；科学计数法/超 Doris 上限回退 DOUBLE
        if col["dtype"] == "DECIMAL":
            if stats.has_sci[idx]:
                col["dtype"] = "DOUBLE"
                col["note"] = (col.get("note", "") + " sci_notation_fallback").strip()
            else:
                scale = min(max(stats.max_frac_digits[idx], 2), 9)
                precision = stats.max_int_digits[idx] + scale
                if precision > 38:
                    col["dtype"] = "DOUBLE"
                    col["note"] = (col.get("note", "") + " precision_overflow_fallback").strip()
                else:
                    col["dtype"] = f"DECIMAL({precision},{scale})"
        if final != base:
            col["note"] = (col.get("note", "") + " duplicate_renamed").strip()
        columns.append(col)
    return {
        "file": path.name,
        "stem": path.stem,
        "row_count": total,
        "columns": columns,
        "delimiter": delimiter,
        "encoding": encoding,
    }


_EXCEL_SUFFIXES = (".xlsx", ".xlsm")


def _xlsx_type(duck_type: str, max_len: int | None) -> str:
    """duckdb 嗅探类型 → Doris 语义规格风格类型（与 CSV 推断口径对齐）。"""
    upper = duck_type.upper()
    if upper.startswith("DECIMAL"):
        return upper
    if upper in ("BIGINT", "HUGEINT"):
        return "BIGINT"
    if upper in ("DOUBLE", "FLOAT"):
        return "DOUBLE"
    if upper == "DATE":
        return "DATE"
    if upper.startswith("TIMESTAMP"):
        return "DATETIME"
    if upper == "BOOLEAN":
        return "BOOLEAN"
    if upper == "INTEGER":
        return "BIGINT"
    if upper.startswith("VARCHAR"):
        if max_len is None:
            return "STRING"
        if max_len <= 64:
            return "VARCHAR(64)"
        if max_len <= 255:
            return "VARCHAR(255)"
        if max_len <= _MAX_VARCHAR:
            return "VARCHAR(65533)"
        return "STRING"
    return "STRING"


def _infer_xlsx(path: Path) -> dict:
    """xlsx/xlsm 推断：duckdb read_xlsx 嗅探列类型 + COUNT/长度聚合（一次连接）。

    与 infer_csv_schema 返回结构同形；xlsx 无分隔符/编码，delimiter/encoding
    为 None（build 层 file_options 对 None 取无害默认，导入 code 对 excel
    分支不使用这两个参数）。
    """
    import duckdb

    conn = duckdb.connect(":memory:")
    try:
        escaped = path.resolve().as_posix().replace("'", "''")
        describes = conn.execute(
            f"DESCRIBE SELECT * FROM read_xlsx('{escaped}')"
        ).fetchall()
        total = int(conn.execute(
            f"SELECT COUNT(*) FROM read_xlsx('{escaped}')"
        ).fetchone()[0])
        columns: list[dict] = []
        if describes and total > 0:
            # LENGTH 只对 VARCHAR 合法（数值列 Binder 报错），仅 varchar 列生成
            agg_parts: list[str] = []
            for row in describes:
                col = row[0].replace('"', '""')
                agg_parts.append(f'COUNT("{col}")')
                if row[1].upper().startswith("VARCHAR"):
                    agg_parts.append(f'MAX(LENGTH("{col}"))')
            aggs = conn.execute(
                f"SELECT {', '.join(agg_parts)} FROM read_xlsx('{escaped}')"
            ).fetchone() if agg_parts else None
        else:
            aggs = None
        # count 与 varchar 列的 max_len 按生成顺序交错存放，重排为逐列平行数组
        if aggs is not None:
            counts: list[int] = []
            lens: list[int | None] = []
            agg_idx = 0
            for row in describes:
                counts.append(int(aggs[agg_idx] or 0))
                agg_idx += 1
                if row[1].upper().startswith("VARCHAR"):
                    lens.append(int(aggs[agg_idx] or 0))
                    agg_idx += 1
                else:
                    lens.append(None)
        else:
            counts = [0] * len(describes)
            lens = [None] * len(describes)
        for idx, row in enumerate(describes):
            name, duck_type = row[0], row[1]
            is_varchar = duck_type.upper().startswith("VARCHAR")
            columns.append({
                "name": name,
                "dtype": _xlsx_type(duck_type, lens[idx] if is_varchar else None),
                "nullable": counts[idx] < total,
                "note": "",
            })
        return {
            "file": path.name,
            "stem": path.stem,
            "row_count": total,
            "columns": columns,
            "delimiter": None,
            "encoding": None,
        }
    finally:
        conn.close()


def infer_dir(csv_dir: str | Path, files: list[str] | None = None, sample_rows: int = 200) -> list[dict]:
    """目录推断：默认取全部 *.csv/*.xlsx/*.xlsm（不区分大小写）；files 指定子集。"""
    directory = Path(csv_dir)
    if not directory.is_dir():
        raise RuntimeError(f"CSV 目录不存在: {directory}")
    if files:
        paths = []
        for name in files:
            p = directory / name
            if not p.is_file():
                raise RuntimeError(f"指定的文件不存在: {p}")
            paths.append(p)
    else:
        paths = sorted(
            p for p in directory.iterdir()
            if p.is_file() and (p.suffix.lower() == ".csv" or p.suffix.lower() in _EXCEL_SUFFIXES)
        )
    return [
        _infer_xlsx(p) if p.suffix.lower() in _EXCEL_SUFFIXES else infer_csv_schema(p, sample_rows)
        for p in paths
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="CSV schema 本地推断")
    parser.add_argument("--csv-dir", required=True)
    parser.add_argument("--files", default="", help="逗号分隔的文件名（可选）")
    parser.add_argument("--sample-rows", type=int, default=200)
    args = parser.parse_args()
    files = [f.strip() for f in args.files.split(",") if f.strip()] or None
    try:
        result = infer_dir(args.csv_dir, files, args.sample_rows)
        print(json.dumps({"ok": True, "files": result}, ensure_ascii=False, indent=2))
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
