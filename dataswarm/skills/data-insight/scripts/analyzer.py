#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""data-insight 统计算子集。

调用：

    python analyzer.py <op> --data <csv> --params '<json>'

op 共六个：trend_profile / outlier_scan / dim_breakdown / dim_rank /
formula_breakdown / driver_correlation。formula_breakdown 只读 --params，
其余 op 读取 --data 指定的 CSV。

输入 CSV 应已是目标列与目标粒度；派生比率、重采样、过滤、关联由上游取数完成，
本脚本收到这类需求时返回 invalid_request。

每个返回体都带 self_check：针对返回结构本身的自洽断言。

旧版调用形式（--op / --csv_path / --args、旧 op 名、旧参数名）仍然可用，
入口处经 _LEGACY_* 映射统一转换为当前名称。
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import sys
from collections.abc import Callable
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# 输出固定为 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── 判定参数 ──────────────────────────────────────────────────────
DIVERGENCE_HIGH, DIVERGENCE_MID = 15.0, 5.0     # 成员变化率离散度分级（百分点）
ROLE_MAJOR_PCT, ROLE_MINOR_PCT = 25.0, 8.0      # 角色分级：占变动总量的份额（%）
COEF_HIGH, COEF_MID = 0.6, 0.3                  # 相关强度分级（|系数|）
FLAT_BAND_MIN = 0.03                            # 持平带下限 ±3%
FLAT_NOISE_K = 2.0                              # 持平带 = max(下限, K × 首尾均值差的相对标准误)
NET_GROSS_FLOOR = 0.20                          # 净变动/毛变动 的下限，低于时净占比不可用
ALPHA_DEFAULT = 0.05                            # 显著性水平
Z_CUTOFF_DEFAULT = 3.0                          # 离群判定的默认偏离倍数
WEEKLY_RATIO_MIN = 1.15                         # 星期均值峰谷比超过此值视为有周周期
MAX_TERMS = 8                                   # formula_breakdown 因子上限（精确 Shapley 枚举 2^n 子集）
ALGORITHMS_FORMULA = ("shapley", "one_at_a_time", "delta_sum")
ALGORITHMS_CORR = ("spearman", "pearson", "kendall")
WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# ── 旧版名称兼容 ──────────────────────────────────────────────────
_LEGACY_OPS = {
    "trend": "trend_profile",
    "detect_anomalies": "outlier_scan",
    "attribution": "dim_breakdown",
    "multi_attribution": "dim_rank",
    "factor_decomposition": "formula_breakdown",
    "correlation": "driver_correlation",
}
_LEGACY_WINDOW = {"period_col": "time_field", "base_period": "baseline_window",
                  "curr_period": "current_window", "compare": "split_mode"}
_LEGACY_PARAMS = {                               # 每个 op 只映射旧版该 op 实际读取的参数
    "trend_profile": {"date_col": "time_field", "value_col": "metric_field"},
    "outlier_scan": {"column": "metric_field", "date_col": "time_field",
                     "exclude_col": "skip_flag_field", "threshold": "z_cutoff"},
    "dim_breakdown": {"value_col": "metric_field", "dim_col": "dim_field", **_LEGACY_WINDOW},
    "dim_rank": {"value_col": "metric_field", "dim_cols": "dim_fields", **_LEGACY_WINDOW},
    "formula_breakdown": {"base_values": "baseline", "current_values": "current",
                          "formula": "expression", "method": "algorithm"},
    "driver_correlation": {"target_col": "metric_field", "candidate_cols": "driver_fields",
                           "confound_cols": "control_fields", "method": "algorithm",
                           "significance_level": "alpha", "emit_full_matrix": "with_matrix"},
}
_LEGACY_ALGORITHMS = {"factorization": "one_at_a_time", "additive": "delta_sum"}


def _normalize_params(op: str, params: dict) -> dict:
    """把该 op 的旧参数名改写为当前名称；新旧同时出现时以当前名称为准，其余键原样保留。"""
    aliases = _LEGACY_PARAMS.get(op, {})
    normalized = {k: v for k, v in params.items() if k not in aliases}
    for key, value in params.items():
        new_key = aliases.get(key)
        if new_key and new_key not in normalized:
            normalized[new_key] = value
    algorithm = normalized.get("algorithm")
    if isinstance(algorithm, str):
        algorithm = algorithm.strip().lower()
        normalized["algorithm"] = _LEGACY_ALGORITHMS.get(algorithm, algorithm)
    # 旧版 formula 的 method 取未知值时按 shapley 计算
    if (op == "formula_breakdown" and "method" in params and "algorithm" not in params
            and normalized.get("algorithm") not in ALGORITHMS_FORMULA):
        normalized["_legacy_note"] = f"旧参数 method={params['method']!r} 不是可识别的算法，已按 shapley 计算"
        normalized["algorithm"] = "shapley"
    return normalized


# ══════════════════════════════════════════════════════════════════════
# 自洽校验
# ══════════════════════════════════════════════════════════════════════
class Checks:
    """返回结构的自洽断言集合。"""

    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, name: str, passed: bool, severity: str = "error",
            observed: Any = None, expected: Any = None, detail: str = "") -> None:
        self.items.append({
            "check": name, "passed": bool(passed), "severity": severity,
            "observed": observed, "expected": expected, "detail": detail,
        })

    def close_to(self, name: str, value: float, target: float,
                 rel_tol: float = 0.02, severity: str = "error") -> None:
        ok = _rel_close(value, target, rel_tol)
        self.add(name, ok, severity, _r(value), _r(target), "" if ok else "与期望值不符")

    def in_range(self, name: str, value: float, lo: float, hi: float) -> None:
        ok = value is not None and lo <= value <= hi
        self.add(name, ok, "error", _r(value), f"[{lo}, {hi}]", "" if ok else "超出合法区间")

    def report(self) -> dict:
        errs = [c for c in self.items if not c["passed"] and c["severity"] == "error"]
        warns = [c for c in self.items if not c["passed"] and c["severity"] == "warn"]
        return {
            "passed": not errs,
            "total": len(self.items),
            "confidence": "low" if errs else ("medium" if warns else "high"),
            "errors": [_brief(c) for c in errs],
            "warnings": [_brief(c) for c in warns],
        }


def _brief(c: dict) -> str:
    return f"{c['check']}: 实际 {c['observed']}，应为 {c['expected']}"


