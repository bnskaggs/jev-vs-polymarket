from __future__ import annotations

from jev_vs_polymarket.metrics import auc, brier, ece, reliability_bins, routing_table


ROWS = [
    {"p": 0.9, "resolved_yes": 1},
    {"p": 0.8, "resolved_yes": 1},
    {"p": 0.2, "resolved_yes": 0},
    {"p": 0.1, "resolved_yes": 0},
]


def test_brier() -> None:
    assert round(brier(ROWS, "p"), 4) == 0.0250


def test_auc_perfect_ordering() -> None:
    assert auc(ROWS, "p") == 1.0


def test_ece_uses_weighted_bin_gap() -> None:
    # Bins are perfectly ordered but underconfident: positive bins observe 1,
    # negative bins observe 0.
    assert round(ece(ROWS, "p"), 4) == 0.15


def test_reliability_bins() -> None:
    bins = reliability_bins(ROWS, "p")
    assert bins[0]["bin"] == "0.1-0.2"
    assert bins[-1]["bin"] == "0.9-1.0"
    assert bins[-1]["observed"] == 1


def test_routing_table_flags_midrange() -> None:
    rows = [
        {"p": 0.99, "resolved_yes": 1},
        {"p": 0.51, "resolved_yes": 0},
        {"p": 0.49, "resolved_yes": 1},
        {"p": 0.01, "resolved_yes": 0},
    ]
    table = routing_table(rows, "p")
    margin_40 = next(row for row in table if row["margin"] == 0.4)
    assert margin_40["auto_n"] == 2
    assert margin_40["auto_error_rate"] == 0
    assert margin_40["flagged_n"] == 2
