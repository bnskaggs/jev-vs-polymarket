from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

GAMMA_URL = "https://gamma-api.polymarket.com/markets"
CLOB_HISTORY_URL = "https://clob.polymarket.com/prices-history"


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    if " " in normalized and "T" not in normalized:
        normalized = normalized.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_jsonish(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _as_float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def normalize_market(raw: dict[str, Any]) -> dict[str, Any] | None:
    outcomes = _parse_jsonish(raw.get("outcomes"))
    prices = _parse_jsonish(raw.get("outcomePrices"))
    token_ids = _parse_jsonish(raw.get("clobTokenIds"))
    if not isinstance(outcomes, list) or not isinstance(prices, list) or not isinstance(token_ids, list):
        return None

    lower = [str(o).strip().lower() for o in outcomes]
    if sorted(lower) != ["no", "yes"]:
        return None
    yes_idx = lower.index("yes")
    no_idx = lower.index("no")
    if yes_idx >= len(prices) or yes_idx >= len(token_ids):
        return None

    yes_price = _as_float(prices[yes_idx])
    no_price = _as_float(prices[no_idx])
    if yes_price is None or no_price is None:
        return None
    if not (
        (yes_price >= 0.99 and no_price <= 0.01)
        or (yes_price <= 0.01 and no_price >= 0.99)
    ):
        return None

    closed_time = parse_dt(str(raw.get("closedTime") or raw.get("umaEndDate") or raw.get("endDate") or ""))
    if not closed_time:
        return None

    events = raw.get("events")
    first_event = events[0] if isinstance(events, list) and events else {}
    group = first_event.get("title") if isinstance(first_event, dict) else None
    event_slug = first_event.get("slug") if isinstance(first_event, dict) else None

    return {
        "id": str(raw.get("id")),
        "slug": raw.get("slug"),
        "question": raw.get("question") or "",
        "description": raw.get("description") or "",
        "outcomes": outcomes,
        "outcome_prices": prices,
        "resolved_yes": 1 if yes_price >= 0.99 else 0,
        "volume": _as_float(raw.get("volumeNum") or raw.get("volume")) or 0.0,
        "start_time": (parse_dt(str(raw.get("startDate") or raw.get("createdAt") or "")) or closed_time).isoformat(),
        "closed_time": closed_time.isoformat(),
        "uma_resolution_status": raw.get("umaResolutionStatus"),
        "yes_token_id": str(token_ids[yes_idx]),
        "no_token_id": str(token_ids[no_idx]) if no_idx < len(token_ids) else None,
        "neg_risk": bool(raw.get("negRisk")),
        "group_item_title": raw.get("groupItemTitle"),
        "group": group,
        "event_slug": event_slug,
        "is_grouped": bool(raw.get("groupItemTitle")),
    }


def fetch_closed_markets(
    *,
    volume_min: float,
    days: int,
    limit: int,
    max_rows: int,
    target: int,
) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    kept: list[dict[str, Any]] = []
    offset = 0
    with httpx.Client(timeout=30.0) as client:
        while offset < max_rows:
            request_limit = min(limit, 100)
            params = {
                "closed": "true",
                "volume_num_min": str(volume_min),
                "limit": str(request_limit),
                "offset": str(offset),
                "order": "closedTime",
                "ascending": "false",
            }
            resp = client.get(GAMMA_URL, params=params)
            if resp.status_code == 422:
                print(f"stopping at offset={offset}: gamma returned 422")
                break
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            for raw in batch:
                market = normalize_market(raw)
                if not market:
                    continue
                closed = parse_dt(market["closed_time"])
                if not closed or closed < cutoff:
                    continue
                kept.append(market)
            print(f"offset={offset} raw={len(batch)} kept={len(kept)}")
            if len(kept) >= target:
                break
            offset += len(batch)
    return kept


def fetch_price_history(token_id: str) -> list[dict[str, Any]]:
    params = {"market": token_id, "interval": "max", "fidelity": "1440"}
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(CLOB_HISTORY_URL, params=params)
        resp.raise_for_status()
        history = resp.json().get("history") or []
    points: list[dict[str, Any]] = []
    for point in history:
        try:
            ts = int(point["t"])
            price = float(point["p"])
        except (KeyError, TypeError, ValueError):
            continue
        points.append({"t": ts, "p": price})
    return sorted(points, key=lambda p: p["t"])


def cache_price_history(token_id: str, cache_dir: Path, *, sleep_s: float = 0.0) -> list[dict[str, Any]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{token_id}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    history = fetch_price_history(token_id)
    path.write_text(json.dumps(history, sort_keys=True), encoding="utf-8")
    if sleep_s:
        time.sleep(sleep_s)
    return history


def snapshot_price(history: list[dict[str, Any]], closed_time: datetime, horizon: str) -> float | None:
    if not history:
        return None
    if horizon == "open":
        return float(history[0]["p"])
    if horizon == "7d":
        target = closed_time - timedelta(days=7)
    elif horizon == "1d":
        target = closed_time - timedelta(days=1)
    else:
        raise ValueError(f"unknown horizon: {horizon}")
    target_ts = int(target.timestamp())
    eligible = [point for point in history if int(point["t"]) <= target_ts]
    if not eligible:
        return None
    return float(eligible[-1]["p"])


def attach_price_snapshots(row: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    closed_time = parse_dt(row["closed_time"])
    if not closed_time:
        raise ValueError(f"bad closed_time for market {row.get('id')}: {row.get('closed_time')}")
    next_row = dict(row)
    next_row["market_price_open"] = snapshot_price(history, closed_time, "open")
    next_row["market_price_7d"] = snapshot_price(history, closed_time, "7d")
    next_row["market_price_1d"] = snapshot_price(history, closed_time, "1d")
    next_row["price_points"] = len(history)
    return next_row
