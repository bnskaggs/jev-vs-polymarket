from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from typesafe_sdk import Choice, Noul, TypeSafeClient

from .io import read_jsonl, utc_stamp, write_json, write_jsonl
from .metrics import (
    auc,
    base_rate_brier,
    brier,
    brier_skill,
    correlation,
    disagreement_rows,
    ece,
    reliability_bins,
    routing_table,
    summarize_by,
)
from .polymarket import (
    attach_price_snapshots,
    cache_price_history,
    fetch_closed_markets,
    fetch_price_history,
    normalize_market,
    parse_dt,
)
from .report import plot_reliability, write_results_md

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RESULTS = ROOT / "results"
PRICES = DATA / "prices"

JEV_PRICE_PER_INPUT_MTOK = 0.042

CATEGORIES = {
    "politics": "Elections, politicians, legislation, courts, geopolitics, government actions",
    "sports": "Sports matches, tournaments, player/team performance",
    "crypto_price": "Crypto token prices, market caps, launches, exchange listings, on-chain metrics",
    "finance_macro": "Macroeconomics, interest rates, inflation, equities, commodities, non-crypto finance",
    "entertainment_culture": "Movies, music, celebrity, awards, internet culture",
    "science_tech": "Science, technology, AI, products, space, health, non-crypto launches",
    "other": "Anything that does not fit the other labels",
}


def _default_path(folder: Path, prefix: str, suffix: str = ".jsonl") -> Path:
    return folder / f"{prefix}-{utc_stamp()}{suffix}"


def cmd_fetch(args: argparse.Namespace) -> None:
    out = args.out or _default_path(DATA, "markets")
    rows = fetch_closed_markets(
        volume_min=args.volume_min,
        days=args.days,
        limit=args.limit,
        max_rows=args.max_rows,
        target=args.target,
    )
    if args.group_cap:
        rows = cap_grouped_markets(rows, args.group_cap)
    count = write_jsonl(out, rows)
    print(f"wrote {count} markets -> {out}")


def cap_grouped_markets(rows: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    grouped_counts: dict[str, int] = {}
    output: list[dict[str, Any]] = []
    for row in rows:
        group = row.get("event_slug") or row.get("group") or row["id"]
        if row.get("is_grouped"):
            count = grouped_counts.get(group, 0)
            if count >= cap:
                continue
            grouped_counts[group] = count + 1
        output.append(row)
    return output


def cmd_prices(args: argparse.Namespace) -> None:
    rows = read_jsonl(args.markets)
    out = args.out or args.markets.with_name(args.markets.stem + "-priced.jsonl")
    priced: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, 1):
        history = cache_price_history(row["yes_token_id"], args.cache_dir, sleep_s=args.sleep)
        priced.append(attach_price_snapshots(row, history))
        if idx % 50 == 0 or idx == len(rows):
            print(f"priced {idx}/{len(rows)}")
    count = write_jsonl(out, priced)
    print(f"wrote {count} priced markets -> {out}")


def _state_for_market(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "question": row["question"],
        "resolution_rules": row.get("description") or "",
    }


def _hydrated_client() -> TypeSafeClient:
    if not os.environ.get("TYPESAFE_API_KEY"):
        user_key = os.environ.get("TYPESAFE_API_KEY") or None
        if user_key:
            os.environ["TYPESAFE_API_KEY"] = user_key
    if not os.environ.get("TYPESAFE_API_KEY"):
        raise SystemExit("TYPESAFE_API_KEY is not set.")
    return TypeSafeClient()


