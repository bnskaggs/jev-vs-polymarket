from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean
from typing import Any


def brier(rows: list[dict[str, Any]], prob_key: str, y_key: str = "resolved_yes") -> float | None:
    values = [(row.get(prob_key), row.get(y_key)) for row in rows if row.get(prob_key) is not None]
    if not values:
        return None
    return mean((float(p) - int(y)) ** 2 for p, y in values)


def base_rate_brier(rows: list[dict[str, Any]], y_key: str = "resolved_yes") -> float | None:
    if not rows:
        return None
    base = mean(int(row[y_key]) for row in rows)
    return mean((base - int(row[y_key])) ** 2 for row in rows)


def brier_skill(score: float | None, baseline: float | None) -> float | None:
    if score is None or baseline is None or baseline == 0:
        return None
    return 1 - score / baseline


def reliability_bins(
    rows: list[dict[str, Any]],
    prob_key: str,
    y_key: str = "resolved_yes",
    bins: int = 10,
) -> list[dict[str, float | int]]:
    output: list[dict[str, float | int]] = []
    usable = [row for row in rows if row.get(prob_key) is not None]
    for idx in range(bins):
        lo = idx / bins
        hi = (idx + 1) / bins
        bucket = [
            row
            for row in usable
            if lo <= float(row[prob_key]) < hi or (idx == bins - 1 and float(row[prob_key]) == 1.0)
        ]
        if not bucket:
            continue
        avg_p = mean(float(row[prob_key]) for row in bucket)
        obs = mean(int(row[y_key]) for row in bucket)
        output.append(
            {
                "bin": f"{lo:.1f}-{hi:.1f}",
                "lo": lo,
                "hi": hi,
                "n": len(bucket),
                "avg_prob": avg_p,
                "observed": obs,
                "gap": obs - avg_p,
            }
        )
    return output


def ece(rows: list[dict[str, Any]], prob_key: str, y_key: str = "resolved_yes", bins: int = 10) -> float | None:
    usable = [row for row in rows if row.get(prob_key) is not None]
    if not usable:
        return None
    err = 0.0
    for bucket in reliability_bins(usable, prob_key, y_key, bins):
        err += (int(bucket["n"]) / len(usable)) * abs(float(bucket["gap"]))
    return err


def auc(rows: list[dict[str, Any]], prob_key: str, y_key: str = "resolved_yes") -> float | None:
    pairs = [(float(row[prob_key]), int(row[y_key])) for row in rows if row.get(prob_key) is not None]
    pos = [score for score, y in pairs if y == 1]
    neg = [score for score, y in pairs if y == 0]
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return wins / (len(pos) * len(neg))


def routing_table(rows: list[dict[str, Any]], prob_key: str, y_key: str = "resolved_yes") -> list[dict[str, float | int]]:
    output = []
    usable = [row for row in rows if row.get(prob_key) is not None]
    for margin in (0.0, 0.1, 0.2, 0.3, 0.4, 0.45, 0.49):
        auto = [row for row in usable if abs(float(row[prob_key]) - 0.5) >= margin]
        flagged = [row for row in usable if abs(float(row[prob_key]) - 0.5) < margin]
        auto_err = sum((float(row[prob_key]) >= 0.5) != bool(int(row[y_key])) for row in auto)
        flagged_err = sum((float(row[prob_key]) >= 0.5) != bool(int(row[y_key])) for row in flagged)
        output.append(
            {
                "margin": margin,
                "auto_n": len(auto),
                "auto_share": len(auto) / len(usable) if usable else 0,
                "auto_error_rate": auto_err / len(auto) if auto else 0,
                "flagged_n": len(flagged),
                "flagged_error_rate": flagged_err / len(flagged) if flagged else 0,
            }
        )
    return output


def correlation(rows: list[dict[str, Any]], x_key: str, y_key: str) -> float | None:
    values = [
        (float(row[x_key]), float(row[y_key]))
        for row in rows
        if row.get(x_key) is not None and row.get(y_key) is not None
    ]
    if len(values) < 2:
        return None
    xs = [x for x, _ in values]
    ys = [y for _, y in values]
    mx = mean(xs)
    my = mean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in values) / (sx * sy)


def summarize_by(rows: list[dict[str, Any]], key: str, prob_key: str = "jev_resolve_yes") -> list[dict[str, Any]]:
    buckets: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[row.get(key, "unknown")].append(row)
    output = []
    for value, bucket in sorted(buckets.items(), key=lambda item: str(item[0])):
        output.append(
            {
                key: value,
                "n": len(bucket),
                "yes_rate": mean(int(row["resolved_yes"]) for row in bucket),
                "brier": brier(bucket, prob_key),
                "ece": ece(bucket, prob_key),
                "auc": auc(bucket, prob_key),
            }
        )
    return output


def prediction_errors(rows: list[dict[str, Any]], prob_key: str = "jev_resolve_yes") -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if row.get(prob_key) is not None and (float(row[prob_key]) >= 0.5) != bool(int(row["resolved_yes"]))
    ]


def disagreement_rows(
    rows: list[dict[str, Any]],
    market_key: str,
    threshold: float = 0.3,
    prob_key: str = "jev_resolve_yes",
) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        if row.get(prob_key) is None or row.get(market_key) is None:
            continue
        delta = float(row[prob_key]) - float(row[market_key])
        if abs(delta) >= threshold:
            next_row = dict(row)
            next_row["delta"] = delta
            next_row["jev_right"] = int((float(row[prob_key]) >= 0.5) == bool(int(row["resolved_yes"])))
            next_row["market_right"] = int((float(row[market_key]) >= 0.5) == bool(int(row["resolved_yes"])))
            output.append(next_row)
    return sorted(output, key=lambda row: abs(float(row["delta"])), reverse=True)
