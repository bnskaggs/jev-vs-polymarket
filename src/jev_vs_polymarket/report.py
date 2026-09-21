from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt


def fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._"
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(fmt(row.get(col)) for col in columns) + " |")
    return "\n".join(lines)


def plot_reliability(summary: dict[str, Any], out: Path) -> None:
    curves = {
        "Jev": summary["reliability"].get("jev_resolve_yes", []),
        "Market T-7d": summary["reliability"].get("market_price_7d", []),
        "Market T-24h": summary["reliability"].get("market_price_1d", []),
        "Market Open": summary["reliability"].get("market_price_open", []),
    }
    plt.figure(figsize=(7, 7))
    plt.plot([0, 1], [0, 1], linestyle="--", color="gray", label="Perfect calibration")
    for label, rows in curves.items():
        if not rows:
            continue
        xs = [float(row["avg_prob"]) for row in rows]
        ys = [float(row["observed"]) for row in rows]
        sizes = [max(20, int(row["n"]) * 3) for row in rows]
        plt.scatter(xs, ys, s=sizes, alpha=0.75, label=label)
        plt.plot(xs, ys, alpha=0.45)
    plt.xlabel("Average stated probability")
    plt.ylabel("Observed yes rate")
    plt.title("Reliability: Jev vs. Polymarket snapshots")
    plt.xlim(0, 1)
    plt.ylim(0, 1)
    plt.legend()
    plt.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out, dpi=160)
    plt.close()


def write_results_md(summary: dict[str, Any], out: Path, plot_path: Path) -> None:
    leaderboard_rows = []
    for key, label in (
        ("jev_resolve_yes", "Jev"),
        ("market_price_open", "Market open"),
        ("market_price_7d", "Market T-7d"),
        ("market_price_1d", "Market T-24h"),
    ):
        metrics = summary["models"].get(key, {})
        leaderboard_rows.append(
            {
                "model": label,
                "n": metrics.get("n"),
                "brier": metrics.get("brier"),
                "skill": metrics.get("brier_skill"),
                "ece": metrics.get("ece"),
                "auc": metrics.get("auc"),
            }
        )

    lines = [
        "# Jev vs. Polymarket results",
        "",
        f"_Run: `{summary['stamp']}`. Model: `{summary.get('model', 'unknown')}`. "
        f"Markets: {summary['n']}. Total cost: ${summary['cost_usd']:.6f}._",
        "",
        "## Headline",
        "",
        summary.get("headline", "_No headline generated._"),
        "",
        "## Leaderboard",
        "",
        markdown_table(leaderboard_rows, ["model", "n", "brier", "skill", "ece", "auc"]),
        "",
        "## Reliability",
        "",
        f"![Reliability plot]({plot_path.name})",
        "",
        "### Jev reliability bins",
        "",
        markdown_table(
            summary["reliability"].get("jev_resolve_yes", []),
            ["bin", "n", "avg_prob", "observed", "gap"],
        ),
        "",
        "## Routing table",
        "",
        markdown_table(
            summary.get("routing", []),
            ["margin", "auto_n", "auto_share", "auto_error_rate", "flagged_n", "flagged_error_rate"],
        ),
        "",
        "## Category breakdown",
        "",
        markdown_table(summary.get("by_category", []), ["jev_category", "n", "yes_rate", "brier", "ece", "auc"]),
        "",
        "## Numeric-threshold breakdown",
        "",
        markdown_table(
            summary.get("by_numeric_threshold_bucket", []),
            ["numeric_threshold_bucket", "n", "yes_rate", "brier", "ece", "auc"],
        ),
        "",
        "## Leakage split by close month",
        "",
        markdown_table(summary.get("by_close_month", []), ["close_month", "n", "yes_rate", "brier", "ece", "auc"]),
        "",
        "## Largest Jev vs. T-7d market disagreements",
        "",
        markdown_table(
            summary.get("largest_disagreements_7d", [])[:20],
            ["id", "question", "resolved_yes", "jev_resolve_yes", "market_price_7d", "delta", "jev_right", "market_right"],
        ),
        "",
        "## Scope",
        "",
        "- Retrospective resolved Polymarket markets; no trading signal is implied.",
        "- v0 input is question + resolution rules only. No news or outside context.",
        "- Older resolved markets may be in model training data; close-month split is included to inspect leakage.",
        "- Market prices are strong but imperfect calibration baselines, especially on low-liquidity or grouped markets.",
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
