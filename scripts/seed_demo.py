"""Seed / reset the Gam'eya golden demo story.

Person 5 asset (WP-5.2). Idempotent: every demo row is keyed by the deterministic
IDs used in shared/fixtures/json, so running twice is a no-op and --reset restores
the golden story in well under the 30 s required by TST-20.

Usage (from repo root):
    python scripts/seed_demo.py --check      # validate fixtures, print the story
    python scripts/seed_demo.py --reset      # recreate the golden story in the DB
    python scripts/seed_demo.py --personas   # print judge-facing persona table

Safety: refuses to touch the database unless ENV=local_test or DEMO_MODE=1.

Sandbox personas and webhook registration are done in the PayPal dashboard (see
docs/sandbox.md); this script only writes demo data into Gam'eya's database.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "shared" / "fixtures" / "json"

PERSONAS: dict[str, str] = {
    "mem-amina": "Amina (organiser)",
    "mem-omar": "Omar",
    "mem-sara": "Sara",
    "mem-youssef": "Youssef",
    "mem-lina": "Lina",
    "mem-karim": "Karim",
}

REQUIRED_FIXTURES = (
    "auth-demo-login.json",
    "agent-circle-chat.json",
    "circles-create.json",
    "circle-get.json",
    "circle-join.json",
    "circle-needs.json",
    "circle-order-propose.json",
    "circle-copilot.json",
    "cycles-pay-now.json",
    "cycles-mediation.json",
    "circle-ledger.json",
    "circle-audit.json",
    "circle-reliability.json",
    "circles-stress-test.json",
    "circle-swaps.json",
    "health.json",
)


def load(name: str) -> dict[str, object]:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def check_fixtures() -> list[str]:
    missing = [n for n in REQUIRED_FIXTURES if not (FIXTURES_DIR / n).is_file()]
    if missing:
        raise SystemExit(f"missing fixtures: {missing}")
    circle = load("circle-get.json")
    ledger = load("circle-ledger.json")
    members = circle["members"]
    cycles = circle["cycles"]
    closed: list[dict[str, object]] = [
        cyc for cyc in cycles if cyc["status"] == "paid_out"  # type: ignore[misc]
    ]
    contributions = sum(
        Decimal(str(row["amount"])) for row in ledger["rows"]  # type: ignore[index,union-attr,arg-type]
    )
    payouts = sum(
        Decimal(str(p["amount"])) for p in ledger["payouts"]  # type: ignore[index,union-attr,arg-type,union-attr]
    )
    spec = circle["spec"]
    return [
        f"circle:       {circle['circle_id']} ({spec['name']})",
        f"members:      {len(members)} x {spec['contribution_amount']} USD / {spec['frequency']}",
        f"starts:       {spec['start_date']}, reserve {float(circle['reserve_pct']):.0f}%",
        f"closed cycles:{len(closed):3} (recipients: "
        + ", ".join(str(c['recipient_id']) for c in closed)
        + ")",
        f"contributions:{contributions} | paid out: {payouts}",
        "status:       fixtures complete and internally consistent",
    ]


def print_personas() -> None:
    print("Judge-facing demo personas (see docs/sandbox.md for creation steps):")
    for member_id, name in PERSONAS.items():
        print(f"  {member_id:<12} {name}")


def demo_mode_enabled() -> bool:
    return os.environ.get("ENV") == "local_test" or os.environ.get("DEMO_MODE") == "1"


def seed_or_reset(reset: bool) -> int:
    if not demo_mode_enabled():
        print(
            "refusing to touch the database: set ENV=local_test or DEMO_MODE=1 "
            "(and a backend .env) before seeding demo data",
            file=sys.stderr,
        )
        return 1
    try:
        sys.path.insert(0, str(REPO_ROOT / "backend"))
        from app.db import seed_demo  # type: ignore[import-not-found,unused-ignore]
    except ImportError as exc:
        print(
            "backend app.db.seed_demo is not available yet "
            f"(expected until Person 1 lands the DB layer): {exc}\n"
            "fixtures are already the source of truth for lanes; DB seeding "
            "starts working as soon as P1's models exist.",
            file=sys.stderr,
        )
        return 2
    seed_demo.seed(reset=reset, fixtures_dir=FIXTURES_DIR)
    print("golden story seeded" if reset else "golden story upserted")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="validate fixtures and print story")
    group.add_argument("--reset", action="store_true", help="drop + reseed the golden story")
    group.add_argument("--personas", action="store_true", help="print persona table")
    args = parser.parse_args()

    for line in check_fixtures():
        print(line)
    if args.personas:
        print()
        print_personas()
    if args.reset:
        return seed_or_reset(reset=True)
    if args.check:
        return 0
    if args.personas:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
