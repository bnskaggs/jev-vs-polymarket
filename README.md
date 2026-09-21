# Jev vs. Polymarket

Does a fast typed-decision model give honest probabilities on genuinely
uncertain questions?

This repo probes TypeSafe's Jev model on resolved Polymarket markets. The
input is deliberately narrow: each market's question and resolution rules
only. No news snippets, no search, no trading. Jev answers one yes/no
question:

> This market will resolve Yes.

The script then compares Jev's probability with the resolved outcome and with
Polymarket's own Yes price at three horizons: market open, 7 days before
resolution, and 24 hours before resolution.

## Why this exists

Most classification benchmarks are too easy to test calibration well: the
model is near-certain on almost everything. Prediction markets are different.
They are real, uncertain questions with ground truth and a strong baseline:
the price people actually traded.

The expectation is not that Jev beats the market. The useful result is the map:
where Jev is calibrated, where it is overconfident, and which categories of
questions break the confidence-gated routing loop.

## Setup

```powershell
cd jev-vs-polymarket
uv sync --extra dev

# The SDK reads this from the environment. Never write it to a file.
$env:TYPESAFE_API_KEY = [Environment]::GetEnvironmentVariable('TYPESAFE_API_KEY','User')
```

## Run

```powershell
# 1. Fetch recently-resolved binary Polymarket markets.
uv run jev-polymarket fetch --target 500 --days 120 --volume-min 10000

# 2. Attach Polymarket Yes-price snapshots.
uv run jev-polymarket prices data\markets-YYYYMMDDTHHMMSSZ.jsonl

# 3. Validate the Jev path before the full run.
uv run jev-polymarket jev data\markets-YYYYMMDDTHHMMSSZ-priced.jsonl --limit 10
uv run jev-polymarket analyze results\preds-YYYYMMDDTHHMMSSZ.jsonl

# 4. Full run.
uv run jev-polymarket jev data\markets-YYYYMMDDTHHMMSSZ-priced.jsonl
uv run jev-polymarket analyze results\preds-YYYYMMDDTHHMMSSZ.jsonl
```

The analysis writes:

- `results/<stamp>-summary.json`
- `results/reliability-<stamp>.png`
- `results/results.md`

## Optional forward test

Retrospective resolved markets may have appeared in model training data. For a
zero-leakage sample, snapshot open markets today and resolve them later:

```powershell
uv run jev-polymarket snapshot-open --target 500 --score
uv run jev-polymarket resolve data\open-snapshot-YYYYMMDDTHHMMSSZ.jsonl
```

## Metrics

- Brier score and Brier skill vs. the base-rate predictor.
- Expected calibration error (10 equal-width bins).
- AUC for discrimination.
- Reliability tables and plot for Jev and market snapshots.
- Jev-vs-market disagreement rows.
- Breakdowns by Jev-assigned category, numeric-threshold probability,
  grouped markets, and close month.
- Confidence-gated routing table: auto-act share vs. error at increasing
  distance from a coin flip.

## Scope

- v0 is question-only: market question + resolution rules.
- No news, LLM baseline, Kalshi, Metaculus, or trading.
- Market prices are a strong but imperfect baseline; grouped markets and thin
  markets can be weird.
- Older resolved markets may be in model training data. The close-month split
  is included to inspect that risk; the forward-test command is the clean fix.
