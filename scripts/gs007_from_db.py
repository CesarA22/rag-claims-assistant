"""Answer gs-007 from the claims database alone.

Prints what the database can establish, then runs the Vidros counter-example
so the corpus/database boundary is a measured contradiction, not a comment.
"""

from __future__ import annotations

import asyncio
import re
import sys

from app.tools.claims import ClaimsTool, format_brl

SHOWCASE = "SIN-2025-004512"
RCF_DM = "Danos a Terceiros (RCF-DM)"
CG_AUTO_VIDROS = 5_000.00


def _max_paid(text: str) -> str:
    match = re.search(r"^paid_max: (.+)$", text, re.M)
    return match.group(1) if match else text


def _line(evidence, extra: str = "") -> str:
    coverage = evidence.effective_date.isoformat()
    suffix = f" {extra}" if extra else ""
    return f"{evidence.section} · dados até {coverage}{suffix}"


async def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    tool = ClaimsTool()
    question = (
        "No sinistro SIN-2025-004512, o valor pago respeitou o limite da "
        "cobertura de Danos Materiais a Terceiros do Seguro Auto?"
    )
    print(question)
    print()

    claimed = await tool.execute("get_claim", claim_number=SHOWCASE)
    print("1. get_claim")
    print(claimed.text)
    print()

    paid = await tool.execute("get_claim_payment", claim_number=SHOWCASE)
    print("2. get_claim_payment")
    print(paid.text)
    print()

    rcfdm = await tool.execute(
        "claims_summary", product="Auto", claim_type=RCF_DM
    )
    print("3. claims_summary(product='Auto', claim_type=RCF-DM)")
    print(rcfdm.text)
    print()

    print("4. citation strings (database lines only)")
    print(_line(claimed))
    print(_line(paid))
    print()

    vidros = await tool.execute(
        "claims_summary", product="Auto", claim_type="Vidros"
    )
    print("5. the counter-example, executed")
    print(
        "CG-AUTO-2024 Tabela 1, Cobertura de Vidros ..... limite  "
        f"{format_brl(CG_AUTO_VIDROS)}"
    )
    print(
        'claims.claims_summary(product="Auto", claim_type="Vidros") '
        f"→ max {_max_paid(vidros.text)}"
    )
    print(vidros.text)
    print()

    print("6. what the database cannot answer")
    print(
        "The database establishes claimed R$ 120.000,00 and paid R$ 100.000,00 "
        "on SIN-2025-004512, and that no Auto RCF-DM payment exceeds "
        f"{_max_paid(rcfdm.text)}. Inferring the *limit* from that maximum "
        "is a corpus fact (CG-AUTO-2024, Tabela de Coberturas). Step 5 is "
        "why: Auto Vidros pays above the stated R$ 5.000,00 ceiling, so "
        "agreement on RCF-DM is a coincidence of the synthetic data. "
        "The honest database-only answer to gs-007 is "
        "'paid R$ 100.000,00 of R$ 120.000,00 claimed'; "
        "the word respeitou requires the corpus half."
    )


if __name__ == "__main__":
    asyncio.run(main())
