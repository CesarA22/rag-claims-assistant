import re
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from app.domain.errors import InvalidRequest
from app.safety.redact import scan
from app.tools.claims import ClaimsTool, QUERIES

SHOWCASE = "SIN-2025-004512"
UNPAID = "SIN-2023-005055"
MISSING_DB = Path("/no/such/claims.db")
REAL_DB = Path(__file__).resolve().parents[1] / "data" / "claims.db"


def _claim_count(text: str) -> int:
    match = re.search(r"^claim_count: (\d+)$", text, re.M)
    assert match, text
    return int(match.group(1))


async def test_unknown_query_and_bad_claim_number_never_open_the_db():
    """T-20 / R-10: unknown query name and malformed claim_number are rejected before any DB access."""
    tool = ClaimsTool(MISSING_DB)
    with pytest.raises(InvalidRequest, match="unknown query"):
        await tool.execute("drop_table")
    with pytest.raises(InvalidRequest, match="claim_number"):
        await tool.execute("get_claim", claim_number="not-a-claim")


async def test_no_query_touches_policyholders():
    """T-21 / R-10: no SQL string names policyholders; PII columns are absent as belt-and-braces."""
    sql = "\n".join(query.sql for query in QUERIES.values())
    assert "policyholders" not in sql.lower()
    forbidden = re.compile(
        r"\b(name|cpf|phone|email|city|state|birth_date)\b", re.I
    )
    assert forbidden.search(sql) is None, sql


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
_STATUS_PAGO = re.compile(r"status\s*=\s*['\"]Pago")


async def test_pago_status_is_unreachable():
    """T-22 / R-10: no SQL contains the literal 'Pago'; count_claims(status='Pago') is InvalidRequest."""
    for name, query in QUERIES.items():
        assert "'Pago'" not in query.sql, name
        assert '"Pago"' not in query.sql, name
    offenders = [
        str(path.relative_to(APP_ROOT.parent))
        for path in APP_ROOT.rglob("*.py")
        if _STATUS_PAGO.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
    tool = ClaimsTool(MISSING_DB)
    with pytest.raises(InvalidRequest, match="paid"):
        await tool.execute("count_claims", status="Pago")
    with pytest.raises(InvalidRequest, match="paid"):
        await tool.execute("count_claims", status="Pago parcial")


async def test_incendio_paid_is_713_not_614():
    """T-23 / R-10: count_claims(claim_type='Incêndio', paid=True) is 713, not 614."""
    evidence = await ClaimsTool().execute(
        "count_claims", claim_type="Incêndio", paid=True
    )
    assert _claim_count(evidence.text) == 713
    assert "614" not in evidence.text


async def test_payment_type_parcial_preserves_the_capability():
    """T-24 / R-10: count_claims(payment_type='Parcial') is 772, the old Pago parcial count."""
    evidence = await ClaimsTool().execute(
        "count_claims", payment_type="Parcial"
    )
    assert _claim_count(evidence.text) == 772


async def test_claim_amount_is_labelled_and_unpaid_carries_status():
    """T-25 / R-10: get_claim labels the claimed amount; get_claim_payment is the paid figure; unpaid renders status."""
    claimed = await ClaimsTool().execute("get_claim", claim_number=SHOWCASE)
    assert "valor reivindicado pelo segurado, antes da regulação" in claimed.text
    assert "R$ 120.000,00" in claimed.text
    assert "100.000" not in claimed.text
    assert "paid_amount" not in claimed.text

    paid = await ClaimsTool().execute(
        "get_claim_payment", claim_number=SHOWCASE
    )
    assert "R$ 100.000,00" in paid.text
    assert "Integral" in paid.text

    unpaid = await ClaimsTool().execute("get_claim_payment", claim_number=UNPAID)
    assert "nenhum pagamento registrado" in unpaid.text
    assert "Em regulação" in unpaid.text
    assert "paid_amount" not in unpaid.text


async def test_showcase_claim_does_not_leak_policyholder_pii():
    """T-04 / R-03: a claims row whose policyholder has a CPF never returns that identifier."""
    conn = sqlite3.connect(REAL_DB.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        cpf, name = conn.execute(
            """
            SELECT ph.cpf, ph.name
            FROM claims cl
            JOIN policies p ON p.policy_id = cl.policy_id
            JOIN policyholders ph ON ph.policyholder_id = p.policyholder_id
            WHERE cl.claim_number = ?
            """,
            (SHOWCASE,),
        ).fetchone()
    finally:
        conn.close()
    assert cpf
    assert name

    claimed = await ClaimsTool().execute("get_claim", claim_number=SHOWCASE)
    paid = await ClaimsTool().execute(
        "get_claim_payment", claim_number=SHOWCASE
    )
    for evidence in (claimed, paid):
        assert scan(evidence.text) == set()
        assert cpf not in evidence.text
        assert name not in evidence.text


async def test_coverage_date_and_citation_section_are_the_d05_inputs():
    """T-26 / R-10: DB provenance line reads section + effective_date; coverage is 2026-02-11, not file mtime."""
    evidence = await ClaimsTool().execute(
        "get_claim_payment", claim_number=SHOWCASE
    )
    assert (
        evidence.section
        == 'claims.get_claim_payment(claim_number="SIN-2025-004512")'
    )
    assert evidence.effective_date == date(2026, 2, 11)
    assert evidence.version == "snapshot"
    assert evidence.source_kind == "claims"