def _rel_close(a: float, b: float, rel_tol: float, abs_tol: float = 1e-9) -> bool:
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(a) and math.isfinite(b)):
        return False
    return abs(a - b) <= max(abs_tol, rel_tol * max(abs(a), abs(b)))


def _r(x: Any, nd: int = 4) -> Any:
    """numpy 数值转原生 float 并四舍五入；非有限值返回 None。"""
    if x is None or isinstance(x, str):
        return x
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    if not math.isfinite(v):
        return None
    return round(v, nd)


def _fail(status: str, error: str, **extra: Any) -> dict:
    return {"status": status, "error": error, **extra}


def _missing(df: pd.DataFrame, *fields: Any) -> dict | None:
    for f in fields:
        if f not in df.columns:
            return _fail("invalid_request", f"列不存在：{f}；可用列：{list(df.columns)}")
    return None


def _numeric(df: pd.DataFrame, field: Any) -> tuple[pd.Series | None, dict | None]:
    """把一列转为数值，无法解析的取值置 NaN。

    返回 (数值列, 附加信息或 None)；整列都无法解析时返回 (None, 错误返回体)。
    """
    v = pd.to_numeric(df[field], errors="coerce")
    if v.notna().sum() == 0:
        return None, _fail(
            "invalid_request",
            f"列 {field!r} 没有可解析为数值的取值；请确认取数时选对了列，或在 SQL 里做类型转换")
    dropped = int(v.isna().sum() - df[field].isna().sum())
    return v, ({"coerced": dropped} if dropped > 0 else None)


# ══════════════════════════════════════════════════════════════════════
# trend_profile
# ══════════════════════════════════════════════════════════════════════
def _flat_band(vals: np.ndarray, window: int, opening: float) -> tuple[float, float]:
    """持平带与噪声带（均为相对值）。

    噪声 σ 由一阶差分的 MAD 估计：σ ≈ 1.4826·MAD(Δ)/√2；
    首尾窗口均值差的标准误 = σ·√(2/window)。
    """
    if len(vals) < 3 or not opening:
        return FLAT_BAND_MIN, 0.0
    step = np.diff(vals)
    sigma = 1.4826 * float(np.median(np.abs(step - np.median(step)))) / math.sqrt(2.0)
    noise = FLAT_NOISE_K * sigma * math.sqrt(2.0 / window) / abs(opening)
    return max(FLAT_BAND_MIN, noise), noise


def _is_daily(dates: pd.Series) -> bool:
    """相邻日期间隔的中位数为 1 天时视为日粒度。"""
    dt = pd.to_datetime(dates).sort_values()
    if len(dt) < 3:
        return False
    gaps = dt.diff().dropna().dt.days
    return bool(len(gaps) and float(gaps.median()) == 1.0)


