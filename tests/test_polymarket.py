from __future__ import annotations

from datetime import datetime, timezone

from jev_vs_polymarket.polymarket import normalize_market, snapshot_price


def test_snapshot_price_uses_last_point_before_horizon() -> None:
    closed = datetime(2026, 9, 21, tzinfo=timezone.utc)
    history = [
        {"t": int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()), "p": 0.2},
        {"t": int(datetime(2026, 9, 13, tzinfo=timezone.utc).timestamp()), "p": 0.4},
        {"t": int(datetime(2026, 9, 14, tzinfo=timezone.utc).timestamp()), "p": 0.6},
        {"t": int(datetime(2026, 9, 20, tzinfo=timezone.utc).timestamp()), "p": 0.9},
    ]
    assert snapshot_price(history, closed, "open") == 0.2
    assert snapshot_price(history, closed, "7d") == 0.6
    assert snapshot_price(history, closed, "1d") == 0.9


def test_snapshot_price_returns_none_when_market_too_short() -> None:
    closed = datetime(2026, 9, 21, tzinfo=timezone.utc)
    history = [
        {"t": int(datetime(2026, 9, 20, tzinfo=timezone.utc).timestamp()), "p": 0.9},
    ]
    assert snapshot_price(history, closed, "7d") is None


def test_normalize_market_keeps_binary_resolved_yes() -> None:
    raw = {
        "id": 1,
        "slug": "example",
        "question": "Will it happen?",
        "description": "Rules.",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["1", "0"]',
        "clobTokenIds": '["yes-token", "no-token"]',
        "closedTime": "2026-09-20T00:00:00Z",
        "volumeNum": 12345,
        "umaResolutionStatus": "resolved",
        "events": [{"title": "Example event", "slug": "example-event"}],
    }
    market = normalize_market(raw)
    assert market is not None
    assert market["resolved_yes"] == 1
    assert market["yes_token_id"] == "yes-token"
    assert market["is_grouped"] is False


def test_normalize_market_marks_group_item_as_grouped() -> None:
    raw = {
        "id": 1,
        "question": "Will it happen?",
        "outcomes": ["Yes", "No"],
        "outcomePrices": ["0", "1"],
        "clobTokenIds": ["yes-token", "no-token"],
        "closedTime": "2026-09-20T00:00:00Z",
        "groupItemTitle": "Candidate A",
    }
    market = normalize_market(raw)
    assert market is not None
    assert market["resolved_yes"] == 0
    assert market["is_grouped"] is True


def test_normalize_market_rejects_unresolved_price() -> None:
    raw = {
        "id": 1,
        "question": "Will it happen?",
        "outcomes": ["Yes", "No"],
        "outcomePrices": ["0.55", "0.45"],
        "clobTokenIds": ["yes-token", "no-token"],
        "closedTime": "2026-09-20T00:00:00Z",
    }
    assert normalize_market(raw) is None
