from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts import research_risk_budget as rb

FRONTIERS = {
    "direct30": ("contrib_profit25", 0.30, "월급 즉시 편입 + 부스터 확정이익 30% 복리"),
    "direct35": ("contrib_profit25", 0.35, "월급 즉시 편입 + 부스터 확정이익 35% 복리"),
    "direct40": ("contrib_profit25", 0.40, "월급 즉시 편입 + 부스터 확정이익 40% 복리"),
    "direct45": ("contrib_profit25", 0.45, "월급 즉시 편입 + 부스터 확정이익 45% 복리"),
    "step5_profit25": ("step5", 0.25, "$5k 고정증액 + 부스터 확정이익 25% 복리"),
    "step5_profit35": ("step5", 0.35, "$5k 고정증액 + 부스터 확정이익 35% 복리"),
}


def run(frontier: str, output: str) -> None:
    base_variant, fraction, label = FRONTIERS[frontier]
    original = rb.profit_reinvest_fraction
    rb.profit_reinvest_fraction = lambda _variant: fraction
    try:
        rb.run(base_variant, output)
    finally:
        rb.profit_reinvest_fraction = original

    path = Path(output)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["research"]["variant"] = frontier
    payload["research"]["label"] = label
    payload["research"]["profit_reinvest_fraction"] = fraction
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def summarize(path: str) -> None:
    rb.summarize(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontier", choices=FRONTIERS)
    parser.add_argument("--output")
    parser.add_argument("--summarize")
    args = parser.parse_args()
    if args.summarize:
        summarize(args.summarize)
    else:
        run(args.frontier, args.output)