def op_trend_profile(df: pd.DataFrame, p: dict) -> dict:
    time_f, metric_f = p.get("time_field"), p.get("metric_field")
    err = _missing(df, time_f, metric_f)
    if err:
        return err
    v, info = _numeric(df, metric_f)
    if v is None:
        return info or _fail("invalid_request", f"列 {metric_f!r} 无法解析为数值")

    d = df[[time_f]].copy()
    d[metric_f] = v
    d = d.dropna()
    d[time_f] = pd.to_datetime(d[time_f], errors="coerce")
    d = d.dropna(subset=[time_f]).sort_values(time_f)
    d = d.groupby(time_f, as_index=False)[metric_f].sum()      # 同一时间点多行先求和
    if len(d) < 3:
        return _fail("too_few_rows", f"有效数据点仅 {len(d)} 个，至少需要 3 个")

    vals = d[metric_f].to_numpy(dtype=float)
    seq = []
    for i in range(len(d)):
        point = {"t": d[time_f].iloc[i].strftime("%Y-%m-%d"), "value": _r(vals[i]), "step_change_pct": None}
        if i > 0 and vals[i - 1] != 0:
            point["step_change_pct"] = _r((vals[i] - vals[i - 1]) / abs(vals[i - 1]) * 100, 2)
        seq.append(point)

    window = max(1, min(14, len(vals) // 4))                    # 首尾窗口长度
    opening, closing = float(vals[:window].mean()), float(vals[-window:].mean())
    rel_move = (closing - opening) / abs(opening) if opening else 0.0
    band, noise = _flat_band(vals, window, opening)
    movement = "rising" if rel_move > band else ("falling" if rel_move < -band else "flat")

    weekly = None
    if len(d) >= 14 and _is_daily(d[time_f]):
        by_dow = d.groupby(d[time_f].dt.dayofweek)[metric_f].mean()
        if len(by_dow) >= 5 and by_dow.min() > 0:
            ratio = float(by_dow.max() / by_dow.min())
            weekly = {
                "detected": ratio > WEEKLY_RATIO_MIN,
                "peak_trough_ratio": _r(ratio, 3),
                "peak_day": WEEKDAY_NAMES[int(by_dow.idxmax())],
                "trough_day": WEEKDAY_NAMES[int(by_dow.idxmin())],
                "hint": ("星期周期明显，做离群检测时请传 time_field 以便先做周期分解"
                         if ratio > WEEKLY_RATIO_MIN else "星期周期不明显"),
            }

    ck = Checks()
    ck.add("序列长度与输入一致", len(seq) == len(d), "error", len(seq), len(d))
    if len(seq) > 1 and seq[1]["step_change_pct"] is not None:
        ck.close_to("首个环比可重算", seq[1]["step_change_pct"],
                    (vals[1] - vals[0]) / abs(vals[0]) * 100, 0.01)
    ck.add("方向与持平带一致",
           (movement == "rising" and rel_move > band) or (movement == "falling" and rel_move < -band)
           or (movement == "flat" and abs(rel_move) <= band),
           "error", movement, f"rel_move={_r(rel_move, 4)}, band={_r(band, 4)}")

    return {
        "status": "ok", "time_field": time_f, "metric_field": metric_f,
        "point_count": len(d),
        "span": {"from": seq[0]["t"], "to": seq[-1]["t"]},
        "movement": movement,
        "net_change_pct": _r(rel_move * 100, 2),
        "flat_band_pct": _r(band * 100, 2),
        "noise_band_pct": _r(noise * 100, 2),
        "window_size": window,
        "opening_mean": _r(opening), "closing_mean": _r(closing),
        "weekly_pattern": weekly,
        "series": seq,
        "self_check": ck.report(),
    }


# ══════════════════════════════════════════════════════════════════════
# outlier_scan
# ══════════════════════════════════════════════════════════════════════
def _seasonal_residual(s: pd.Series, dates: pd.Series | None, cycle: int,
                       keep: pd.Series | None = None) -> tuple[pd.Series, dict]:
    """去趋势、去季节后的残差。

    趋势：中心化滚动中位数，窗口 2×cycle+1；季节：按周期内位置取中位数。
    keep=False 的点保留位置，但不参与趋势与季节的估计。
    """
    if dates is not None and cycle == 7:                          # 周周期按星期取位
        pos = pd.to_datetime(dates).dt.dayofweek
    else:                                                         # 其余按序号取模
        pos = pd.Series(np.arange(len(s)) % cycle, index=s.index)

    clean = s if keep is None else s.where(keep)
    win = 2 * cycle + 1
    trend = clean.rolling(win, center=True, min_periods=2).median().bfill().ffill()
    detrended = s - trend
    ref = detrended if keep is None else detrended.where(keep)
    seasonal = ref.groupby(pos).transform("median").fillna(0.0)
    return detrended - seasonal, {
        "cycle": cycle,
        "trend_window": win,
        "seasonal_amplitude": _r(float(seasonal.max() - seasonal.min())),
    }


def _weekly_cycle(s: pd.Series, dates: pd.Series | None) -> int | None:
    """日粒度且星期中位数峰谷比明显时返回 7，否则 None。"""
    if dates is None or len(s) < 14 or not _is_daily(dates):
        return None
    g = s.groupby(pd.to_datetime(dates).dt.dayofweek).median()
    if len(g) >= 5 and g.min() > 0 and float(g.max() / g.min()) > WEEKLY_RATIO_MIN:
        return 7
    return None


def op_outlier_scan(df: pd.DataFrame, p: dict) -> dict:
    metric_f = p.get("metric_field")
    cutoff = float(p.get("z_cutoff", Z_CUTOFF_DEFAULT))
    time_f = p.get("time_field")
    seasonality = p.get("seasonality", "auto")
    flag_f = p.get("skip_flag_field")
    robust = bool(p.get("robust", True))

    if not metric_f:
        return _fail("invalid_request", "缺少 metric_field")
    err = _missing(df, *[f for f in (metric_f, time_f, flag_f) if f])
    if err:
        return err

    work = df.copy()
    work["_v"] = pd.to_numeric(work[metric_f], errors="coerce")
    work = work.dropna(subset=["_v"])

    # 标记日留在序列里参与去趋势，只在估计基线与报告离群时排除
    keep = pd.Series(True, index=work.index)
    if flag_f:
        keep = ~work[flag_f].astype(bool)
    skipped = int((~keep).sum())
    if int(keep.sum()) < 3:
        return _fail("too_few_rows",
                     f"可用数据点仅 {int(keep.sum())} 个（已排除 {skipped} 个标记日），至少需要 3 个")

    s = work["_v"]
    dates = work[time_f] if time_f else None

    cycle = None
    if seasonality == "auto":
        cycle = _weekly_cycle(s[keep], dates[keep] if dates is not None else None)
    elif isinstance(seasonality, int) and seasonality > 1:
        cycle = seasonality
    adjustment = None
    if cycle:
        resid, adjustment = _seasonal_residual(s, dates, cycle, keep)
    else:
        resid = s

    ref = resid[keep]
    if robust:
        center = float(ref.median())
        spread = float((ref - center).abs().median()) * 1.4826    # MAD 换算为 σ
        spread_name = "MAD×1.4826"
    else:
        center, spread, spread_name = float(ref.mean()), float(ref.std()), "std"
    if spread <= 0:
        return {"status": "degenerate", "metric_field": metric_f,
                "detail": f"离散度为 0（{spread_name}=0），无法做偏离检测"}

    low, high = center - cutoff * spread, center + cutoff * spread
    cand = resid[keep]
    hits = cand[(cand < low) | (cand > high)]
    outliers = []
    for i, rv in hits.items():
        item = {"row": int(i), "value": _r(float(s.loc[i])),
                "z": _r(abs(rv - center) / spread, 2),
                "side": "偏高" if rv > center else "偏低"}
        if time_f:
            item["time"] = str(work.loc[i, time_f])
        if cycle:
            item["residual"] = _r(float(rv))
        outliers.append(item)
    outliers.sort(key=lambda x: -x["z"])

    ck = Checks()
    ck.close_to("下界可重算", low, center - cutoff * spread, 1e-6)
    ck.close_to("上界可重算", high, center + cutoff * spread, 1e-6)
    weak = [x for x in outliers if x["z"] < cutoff - 1e-6]
    ck.add("离群点偏离达标", not weak, "error", len(weak), 0)
    rate = len(outliers) / max(int(keep.sum()), 1)
    ck.add("离群率不超过 10%", rate <= 0.10, "warn", f"{rate:.1%}", "≤10%",
           "离群率偏高，可检查周期是否已去除或 z_cutoff 是否过低")

    return {
        "status": "ok", "metric_field": metric_f, "z_cutoff": cutoff,
        "baseline_method": (f"{f'去季节(周期={cycle}) + ' if cycle else ''}"
                            f"{'鲁棒' if robust else '常规'}基线（{spread_name}）"),
        "seasonal_adjustment": adjustment,
        "skipped_flagged_rows": skipped,
        "center": _r(center), "spread": _r(spread),
        "band": {"low": _r(low), "high": _r(high)},
        "point_count": int(keep.sum()),
        "outlier_count": len(outliers),
        "outlier_rate_pct": _r(rate * 100, 2),
        "outliers": outliers[:50],
        "guidance": ("未检出离群点；持续性的水平下移不属于单点离群，可用 dim_breakdown 按维度拆解后查看"
                     if not outliers else
                     f"检出 {len(outliers)} 个离群点"
                     + (f"（已去除周期成分，排除 {skipped} 个标记日）" if cycle or skipped else "")),
        "self_check": ck.report(),
    }


# ══════════════════════════════════════════════════════════════════════
# dim_breakdown / dim_rank
# ══════════════════════════════════════════════════════════════════════
def _role_of(share_pct: float) -> str:
    ap = abs(share_pct)
    return "主要" if ap >= ROLE_MAJOR_PCT else ("次要" if ap >= ROLE_MINOR_PCT else "")


def _divergence_level(level_pp: float) -> str:
    return "高" if level_pp >= DIVERGENCE_HIGH else ("中" if level_pp >= DIVERGENCE_MID else "低")


def _cut_windows(df: pd.DataFrame, p: dict, time_f: str | None):
    """切出基期与当期。返回 (baseline_df, current_df, window_meta) 或 (None, None, 错误信息)。

    优先级：baseline_window + current_window > split_mode="last_two" > 按时间中点二分（默认）。
    两段天数不同时按日均归一。
    """
    if not time_f:
        half = len(df) // 2
        return df.iloc[:half], df.iloc[half:], {"mode": "halves_by_row", "norm": [1.0, 1.0]}
    if time_f not in df.columns:
        return None, None, f"时间列不存在：{time_f}"

    d = df.copy()
    d[time_f] = pd.to_datetime(d[time_f], errors="coerce")
    d = d.dropna(subset=[time_f])
    if d.empty:
        return None, None, f"时间列 {time_f} 无法解析为日期"

    bw, cw = p.get("baseline_window"), p.get("current_window")
    if bw and cw:
        if len(bw) != 2 or len(cw) != 2:
            return None, None, "baseline_window / current_window 需为 [起, 止] 两个可解析为日期的字符串"
        b0, b1 = pd.to_datetime(bw[0]), pd.to_datetime(bw[1])
        c0, c1 = pd.to_datetime(cw[0]), pd.to_datetime(cw[1])
        base = d[(d[time_f] >= b0) & (d[time_f] <= b1)]
        curr = d[(d[time_f] >= c0) & (d[time_f] <= c1)]
        nb, nc = base[time_f].nunique() or 1, curr[time_f].nunique() or 1
        return base, curr, {"mode": "explicit", "norm": [1.0 / nb, 1.0 / nc],
                            "baseline_days": nb, "current_days": nc}

    uniq = np.sort(d[time_f].unique())
    if len(uniq) < 2:
        return None, None, "时间列去重后不足 2 期，无法前后对比"
    if p.get("split_mode", "halves") == "last_two":
        return (d[d[time_f] == uniq[-2]], d[d[time_f] == uniq[-1]],
                {"mode": "last_two", "norm": [1.0, 1.0]})

    cut = uniq[len(uniq) // 2]
    base, curr = d[d[time_f] < cut], d[d[time_f] >= cut]
    nb, nc = base[time_f].nunique() or 1, curr[time_f].nunique() or 1
    return base, curr, {"mode": "halves", "norm": [1.0 / nb, 1.0 / nc],
                        "baseline_days": nb, "current_days": nc,
                        "cut_at": pd.Timestamp(cut).strftime("%Y-%m-%d")}


def _divergence(members: list[dict]) -> tuple[dict, float, str | None]:
    """各成员变化率的体量加权离散度（百分点）；基期为 0 的成员不参与计算。"""
    usable = [m for m in members if m["delta_pct"] is not None and m["baseline"]]
    if not usable:
        return {"divergence_pp": 0.0, "mean_rate_pct": 0.0}, 0.0, None
    w = np.array([abs(float(m["baseline"])) for m in usable], dtype=float)
    w = w / w.sum() if w.sum() else np.full(len(usable), 1.0 / len(usable))
    rate = np.array([float(m["delta_pct"]) for m in usable], dtype=float)
    avg_rate = float((w * rate).sum())
    spread_pp = float(np.sqrt((w * (rate - avg_rate) ** 2).sum()))
    gaps = np.abs(rate - avg_rate)
    top_idx = int(gaps.argmax())
    return {"divergence_pp": spread_pp, "mean_rate_pct": avg_rate}, float(gaps[top_idx]), usable[top_idx]["member"]


def _breakdown(df: pd.DataFrame, metric_f: Any, dim_f: Any, p: dict) -> dict | str:
    base, curr, meta = _cut_windows(df, p, p.get("time_field"))
    if base is None:
        return meta
    nb, nc = meta["norm"]
    for part in (base, curr):
        part[metric_f] = pd.to_numeric(part[metric_f], errors="coerce")
    if base[metric_f].notna().sum() == 0 and curr[metric_f].notna().sum() == 0:
        return f"列 {metric_f!r} 没有可解析为数值的取值"
    base_sum = base.groupby(dim_f)[metric_f].sum() * nb
    curr_sum = curr.groupby(dim_f)[metric_f].sum() * nc
    keys = sorted(set(base_sum.index) | set(curr_sum.index), key=str)
    if len(keys) < 2:
        return "degenerate"

    members = []
    for k in keys:
        b, c = float(base_sum.get(k, 0.0)), float(curr_sum.get(k, 0.0))
        members.append({"member": str(k), "baseline": _r(b), "current": _r(c), "delta": _r(c - b),
                        "delta_pct": _r((c - b) / abs(b) * 100, 2) if b else None})

    net = sum(m["delta"] for m in members)
    gross = sum(abs(m["delta"]) for m in members)
    ratio = abs(net) / gross if gross else 0.0
    reliable = ratio >= NET_GROSS_FLOOR                            # 净/毛比低于下限时净占比不可用
    for m in members:
        m["gross_share_pct"] = _r(m["delta"] / gross * 100, 2) if gross else 0.0
        m["net_share_pct"] = _r(m["delta"] / net * 100, 2) if net else None
        m["role"] = _role_of(m["gross_share_pct"])

    drivers = sorted([m for m in members if m["delta"] > 0], key=lambda m: -m["delta"])
    detractors = sorted([m for m in members if m["delta"] < 0], key=lambda m: m["delta"])

    div, max_gap, top_member = _divergence(members)
    for m in members:
        m["gap_pp"] = None if m["delta_pct"] is None else _r(m["delta_pct"] - div["mean_rate_pct"], 2)

    return {
        "dim_field": dim_f, "window": meta,
        "net_delta": _r(net), "gross_delta": _r(gross),
        "net_to_gross": _r(ratio, 3),
        "net_share_reliable": bool(reliable),
        "net_direction": "rising" if net > 0 else ("falling" if net < 0 else "flat"),
        "divergence_pp": _r(div["divergence_pp"], 2),
        "divergence_level": _divergence_level(div["divergence_pp"]),
        "mean_rate_pct": _r(div["mean_rate_pct"], 2),
        "max_gap_pp": _r(max_gap, 2),
        "most_divergent_member": top_member,
        "max_gross_share_pct": _r(max((abs(m["gross_share_pct"]) for m in members), default=0.0), 2),
        "drivers": drivers, "detractors": detractors, "members": members,
    }


def _breakdown_guidance(bd: dict) -> str:
    if not bd["net_share_reliable"]:
        return (f"净变动很小（净/毛 {bd['net_to_gross']}），内部结构变化明显。"
                "叙述时不要使用 net_share_pct；按 drivers 与 detractors 两侧分别陈述，"
                "用 gross_share_pct 表示各自占变动总量的份额。")
    if bd["divergence_pp"] < DIVERGENCE_MID:
        return (f"成员变化率高度同步（离散度 {bd['divergence_pp']} 个百分点），"
                "按该维度拆解未找到集中解释，本次波动更可能来自整体因素；不要在此维度上给出主因结论。")
    top = bd.get("max_gross_share_pct")
    tail = ""
    if isinstance(top, (int, float)) and top >= 50:
        tail = (f"；结论需带未解释部分——「{bd['most_divergent_member']}」占变动总量的 {top}%，"
                f"其余 {round(100 - top, 1)}% 分散在其他成员")
    return (f"离散度{bd['divergence_level']}（{bd['divergence_pp']} 个百分点），"
            f"偏离整体最多的是「{bd['most_divergent_member']}」，"
            "可从 detractors / drivers 首位展开并附证据" + tail + "。")


def op_dim_breakdown(df: pd.DataFrame, p: dict) -> dict:
    metric_f, dim_f = p.get("metric_field"), p.get("dim_field")
    err = _missing(df, metric_f, dim_f)
    if err:
        return err
    bd = _breakdown(df, metric_f, dim_f, p)
    if bd == "degenerate":
        return {"status": "degenerate", "dim_field": dim_f, "detail": "该维度去重后不足 2 个成员，无法拆解"}
    if isinstance(bd, str):
        return _fail("invalid_request", bd)

    kind = p.get("metric_kind", "additive")
    members = bd["members"]
    ck = Checks()
    if kind == "additive":
        ck.close_to("份额闭合", sum(abs(m["gross_share_pct"]) for m in members), 100.0, 0.02)
        ck.close_to("净变动闭合", sum(m["delta"] for m in members), bd["net_delta"], 0.01)
        if bd["net_share_reliable"]:
            ck.close_to("净占比闭合", sum(m["net_share_pct"] for m in members), 100.0, 0.02)
    else:
        ck.add(f"非可加指标跳过加和断言（metric_kind={kind}）", True, "info")
    for m in members[:20]:
        ck.close_to(f"变动可重算[{m['member']}]", m["delta"], m["current"] - m["baseline"], 0.01)
    ck.add("最大偏离不小于离散度",
           bd["most_divergent_member"] is None or bd["max_gap_pp"] >= bd["divergence_pp"] - 1e-6,
           "warn", bd["max_gap_pp"], f"≥{bd['divergence_pp']}")
    if not bd["net_share_reliable"]:
        ck.add("净变动过小，净占比不可用", False, "warn", bd["net_to_gross"], f"≥{NET_GROSS_FLOOR}",
               "net_share_pct 不可用于叙述")

    bd.update({"status": "ok", "metric_field": metric_f, "metric_kind": kind,
                "guidance": _breakdown_guidance(bd), "self_check": ck.report()})
    return bd


def op_dim_rank(df: pd.DataFrame, p: dict) -> dict:
    metric_f = p.get("metric_field")
    dim_fs = p.get("dim_fields") or []
    err = _missing(df, metric_f)
    if err:
        return err
    absent = [f for f in dim_fs if f not in df.columns]
    if absent or not dim_fs:
        return _fail("invalid_request", f"维度列缺失或为空：{absent or dim_fs}；可用列：{list(df.columns)}")

    ranking, skipped, by_dim = [], [], {}
    for dim_f in dim_fs:
        r = _breakdown(df, metric_f, dim_f, p)
        by_dim[dim_f] = r
        if isinstance(r, str):
            skipped.append({"dim_field": dim_f, "reason": r})
            continue
        ranking.append({
            "dim_field": dim_f,
            "divergence_pp": r["divergence_pp"],
            "divergence_level": r["divergence_level"],
            "mean_rate_pct": r["mean_rate_pct"],
            "most_divergent_member": r["most_divergent_member"],
            "max_gap_pp": r["max_gap_pp"],
            "top_driver": r["drivers"][0] if r["drivers"] else None,
            "top_detractor": r["detractors"][0] if r["detractors"] else None,
            "member_count": len(r["members"]),
        })
    if not ranking:
        return _fail("degenerate", "所有候选维度都无法拆解", skipped=skipped)

    ranking.sort(key=lambda x: -x["divergence_pp"])
    lead = ranking[0]
    first = by_dim[dim_fs[0]]
    net = first["net_delta"] if isinstance(first, dict) else None

    ck = Checks()
    ck.add("按离散度降序",
           all(ranking[i]["divergence_pp"] >= ranking[i + 1]["divergence_pp"] for i in range(len(ranking) - 1)),
           "error")
    ck.add("leading_dim 为离散度最高",
           lead["dim_field"] == max(ranking, key=lambda x: x["divergence_pp"])["dim_field"],
           "error", lead["dim_field"], None)

    return {
        "status": "ok", "metric_field": metric_f, "net_delta": net,
        "leading_dim": lead["dim_field"],
        "leading_divergence_level": lead["divergence_level"],
        "ranking": ranking, "skipped": skipped,
        "guidance": (
            f"各候选维度的成员变化率都高度同步（最高离散度 {lead['divergence_pp']} 个百分点），"
            "波动来自整体而非某一维度；请如实说明未找到集中解释"
            if lead["divergence_pp"] < DIVERGENCE_MID else
            f"「{lead['dim_field']}」维度分化最明显（离散度 {lead['divergence_pp']} 个百分点），"
            f"其中「{lead['most_divergent_member']}」偏离整体最多，建议优先核查"),
        "self_check": ck.report(),
    }


# ══════════════════════════════════════════════════════════════════════
# formula_breakdown
# ══════════════════════════════════════════════════════════════════════
def _make_evaluator(expr: str, names: list[str]) -> Callable[[dict], float]:
    """把因子公式编译成「因子取值 dict → 指标值」的函数。

    例：expr='visitors * conv_rate * aov'，names=['visitors', 'conv_rate', 'aov']。
    公式中出现 names 以外的名字时报错；求值环境不含 builtins。
    """
    compiled = compile(expr, "<expression>", "eval")
    for n in compiled.co_names:
        if n not in names:
            raise ValueError(f"公式引用了未提供的因子：{n}；可用因子：{names}")

    def evaluate(values: dict) -> float:
        return float(eval(compiled, {"__builtins__": {}}, dict(values)))  # noqa: S307
    return evaluate


def _shapley_effects(base: dict, curr: dict, fn: Callable[[dict], float]) -> dict[str, float]:
    """精确 Shapley 分解：φ_i = Σ_S |S|!(n-|S|-1)!/n! · [f(S∪{i}) − f(S)]，且 Σφ_i = f(current) − f(baseline)。"""
    names = list(base)
    n = len(names)
    fact = [math.factorial(i) for i in range(n + 1)]
    phi = {k: 0.0 for k in names}

    def value_at(subset: frozenset) -> float:
        return fn({k: (curr[k] if k in subset else base[k]) for k in names})

    for i in names:
        others = [k for k in names if k != i]
        for size in range(len(others) + 1):
            weight = fact[size] * fact[n - size - 1] / fact[n]
            for combo in combinations(others, size):
                s = frozenset(combo)
                phi[i] += weight * (value_at(s | {i}) - value_at(s))
    return phi


def op_formula_breakdown(p: dict) -> dict:
    base, curr, expr = p.get("baseline"), p.get("current"), p.get("expression")
    if not isinstance(base, dict) or not isinstance(curr, dict) or not expr:
        return _fail("invalid_request", "需要 baseline / current / expression 三个参数")
    if set(base) != set(curr):
        return _fail("invalid_request", f"基期与当期因子不一致：{sorted(base)} vs {sorted(curr)}")
    if len(base) > MAX_TERMS:
        return _fail("invalid_request", f"因子数 {len(base)} 超过上限 {MAX_TERMS}")
    requested = p.get("algorithm")
    algorithm = requested or "shapley"
    if algorithm not in ALGORITHMS_FORMULA:
        return _fail("invalid_request", f"未知 algorithm：{algorithm}；可选 {list(ALGORITHMS_FORMULA)}")

    try:
        fn = _make_evaluator(expr, list(base))
        v0, v1 = fn(base), fn(curr)
    except Exception as exc:                                       # noqa: BLE001
        return _fail("invalid_request", f"公式无法求值：{exc}")

    net = v1 - v0
    rate = abs(net / v0) if v0 else 1.0
    if algorithm == "delta_sum":
        effects = {k: float(curr[k]) - float(base[k]) for k in base}
    elif algorithm == "one_at_a_time":
        effects = {}
        for k in base:                                             # 逐项替换为当期值
            swapped = dict(base)
            swapped[k] = curr[k]
            effects[k] = fn(swapped) - v0
        total = sum(effects.values())
        if total:                                                  # 按净变动等比缩放
            effects = {k: v / total * net for k, v in effects.items()}
    else:
        effects = _shapley_effects(base, curr, fn)

    gross = sum(abs(e) for e in effects.values())
    ratio = abs(net) / gross if gross else 0.0
    reliable = ratio >= NET_GROSS_FLOOR

    terms = []
    for k, e in effects.items():
        b, c = float(base[k]), float(curr[k])
        terms.append({
            "term": k, "baseline": _r(b), "current": _r(c),
            "term_delta_pct": _r((c - b) / abs(b) * 100, 2) if b else None,
            "effect": _r(e),
            "gross_share_pct": _r(e / gross * 100, 2) if gross else 0.0,
            "net_share_pct": _r(e / net * 100, 2) if net else None,
        })
    for t in terms:
        t["role"] = _role_of(t["gross_share_pct"])
    terms.sort(key=lambda t: -abs(t["effect"]))

    ck = Checks()
    ck.close_to("因子效应闭合", sum(t["effect"] for t in terms), net, 0.01)
    ck.close_to("份额闭合", sum(abs(t["gross_share_pct"]) for t in terms), 100.0, 0.02)
    if reliable:
        ck.close_to("净占比闭合", sum(t["net_share_pct"] for t in terms), 100.0, 0.02)
    else:
        ck.add("因子涨跌相抵，净占比不可用", False, "warn", _r(ratio, 3), f"≥{NET_GROSS_FLOOR}",
               "改用 gross_share_pct 表述各因子占变动总量的份额")
    ck.close_to("公式差值自洽", v1 - v0, net, 1e-6)
    ck.add("leading_term 为效应最大者",
           terms[0]["term"] == max(terms, key=lambda t: abs(t["effect"]))["term"],
           "warn", terms[0]["term"], None)

    return {
        "status": "ok", "algorithm": algorithm, "expression": expr,
        "baseline_result": _r(v0), "current_result": _r(v1),
        "net_delta": _r(net), "gross_delta": _r(gross),
        "net_to_gross": _r(ratio, 3),
        "net_share_reliable": bool(reliable),
        "delta_rate_pct": _r(rate * 100, 2),
        "algorithm_note": (p.get("_legacy_note")
                           or (f"精确 Shapley（{len(base)} 个因子，枚举 {2 ** len(base)} 个子集）"
                               if algorithm == "shapley" and not requested else f"按请求使用 {algorithm}")),
        "guidance": ("各因子涨跌基本相抵、净变动接近 0，叙述时不要使用 net_share_pct；"
                     "请说明「总量变化不大，但构成在调整」，并用 gross_share_pct 陈述"
                     if not reliable else
                     f"效应最大的因子为「{terms[0]['term']}」，可按 effect 展开叙述"),
        "terms": terms, "leading_term": terms[0]["term"],
        "self_check": ck.report(),
    }


# ══════════════════════════════════════════════════════════════════════
# driver_correlation
# ══════════════════════════════════════════════════════════════════════
def _partial_out(y: np.ndarray, Z: np.ndarray) -> np.ndarray:
    """对控制变量做 OLS 回归后的残差。"""
    beta, *_ = np.linalg.lstsq(Z, y, rcond=None)
    return y - Z @ beta


def _magnitude(coef: float) -> str:
    a = abs(coef)
    return "高" if a >= COEF_HIGH else ("中" if a >= COEF_MID else "低")


def _coef_ci95(coef: float, n: int, algorithm: str) -> list | None:
    """Fisher z 变换的 95% 置信区间。

    标准误：pearson √(1/(n−3))；spearman √(1.06/(n−3))；kendall √(0.437/(n−4))。
    """
    if n <= 4:
        return None
    if abs(coef) >= 1.0:
        return [_r(coef, 4), _r(coef, 4)]
    if algorithm == "kendall":
        se = math.sqrt(0.437 / (n - 4))
    else:
        se = math.sqrt((1.06 if algorithm == "spearman" else 1.0) / (n - 3))
    z = math.atanh(coef)
    return [_r(math.tanh(z - 1.96 * se), 4), _r(math.tanh(z + 1.96 * se), 4)]


def op_driver_correlation(df: pd.DataFrame, p: dict) -> dict:
    try:
        from scipy.stats import kendalltau, pearsonr, spearmanr
    except ImportError as exc:                                     # noqa: BLE001
        return _fail("dependency_missing",
                     f"driver_correlation 需要 scipy：{exc}。未安装时只做描述性叙述，不要给出相关系数")

    metric_f = p.get("metric_field")
    drivers = p.get("driver_fields") or []
    controls = p.get("control_fields") or []
    algorithm = p.get("algorithm", "spearman")
    alpha = float(p.get("alpha", ALPHA_DEFAULT))
    if algorithm not in ALGORITHMS_CORR:
        return _fail("invalid_request", f"未知 algorithm：{algorithm}；可选 {list(ALGORITHMS_CORR)}")

    absent = [f for f in [metric_f, *drivers, *controls] if f not in df.columns]
    if absent:
        return _fail("invalid_request", f"列不存在：{absent}；可用列：{list(df.columns)}")
    if not drivers:
        return _fail("invalid_request", "driver_fields 不能为空")

    fields = [metric_f, *drivers, *controls]
    d = df[fields].apply(pd.to_numeric, errors="coerce").dropna()
    if len(d) < 8:
        return _fail("too_few_rows", f"完整样本仅 {len(d)} 行，至少需要 8 行")

    corr = {"spearman": spearmanr, "pearson": pearsonr, "kendall": kendalltau}[algorithm]
    y = d[metric_f].to_numpy(float)
    Z = np.c_[np.ones(len(d)), d[controls].to_numpy(float)] if controls else None
    ry = _partial_out(y, Z) if Z is not None else None

    pairs = []
    for f in drivers:
        x = d[f].to_numpy(float)
        if float(np.nanstd(x)) == 0.0:                             # 常数列：系数无定义
            pairs.append({"field": f, "coef": None, "p": None, "is_significant": False,
                          "magnitude": "不可计算", "sign": "取值恒定", "coef_ci95": None,
                          "control_test": None, "remark": "该列在样本内取值恒定，系数无定义"})
            continue
        with np.errstate(invalid="ignore"):
            coef_raw, p_raw = corr(y, x)
        coef_raw, p_raw = float(coef_raw), float(p_raw)
        if not (math.isfinite(coef_raw) and math.isfinite(p_raw)):
            pairs.append({"field": f, "coef": None, "p": None, "is_significant": False,
                          "magnitude": "不可计算", "sign": "无法判定", "coef_ci95": None,
                          "control_test": None, "remark": "系数无定义（样本退化或含无穷值）"})
            continue
        item = {
            "field": f, "coef": _r(coef_raw, 4), "p": _r(p_raw, 6),
            "is_significant": bool(p_raw < alpha),
            "magnitude": _magnitude(coef_raw),
            "sign": "同向" if coef_raw > 0 else ("反向" if coef_raw < 0 else "无"),
            "coef_ci95": _coef_ci95(coef_raw, len(d), algorithm),
            "control_test": None,
        }
        if Z is not None and item["is_significant"]:               # 显著的驱动列再做控制后检验
            coef_c, p_c = corr(ry, _partial_out(x, Z))
            coef_c, p_c = float(coef_c), float(p_c)
            item["control_test"] = {
                "controls": controls,
                "coef_after_control": _r(coef_c, 4), "p_after_control": _r(p_c, 6),
                "holds_after_control": bool(p_c < alpha),
                "reading": ("控制后仍显著，关联较稳健" if p_c < alpha
                            else "控制后不再显著，原关联更可能来自控制变量"),
            }
        pairs.append(item)
    pairs.sort(key=lambda i: (i["coef"] is None, -abs(i["coef"] or 0.0)))   # 系数无定义的排最后

    coef_mat = None
    if p.get("with_matrix", True) and len(fields) >= 3:
        m = d[fields].corr(method=algorithm)
        coef_mat = {"fields": fields, "values": [[_r(v, 4) for v in row_vals] for row_vals in m.to_numpy()]}

    ck = Checks()
    for it in pairs:
        if it["coef"] is None:
            ck.add(f"跳过不可计算的列[{it['field']}]", True, "info")
            continue
        ck.in_range(f"系数取值域[{it['field']}]", it["coef"], -1, 1)
        ck.in_range(f"p 取值域[{it['field']}]", it["p"], 0, 1)
        ck.add(f"显著性标记一致[{it['field']}]", it["is_significant"] == (it["p"] < alpha), "error")
        ck.add(f"强度分级一致[{it['field']}]", it["magnitude"] == _magnitude(it["coef"]),
               "error", it["magnitude"], _magnitude(it["coef"]))
        ci = it["coef_ci95"]
        if ci:
            ck.add(f"置信区间包含系数[{it['field']}]", ci[0] - 1e-6 <= it["coef"] <= ci[1] + 1e-6,
                   "error", it["coef"], ci)
    if coef_mat:
        vals = coef_mat["values"]
        ok_idx = [i for i in range(len(vals)) if vals[i][i] is not None]   # 常数列的对角为 None，跳过
        if len(vals) - len(ok_idx):
            ck.add(f"相关矩阵跳过 {len(vals) - len(ok_idx)} 个恒定列", True, "info")
        ck.add("相关矩阵对角为 1", all(_rel_close(vals[i][i], 1.0, 1e-6) for i in ok_idx), "error")
        ck.add("相关矩阵对称",
               all(_rel_close(vals[i][j], vals[j][i], 1e-6) for i in ok_idx for j in ok_idx if j < i),
               "error")

    return {
        "status": "ok", "metric_field": metric_f, "algorithm": algorithm,
        "n_rows": len(d), "control_fields": controls,
        "leading_field": next((i["field"] for i in pairs if i["coef"] is not None), None),
        "pairs": pairs, "coef_matrix": coef_mat,
        "disclaimer": "相关系数只说明两者同向或反向变动，不能据此认定因果；下结论前请结合业务机制核实",
        "self_check": ck.report(),
    }


# ══════════════════════════════════════════════════════════════════════
# 分发
# ══════════════════════════════════════════════════════════════════════
TABLE_OPS = {
    "trend_profile": op_trend_profile,
    "outlier_scan": op_outlier_scan,
    "dim_breakdown": op_dim_breakdown,
    "dim_rank": op_dim_rank,
    "driver_correlation": op_driver_correlation,
}
PARAM_OPS = {"formula_breakdown": op_formula_breakdown}
ALL_OPS = sorted([*TABLE_OPS, *PARAM_OPS])


def run(op: str, data_path: str | None, params: dict) -> dict:
    op = _LEGACY_OPS.get(op, op)
    params = _normalize_params(op, params)
    if op in PARAM_OPS:
        return PARAM_OPS[op](params)
    if op not in TABLE_OPS:
        return _fail("invalid_request", f"未知 op：{op}；可用：{ALL_OPS}")
    if not data_path:
        return _fail("invalid_request", f"op={op} 需要 --data（CSV 路径）")
    path = Path(data_path)
    if not path.exists():
        return _fail("invalid_request", f"CSV 不存在：{data_path}")
    try:
        df = pd.read_csv(path)
    except Exception as exc:                                       # noqa: BLE001
        return _fail("invalid_request", f"CSV 读取失败：{exc}")
    if df.empty:
        return _fail("too_few_rows", "CSV 为空")
    return TABLE_OPS[op](df, params)


# ══════════════════════════════════════════════════════════════════════
# --params 解析
# ══════════════════════════════════════════════════════════════════════
def _short_hand_to_literal(s: str):
    """把无引号简写 {key:val, ...} 转为 dict。

    键、值可带或不带引号；值可为字符串、数字、布尔、null、[]、{} 嵌套。
    只构造字面量，不执行表达式。
    """
    s = s.strip()
    if not (s.startswith("{") and s.endswith("}")):
        return None
    inner = s[1:-1].strip()
    if not inner:
        return {}
    parsed: dict = {}
    for part in _split_top_level(inner, ","):
        part = part.strip()
        if not part:
            continue
        kv = _split_top_level(part, ":")
        if len(kv) != 2:
            raise ValueError(f"--params 简写的键值对缺少冒号: {part!r}")
        parsed[_literal_token(kv[0].strip(), expect_string=True)] = _literal_token(kv[1].strip())
    return parsed


def _split_top_level(text: str, sep: str) -> list[str]:
    """按分隔符切分，引号、方括号、花括号内的分隔符不切（支持嵌套）。"""
    pieces: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    for i, ch in enumerate(text):
        if quote:
            buf.append(ch)
            if ch == quote and (i == 0 or text[i - 1] != "\\"):
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        if ch == sep and depth == 0:
            pieces.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        pieces.append("".join(buf).strip())
    return pieces


def _literal_token(raw: str, *, expect_string: bool = False):
    """单个 token 转字面量：数字、布尔、None、数组、对象保留类型，其余按字符串。"""
    token = raw.strip()
    if not token:
        return None
    if len(token) >= 2 and token[0] in ("'", '"') and token[-1] == token[0]:
        inner = token[1:-1]
        for q in ("'", '"', "\\", "\n", "\t"):
            if q in inner:
                raise ValueError(f"--params 值里出现未转义字符 {q!r}: {raw!r}")
        return inner
    low = token.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "none"):
        return None
    try:
        return ast.literal_eval(token)
    except Exception:                                              # noqa: BLE001
        pass
    if token.startswith("[") and token.endswith("]"):
        inner = token[1:-1].strip()
        return [_literal_token(it) for it in _split_top_level(inner, ",")] if inner else []
    if token.startswith("{") and token.endswith("}"):
        return _short_hand_to_literal(token)
    return token


def _parse_params(raw: str) -> dict:
    """解析 --params，依次尝试以下形态，全部失败才报错：

    ① 标准 JSON：           {"metric_field":"gmv"}
    ② 外层成对引号：        '{"metric_field":"gmv"}'
    ③ Python 字面量：       {'metric_field':'gmv'}
    ④ 无引号简写：          {time_field:dt,metric_field:gmv}
    ⑤ 简写 + 外层引号：     '{metric_field:gmv,z_cutoff:3.0}'
    ⑥ 空串或只有引号：      报错并回显原始输入

    只求值字面量，不执行表达式。
    """
    s = (raw or "").strip()
    if len(s) >= 2 and s[0] in ("'", '"') and s[-1] == s[0]:
        s = s[1:-1].strip()
    if not s:
        raise ValueError('--params 为空，请传 JSON，如 {"metric_field":"gmv"}')
    try:
        obj = json.loads(s)
    except Exception:                                              # noqa: BLE001
        try:
            obj = ast.literal_eval(s)
        except Exception:                                          # noqa: BLE001
            try:
                obj = _short_hand_to_literal(s)
            except Exception as exc:
                raise ValueError(f"无法解析 --params（前 80 个字符）: {raw.strip()[:80]}") from exc
    if not isinstance(obj, dict):
        raise ValueError("--params 必须是 JSON 对象")
    return obj


def _emit(body: dict) -> None:
    # 转义为纯 ASCII（\uXXXX），输出与调用方的解码编码无关
    print(json.dumps(body, ensure_ascii=True, default=str))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="data-insight 统计算子")
    ap.add_argument("op", nargs="?", help=f"算子名：{', '.join(ALL_OPS)}")
    ap.add_argument("--op", dest="op_flag", help=argparse.SUPPRESS)          # 旧写法
    ap.add_argument("--data", "--csv_path", dest="data", help="CSV 路径")
    ap.add_argument("--params", "--args", dest="params", default="{}", help="JSON 参数")
    cli = ap.parse_args(argv)

    op = cli.op or cli.op_flag
    if not op or (cli.op and cli.op_flag and cli.op != cli.op_flag):
        _emit(_fail("invalid_request", f"请指定一个 op：{ALL_OPS}"))
        return 2
    canonical = _LEGACY_OPS.get(op, op)
    if canonical not in ALL_OPS:
        _emit(_fail("invalid_request", f"未知 op：{op}；可用：{ALL_OPS}"))
        return 2
    try:
        params = _parse_params(cli.params)
    except Exception as exc:                                       # noqa: BLE001
        _emit(_fail("invalid_request", f"--params 解析失败：{exc}", op=canonical))
        return 2

    body = run(canonical, cli.data, params)
    body.setdefault("op", canonical)
    _emit(body)
    return 0 if body.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