def cmd_jev(args: argparse.Namespace) -> None:
    rows = read_jsonl(args.markets)
    if args.limit:
        rows = rows[: args.limit]
    out = args.out or _default_path(RESULTS, "preds")
    client = _hydrated_client()
    predictions: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, 1):
        t0 = time.perf_counter()
        response = client.system_one(
            state=_state_for_market(row),
            questions={
                "resolve_yes": Noul(
                    instructions=(
                        "This market will resolve Yes. Answer only from the market question "
                        "and resolution rules, using general world knowledge and base rates."
                    )
                ),
                "category": Choice(
                    instructions="Which category best describes this prediction market?",
                    criteria=CATEGORIES,
                ),
                "numeric_threshold": Noul(
                    instructions=(
                        "The outcome hinges on a number, price, date, threshold, count, "
                        "market cap, spread, percentage, or similar quantitative cutoff."
                    )
                ),
            },
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        yes = response.answers["resolve_yes"]
        category = response.answers["category"]
        numeric = response.answers["numeric_threshold"]
        next_row = dict(row)
        next_row.update(
            {
                "model": response.model,
                "jev_resolve_yes": float(yes.noul),
                "jev_category": category.choice,
                "jev_category_confidence": float(category.confidence),
                "jev_category_probabilities": dict(category.probabilities),
                "jev_numeric_threshold": float(numeric.noul),
                "latency_ms": round(elapsed_ms, 1),
                "input_tokens": int(response.usage.input_tokens),
                "output_tokens": int(response.usage.output_tokens),
            }
        )
        predictions.append(next_row)
        if idx % 25 == 0 or idx == len(rows):
            print(f"jev {idx}/{len(rows)}")
    count = write_jsonl(out, predictions)
    print(f"wrote {count} predictions -> {out}")


def cmd_analyze(args: argparse.Namespace) -> None:
    rows = read_jsonl(args.preds)
    stamp = args.stamp or args.preds.stem.replace("preds-", "")
    summary = summarize(rows, stamp)
    summary_path = args.summary_out or RESULTS / f"{stamp}-summary.json"
    plot_path = args.plot_out or RESULTS / f"reliability-{stamp}.png"
    md_path = args.md_out or RESULTS / "results.md"
    write_json(summary_path, summary)
    plot_reliability(summary, plot_path)
    write_results_md(summary, md_path, plot_path)
    print(f"wrote summary -> {summary_path}")
    print(f"wrote plot    -> {plot_path}")
    print(f"wrote report  -> {md_path}")
    print(summary["headline"])


def summarize(rows: list[dict[str, Any]], stamp: str) -> dict[str, Any]:
    if not rows:
        raise ValueError("no rows to analyze")
    baseline = base_rate_brier(rows)
    model_keys = ["jev_resolve_yes", "market_price_open", "market_price_7d", "market_price_1d"]
    models: dict[str, Any] = {}
    reliability: dict[str, Any] = {}
    for key in model_keys:
        usable = [row for row in rows if row.get(key) is not None]
        score = brier(usable, key)
        models[key] = {
            "n": len(usable),
            "brier": score,
            "base_rate_brier": baseline,
            "brier_skill": brier_skill(score, baseline),
            "ece": ece(usable, key),
            "auc": auc(usable, key),
        }
        reliability[key] = reliability_bins(usable, key)

    total_input_tokens = sum(int(row.get("input_tokens") or 0) for row in rows)
    cost_usd = total_input_tokens / 1_000_000 * JEV_PRICE_PER_INPUT_MTOK
    lats = sorted(float(row["latency_ms"]) for row in rows if row.get("latency_ms") is not None)
    by_close_month = []
    for row in rows:
        closed = parse_dt(row.get("closed_time"))
        row["close_month"] = closed.strftime("%Y-%m") if closed else "unknown"
        row["is_grouped"] = bool(row.get("group_item_title"))
        row["numeric_threshold_bucket"] = (
            "numeric" if float(row.get("jev_numeric_threshold") or 0) >= 0.5 else "not_numeric"
        )

    disagreements_7d = disagreement_rows(rows, "market_price_7d")
    jev_brier = models["jev_resolve_yes"]["brier"]
    best_market = min(
        (
            (key, value["brier"])
            for key, value in models.items()
            if key.startswith("market_") and value["brier"] is not None
        ),
        key=lambda item: item[1],
        default=(None, None),
    )
    if best_market[1] is not None and jev_brier is not None:
        if jev_brier < best_market[1]:
            verdict = f"Jev beats the best market snapshot on Brier ({jev_brier:.4f} vs {best_market[1]:.4f}). Check for leakage."
        else:
            verdict = f"Best market snapshot beats Jev on Brier ({best_market[1]:.4f} vs {jev_brier:.4f})."
    else:
        verdict = "Could not compare Jev to market snapshots."

    return {
        "stamp": stamp,
        "n": len(rows),
        "model": rows[0].get("model"),
        "yes_rate": statistics.mean(int(row["resolved_yes"]) for row in rows),
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": sum(int(row.get("output_tokens") or 0) for row in rows),
        "cost_usd": cost_usd,
        "latency_ms_p50": percentile(lats, 50),
        "latency_ms_p95": percentile(lats, 95),
        "models": models,
        "reliability": reliability,
        "routing": routing_table(rows, "jev_resolve_yes"),
        "jev_market_correlation": {
            "open": correlation(rows, "jev_resolve_yes", "market_price_open"),
            "7d": correlation(rows, "jev_resolve_yes", "market_price_7d"),
            "1d": correlation(rows, "jev_resolve_yes", "market_price_1d"),
        },
        "by_category": summarize_by(rows, "jev_category"),
        "by_numeric_threshold_bucket": summarize_by(rows, "numeric_threshold_bucket"),
        "by_grouped": summarize_by(rows, "is_grouped"),
        "by_close_month": summarize_by(rows, "close_month"),
        "largest_disagreements_7d": compact_disagreements(disagreements_7d[:50]),
        "headline": verdict,
    }


def percentile(values: list[float], p: int) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    idx = round((p / 100) * (len(values) - 1))
    return values[idx]


def compact_disagreements(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = [
        "id",
        "question",
        "resolved_yes",
        "jev_resolve_yes",
        "market_price_7d",
        "delta",
        "jev_right",
        "market_right",
    ]
    compact = []
    for row in rows:
        item = {key: row.get(key) for key in keys}
        if isinstance(item.get("question"), str) and len(item["question"]) > 90:
            item["question"] = item["question"][:87] + "..."
        compact.append(item)
    return compact


def cmd_snapshot_open(args: argparse.Namespace) -> None:
    """Forward-test snapshot: currently open markets, Jev p + current market price."""
    import httpx

    out = args.out or _default_path(DATA, "open-snapshot")
    rows: list[dict[str, Any]] = []
    offset = 0
    with httpx.Client(timeout=30.0) as http:
        while offset < args.max_rows:
            request_limit = min(args.limit, 100)
            resp = http.get(
                "https://gamma-api.polymarket.com/markets",
                params={
                    "closed": "false",
                    "active": "true",
                    "volume_num_min": str(args.volume_min),
                    "limit": str(request_limit),
                    "offset": str(offset),
                },
            )
            if resp.status_code == 422:
                print(f"stopping at offset={offset}: gamma returned 422")
                break
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            for raw in batch:
                market = normalize_market_for_open(raw)
                if market:
                    rows.append(market)
            if len(rows) >= args.target:
                break
            offset += len(batch)
    count = write_jsonl(out, rows)
    print(f"wrote {count} open-market snapshot rows -> {out}")
    if args.score:
        print("Scoring current open markets with Jev.")
        score_args = argparse.Namespace(markets=out, out=out.with_name(out.stem + "-preds.jsonl"), limit=None)
        cmd_jev(score_args)


def normalize_market_for_open(raw: dict[str, Any]) -> dict[str, Any] | None:
    market = normalize_market({**raw, "outcomePrices": raw.get("outcomePrices")})
    if market:
        # normalize_market only accepts already-resolved prices; open markets need looser handling.
        return None
    from .polymarket import _as_float, _parse_jsonish  # local import keeps this internal to the CLI.

    outcomes = _parse_jsonish(raw.get("outcomes"))
    prices = _parse_jsonish(raw.get("outcomePrices"))
    token_ids = _parse_jsonish(raw.get("clobTokenIds"))
    if not isinstance(outcomes, list) or not isinstance(prices, list) or not isinstance(token_ids, list):
        return None
    lower = [str(o).strip().lower() for o in outcomes]
    if sorted(lower) != ["no", "yes"]:
        return None
    yes_idx = lower.index("yes")
    yes_price = _as_float(prices[yes_idx]) if yes_idx < len(prices) else None
    if yes_price is None:
        return None
    first_event = raw.get("events")[0] if isinstance(raw.get("events"), list) and raw.get("events") else {}
    return {
        "id": str(raw.get("id")),
        "slug": raw.get("slug"),
        "question": raw.get("question") or "",
        "description": raw.get("description") or "",
        "outcomes": outcomes,
        "current_yes_price": yes_price,
        "volume": _as_float(raw.get("volumeNum") or raw.get("volume")) or 0.0,
        "start_time": raw.get("startDate") or raw.get("createdAt"),
        "closed_time": raw.get("endDate"),
        "yes_token_id": str(token_ids[yes_idx]),
        "neg_risk": bool(raw.get("negRisk")),
        "group_item_title": raw.get("groupItemTitle"),
        "group": first_event.get("title") if isinstance(first_event, dict) else None,
        "event_slug": first_event.get("slug") if isinstance(first_event, dict) else None,
        "is_grouped": bool(raw.get("groupItemTitle")),
    }


def cmd_resolve(args: argparse.Namespace) -> None:
    """Fill outcomes for a prior open-market snapshot when markets have resolved."""
    import httpx

    rows = read_jsonl(args.snapshot)
    resolved: list[dict[str, Any]] = []
    with httpx.Client(timeout=30.0) as client:
        for idx, row in enumerate(rows, 1):
            resp = client.get(GAMMA_URL, params={"id": row["id"]})
            resp.raise_for_status()
            batch = resp.json()
            raw = batch[0] if isinstance(batch, list) and batch else None
            if not raw:
                continue
            market = normalize_market(raw)
            if market:
                next_row = dict(row)
                next_row["resolved_yes"] = market["resolved_yes"]
                next_row["resolved_at"] = market["closed_time"]
                resolved.append(next_row)
            if idx % 50 == 0:
                print(f"checked {idx}/{len(rows)}, resolved={len(resolved)}")
    out = args.out or args.snapshot.with_name(args.snapshot.stem + "-resolved.jsonl")
    count = write_jsonl(out, resolved)
    print(f"wrote {count} resolved rows -> {out}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="Fetch and filter resolved Polymarket markets")
    fetch.add_argument("--out", type=Path)
    fetch.add_argument("--volume-min", type=float, default=10_000)
    fetch.add_argument("--days", type=int, default=120)
    fetch.add_argument("--limit", type=int, default=500)
    fetch.add_argument("--max-rows", type=int, default=3_000)
    fetch.add_argument("--target", type=int, default=500)
    fetch.add_argument("--group-cap", type=int, default=0, help="Cap markets retained per grouped event; 0 disables")
    fetch.set_defaults(func=cmd_fetch)

    prices = sub.add_parser("prices", help="Attach open/T-7d/T-24h market prices")
    prices.add_argument("markets", type=Path)
    prices.add_argument("--out", type=Path)
    prices.add_argument("--cache-dir", type=Path, default=PRICES)
    prices.add_argument("--sleep", type=float, default=0.0)
    prices.set_defaults(func=cmd_prices)

    jev = sub.add_parser("jev", help="Score markets with Jev")
    jev.add_argument("markets", type=Path)
    jev.add_argument("--out", type=Path)
    jev.add_argument("--limit", type=int)
    jev.set_defaults(func=cmd_jev)

    analyze = sub.add_parser("analyze", help="Analyze Jev and Polymarket calibration")
    analyze.add_argument("preds", type=Path)
    analyze.add_argument("--stamp")
    analyze.add_argument("--summary-out", type=Path)
    analyze.add_argument("--plot-out", type=Path)
    analyze.add_argument("--md-out", type=Path)
    analyze.set_defaults(func=cmd_analyze)

    open_snap = sub.add_parser("snapshot-open", help="Snapshot currently-open markets for a forward test")
    open_snap.add_argument("--out", type=Path)
    open_snap.add_argument("--volume-min", type=float, default=10_000)
    open_snap.add_argument("--limit", type=int, default=500)
    open_snap.add_argument("--max-rows", type=int, default=3_000)
    open_snap.add_argument("--target", type=int, default=500)
    open_snap.add_argument("--score", action="store_true")
    open_snap.set_defaults(func=cmd_snapshot_open)

    resolve = sub.add_parser("resolve", help="Resolve a prior open-market snapshot")
    resolve.add_argument("snapshot", type=Path)
    resolve.add_argument("--out", type=Path)
    resolve.set_defaults(func=cmd_resolve)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
