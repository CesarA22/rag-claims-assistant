"""Tier 2 — drive the ten golden cases against the API, N times.

Two mechanisms in the product would silently corrupt a naive three-run loop, and
both were built on purpose:

  IDEMPOTENCY  reusing a client_message_id returns the stored turn without
               calling the provider, so runs 2 and 3 would replay run 1 at zero
               cost and zero latency. Every case gets a fresh id per run.
  HISTORY      recent_messages() feeds prior turns of the same conversation into
               the prompt, so ten cases in one conversation means gs-010 is
               answered with gs-001..gs-009 in context. One conversation per case
               per run: eval-r{run}-{case_id}.

Neither is a bug. Both would have made the numbers wrong.

Usage:
  # prove the harness with no spend — it SHOULD report failures
  python -m evals.run_golden --in-process --runs 1 --out evals/results/fake

  # the live run
  python -m evals.run_golden --base-url http://localhost:8000 --runs 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from evals import assertions

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evals" / "golden_set_enriched.json"
DEFAULT_OUT = ROOT / "evals" / "results"

# gs-010's forbidden names are the PII egress list applied to EVERY response,
# not only that case's — a leak on gs-003 is still a leak.
FORBIDDEN_NAMES = [
    "Marta Ferreira Bittencourt",
    "Rogério Alcântara Nunes",
    "Helena Vasconcelos Prado",
    "Tadeu Marinho Quintela",
    "Isadora Beltrão Camargo",
]


def load_cases() -> list[dict[str, Any]]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


async def _client(base_url: str | None, in_process: bool) -> httpx.AsyncClient:
    if in_process:
        # Same stack, no socket. Used to prove the harness before spending.
        from app.main import create_app

        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()),
            base_url="http://eval",
            timeout=60.0,
        )
    return httpx.AsyncClient(base_url=base_url or "http://localhost:8000", timeout=60.0)


async def ask_one(
    client: httpx.AsyncClient, case: dict[str, Any], run: int
) -> dict[str, Any]:
    """One question, its own conversation, its own idempotency key."""
    conversation = f"eval-r{run}-{case['id']}"
    started = time.perf_counter()
    response = await client.post(
        f"/conversations/{conversation}/messages",
        json={
            "content": case["pergunta"],
            "client_message_id": f"{case['id']}-r{run}",
        },
    )
    elapsed_s = time.perf_counter() - started
    try:
        body = response.json()
    except ValueError:
        body = {"_unparseable": response.text[:500]}
    return {
        "status_code": response.status_code,
        "body": body,
        "elapsed_s": round(elapsed_s, 3),
        "conversation_id": conversation,
    }


async def retrieved_ids_for(questions: list[str]) -> dict[str, set[str]]:
    """What retrieval returned per question, so citation validity checks truth.

    Uses the same retriever the app is configured with — the in-memory one on the
    keyless proof, HybridRetriever over Postgres live. One pool for all ten
    questions rather than one per question.

    `build_retriever(arm)` returns `(pool, retriever)` in that order, and it
    takes the ARM (lexical|vector|hybrid), not RETRIEVER (memory|hybrid).
    """
    if os.getenv("RETRIEVER", "memory") != "hybrid":
        from app.retrieval.memory import InMemoryRetriever

        retriever = InMemoryRetriever()
        return {q: {e.id for e in await retriever.search(q)} for q in questions}

    from app.main import build_retriever

    pool, retriever = await build_retriever(os.getenv("RETRIEVER_ARM", "lexical"))
    try:
        return {q: {e.id for e in await retriever.search(q)} for q in questions}
    finally:
        if pool is not None:
            await pool.close()


async def run_once(
    client: httpx.AsyncClient, cases: list[dict[str, Any]], run: int
) -> list[dict[str, Any]]:
    retrieved = await retrieved_ids_for([c["pergunta"] for c in cases])
    records: list[dict[str, Any]] = []
    for case in cases:
        result = await ask_one(client, case, run)
        envelope = result["body"]
        checks: list[assertions.Check] = []

        # Hard gates run on EVERY case, deterministic or judge.
        checks.append(assertions.error_leakage(envelope))
        checks.append(assertions.pii_egress(envelope, FORBIDDEN_NAMES))
        checks.append(
            assertions.citation_validity(envelope, retrieved.get(case["pergunta"], set()))
        )

        if case["grade"] == "deterministic":
            checks.extend(assertions.check_case(case, envelope))

        records.append(
            {
                "case_id": case["id"],
                "grade": case["grade"],
                "run": run,
                "question": case["pergunta"],
                "status_code": result["status_code"],
                "outcome": envelope.get("outcome"),
                "answer": envelope.get("answer"),
                "citations": envelope.get("citations", []),
                "meta": envelope.get("meta", {}),
                "elapsed_s": result["elapsed_s"],
                "checks": [c.as_dict() for c in checks],
                "deterministic_pass": (
                    assertions.passed(checks) if case["grade"] == "deterministic" else None
                ),
                "hard_gates_pass": assertions.passed(checks[:3]),
            }
        )
        print(
            f"  {case['id']}  {envelope.get('outcome', result['status_code']):>20}  "
            f"{result['elapsed_s']:>6.2f}s  "
            f"{'ok' if assertions.passed(checks) else 'MISS'}"
        )
    return records


async def main() -> int:
    parser = argparse.ArgumentParser(description="Tier 2 golden-set runner.")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--base-url", default=None)
    parser.add_argument(
        "--in-process",
        action="store_true",
        help="Drive the ASGI app directly. Proves the harness without spending.",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    cases = load_cases()
    args.out.mkdir(parents=True, exist_ok=True)

    for run in range(1, args.runs + 1):
        print(f"\nrun {run}/{args.runs}")
        client = await _client(args.base_url, args.in_process)
        try:
            records = await run_once(client, cases, run)
        finally:
            await client.aclose()
        target = args.out / f"run-{run}.json"
        target.write_text(
            json.dumps({"run": run, "records": records}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  -> {target}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
