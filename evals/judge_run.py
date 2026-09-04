"""Drive the judge over the four `grade: judge` cases in a completed run.

Separate from run_golden on purpose. The system's answers are produced once; the
judge is a second, independent pass over them, with its own provider, so its
spend never lands in a QuestionBudget and its failures never trip the product's
breaker. Re-judging costs a judge call and no system calls.

Needs credits — the judge is a model. If it cannot reach the provider it writes
nothing and says so, rather than emitting a verdict it did not earn.

Usage:
  python -m evals.judge_run --results evals/results
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from evals import judge

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evals" / "golden_set_enriched.json"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Grade the judge cases.")
    parser.add_argument("--results", type=Path, default=ROOT / "evals" / "results")
    args = parser.parse_args()

    cases = {
        c["id"]: c
        for c in json.loads(GOLDEN.read_text(encoding="utf-8"))
        if c["grade"] == "judge"
    }
    runs = sorted(args.results.glob("run-*.json"))
    if not runs:
        print("no run-*.json found; run evals.run_golden first")
        return 1

    from app.llm.openai_provider import OpenAIProvider
    from app.storage.db import connect

    provider = OpenAIProvider()
    judged: dict[str, Any] = {}

    async with connect() as pool:
        for path in runs:
            payload = json.loads(path.read_text(encoding="utf-8"))
            run = payload["run"]
            for record in payload["records"]:
                if record["case_id"] not in cases:
                    continue
                key = f"{record['case_id']}#r{run}"
                try:
                    verdict = await judge.judge_case(
                        provider, pool, cases[record["case_id"]], record
                    )
                except Exception as exc:  # noqa: BLE001 — report, never invent a verdict
                    print(f"  {key}  JUDGE UNAVAILABLE: {type(exc).__name__}")
                    print("  no verdicts written; EVALS.md will keep saying 'not-judged'")
                    return 2
                judged[key] = verdict
                print(f"  {key}  {verdict['verdict']}  {verdict['reason'][:80]}")

    out = args.results / "judged.json"
    out.write_text(json.dumps(judged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {out} ({len(judged)} verdicts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
