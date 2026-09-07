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

A third mechanism would fabricate an entire live run, and it is the most
dangerous thing in this harness:

  REPLAY       both ids above used to be stable ACROSS invocations, and
               idempotency is persisted in Postgres. Re-running the live tier
               against a database that already holds a keyless run returns the
               committed FAKE answers, stamps them `provider="openai"` — the
               envelope reports the current process's provider, not the stored
               one — and `report.py` renders thirty replayed fake turns as a live
               run and prints PASS on US$0.0000. Zero spend, zero network, a
               result that looks exactly like the real thing.

               Every invocation therefore mints a fresh `--tag` (printed, and
               pinnable for a deliberate resume) which participates in BOTH ids.
               Belt and braces: a record whose provider is `openai`, which is not
               degraded, and which billed zero prompt tokens is flagged
               `suspected_replay`, and `report.py` refuses to call such a run live.

Usage:
  # prove the harness with no spend — it SHOULD report failures.
  # LLM_PROVIDER is explicit because load_dotenv() now reads .env, and .env on a
  # working machine says openai; without it this command bills real money while
  # calling itself the keyless proof.
  LLM_PROVIDER=fake python -m evals.run_golden --in-process --runs 1       --out evals/results/keyless

  # the live run
  LLM_PROVIDER=openai RETRIEVER=hybrid       python -m evals.run_golden --base-url http://localhost:8000 --runs 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

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
    client: httpx.AsyncClient, case: dict[str, Any], run: int, tag: str
) -> dict[str, Any]:
    """One question, its own conversation, its own idempotency key.

    `tag` is what makes the ids fresh per invocation. Without it a second run
    against the same database replays the first one's stored answers under the
    current process's provider name.
    """
    conversation = f"eval-{tag}-r{run}-{case['id']}"
    started = time.perf_counter()
    response = await client.post(
        f"/conversations/{conversation}/messages",
        json={
            "content": case["pergunta"],
            "client_message_id": f"{case['id']}-r{run}-{tag}",
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


def suspected_replay(envelope: dict[str, Any]) -> bool:
    """A live provider that billed nothing and was not degraded is a replayed turn.

    `_from_turn` stamps the CURRENT process's provider onto a replayed turn, so
    `meta.provider` alone cannot tell a live answer from a committed fake one
    read back out of Postgres — which is how a re-run could render thirty
    replayed fake turns as a live run and print PASS on US$0.0000. Tokens can
    tell them apart: a real call bills input tokens, and the degraded path is the
    only legitimate way to reach a completed turn with none.
    """
    meta = envelope.get("meta") or {}
    if meta.get("provider") != "openai" or meta.get("degraded"):
        return False
    usage = meta.get("usage") or {}
    return usage.get("prompt_tokens", 0) == 0


async def run_once(
    client: httpx.AsyncClient, cases: list[dict[str, Any]], run: int, tag: str
) -> list[dict[str, Any]]:
    retrieved = await retrieved_ids_for([c["pergunta"] for c in cases])
    records: list[dict[str, Any]] = []
    for case in cases:
        result = await ask_one(client, case, run, tag)
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
                "suspected_replay": suspected_replay(envelope),
            }
        )
        print(
            f"  {case['id']}  {envelope.get('outcome', result['status_code']):>20}  "
            f"{result['elapsed_s']:>6.2f}s  "
            f"{'ok' if assertions.passed(checks) else 'MISS'}"
            f"{'  ** SUSPECTED REPLAY **' if records[-1]['suspected_replay'] else ''}"
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
    parser.add_argument(
        "--tag",
        default=None,
        help=(
            "Nonce mixed into every conversation id and idempotency key. Fresh "
            "per invocation by default — pin it only to deliberately resume a "
            "run, which replays whatever that tag already stored."
        ),
    )
    args = parser.parse_args()
    load_dotenv()

    tag = args.tag or uuid.uuid4().hex[:8]
    retriever = os.getenv("RETRIEVER", "memory")
    arm = os.getenv("RETRIEVER_ARM", "lexical")
    provider = os.getenv("LLM_PROVIDER", "fake")
    print(f"tag={tag}  runs={args.runs}  out={args.out}")
    print(f"LLM_PROVIDER={provider}  (this is what will be billed)")
    print(f"eval-process retrieval: RETRIEVER={retriever} RETRIEVER_ARM={arm}")
    if args.tag:
        print(
            "WARNING: --tag was pinned. Any case this tag already answered is "
            "replayed from storage at zero cost and stamped with the current "
            "provider name."
        )
    if retriever != "hybrid" and not args.in_process:
        # Citation validity is scored against what THIS process retrieves, and
        # the default here is the two-chunk InMemoryRetriever. Against a real API
        # that fails 30 of 30, for a reason nothing used to print.
        print(
            "WARNING: RETRIEVER is not 'hybrid' in the eval process, so citation "
            "validity is scored against the two-chunk in-memory fixture rather "
            "than the corpus the API actually searched."
        )
    if arm != "lexical":
        # EmbeddingCache bypasses ResilientProvider, so a query embedding is a
        # real charge that never enters QuestionBudget and never reaches
        # meta.cost_usd. The container also indexes with --no-embed, which makes
        # the vector CTE degenerate to lexical while still paying for it.
        print(
            "WARNING: a non-lexical arm pays an embedding call per question that "
            "never enters QuestionBudget, so the published cost per question "
            "omits a real charge."
        )

    cases = load_cases()
    args.out.mkdir(parents=True, exist_ok=True)

    for run in range(1, args.runs + 1):
        print(f"\nrun {run}/{args.runs}")
        client = await _client(args.base_url, args.in_process)
        try:
            records = await run_once(client, cases, run, tag)
        finally:
            await client.aclose()
        target = args.out / f"run-{run}.json"
        target.write_text(
            json.dumps(
                {"run": run, "tag": tag, "records": records}, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        print(f"  -> {target}")
        replays = [r["case_id"] for r in records if r["suspected_replay"]]
        if replays:
            print(
                f"  ** {len(replays)} record(s) look replayed, not live: "
                f"{', '.join(replays)}. Do not report this run. **"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
