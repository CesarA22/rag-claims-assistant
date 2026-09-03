"""Named, parameterised queries over the read-only claims database.

The model never writes SQL. Six static strings, bound values, hard LIMITs.
No query joins policyholders. Paid-ness is a payments fact, never a Pago status literal.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from pydantic import field_validator, model_validator

from app.domain.errors import InsurCoError, InvalidRequest
from app.domain.models import Evidence, Product
from app.safety.redact import scan

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "claims.db"
CLAIM_AMOUNT_LABEL = (
    "valor reivindicado pelo segurado, antes da regulação"
)
COVERAGE_SQL = """
SELECT MAX(d) FROM (
    SELECT MAX(occurrence_date) AS d FROM claims
    UNION ALL
    SELECT MAX(payment_date) FROM payments
)
"""

ClaimNumber = Annotated[str, StringConstraints(pattern=r"^SIN-\d{4}-\d{6}$")]
PolicyNumber = Annotated[
    str, StringConstraints(pattern=r"^AP-(AUTO|RES|EMP)-\d{6}$")
]
ProductParam = Literal["Auto", "Residencial", "Empresarial"]
ClaimType = Literal[
    "Colisão",
    "Danos Elétricos",
    "Danos a Terceiros (RCF-DC)",
    "Danos a Terceiros (RCF-DM)",
    "Equipamentos Eletrônicos",
    "Incêndio",
    "Lucros Cessantes",
    "Responsabilidade Civil",
    "Roubo de bens",
    "Roubo e Furto (Auto)",
    "Roubo e Furto Qualificado",
    "Vendaval",
    "Vidros",
]
OpenStatus = Literal["Aberto", "Em regulação", "Negado"]
PaymentType = Literal["Integral", "Parcial"]

_PAGO_STATUSES = frozenset({"Pago", "Pago parcial"})
_PAGO_MESSAGE = (
    "status cannot express paid; use paid=true or "
    "payment_type='Integral'/'Parcial'"
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetClaimParams(StrictModel):
    claim_number: ClaimNumber


class GetClaimPaymentParams(StrictModel):
    claim_number: ClaimNumber


class ListClaimsByPolicyParams(StrictModel):
    policy_number: PolicyNumber
    limit: int = Field(default=20, ge=1, le=20)


class ClaimsSummaryParams(StrictModel):
    product: ProductParam | None = None
    claim_type: ClaimType | None = None
    payment_type: PaymentType | None = None
    date_from: date | None = None
    date_to: date | None = None


class GetPolicyParams(StrictModel):
    policy_number: PolicyNumber


class CountClaimsParams(StrictModel):
    product: ProductParam | None = None
    claim_type: ClaimType | None = None
    status: OpenStatus | None = None
    payment_type: PaymentType | None = None
    date_from: date | None = None
    date_to: date | None = None
    paid: bool | None = None

    @field_validator("status", mode="before")
    @classmethod
    def reject_paid_status(cls, value: object) -> object:
        if value in _PAGO_STATUSES:
            raise ValueError(_PAGO_MESSAGE)
        return value

    @model_validator(mode="after")
    def reject_unpaid_with_type(self) -> Self:
        if self.paid is False and self.payment_type is not None:
            raise ValueError(
                "paid=false contradicts payment_type; "
                "a payment type implies a payment"
            )
        return self


GET_CLAIM_SQL = """
SELECT
    cl.claim_number,
    cl.claim_type,
    cl.status,
    cl.occurrence_date,
    cl.notice_date,
    cl.registration_date,
    cl.claim_amount,
    p.product,
    p.policy_number
FROM claims cl
JOIN policies p ON p.policy_id = cl.policy_id
WHERE cl.claim_number = :claim_number
LIMIT 1
"""

GET_CLAIM_PAYMENT_SQL = """
SELECT
    cl.claim_number,
    cl.status,
    p.product,
    pay.paid_amount,
    pay.payment_date,
    pay.payment_type,
    pay.payment_status
FROM claims cl
JOIN policies p ON p.policy_id = cl.policy_id
LEFT JOIN payments pay ON pay.claim_id = cl.claim_id
WHERE cl.claim_number = :claim_number
LIMIT 1
"""

LIST_CLAIMS_BY_POLICY_SQL = """
SELECT
    cl.claim_number,
    cl.claim_type,
    cl.status,
    cl.occurrence_date,
    cl.claim_amount,
    pay.paid_amount,
    p.product
FROM claims cl
JOIN policies p ON p.policy_id = cl.policy_id
LEFT JOIN payments pay ON pay.claim_id = cl.claim_id
WHERE p.policy_number = :policy_number
ORDER BY cl.occurrence_date DESC
LIMIT :limit
"""

CLAIMS_SUMMARY_SQL = """
SELECT
    COUNT(*) AS paid_count,
    SUM(pay.paid_amount) AS paid_sum,
    AVG(pay.paid_amount) AS paid_avg,
    MIN(pay.paid_amount) AS paid_min,
    MAX(pay.paid_amount) AS paid_max
FROM claims cl
JOIN policies p ON p.policy_id = cl.policy_id
JOIN payments pay ON pay.claim_id = cl.claim_id
WHERE (:product IS NULL OR p.product = :product)
  AND (:claim_type IS NULL OR cl.claim_type = :claim_type)
  AND (:payment_type IS NULL OR pay.payment_type = :payment_type)
  AND (:date_from IS NULL OR cl.occurrence_date >= :date_from)
  AND (:date_to IS NULL OR cl.occurrence_date <= :date_to)
LIMIT 1
"""

GET_POLICY_SQL = """
SELECT
    p.policy_number,
    p.product,
    p.start_date,
    p.end_date,
    p.status,
    p.annual_premium
FROM policies p
WHERE p.policy_number = :policy_number
LIMIT 1
"""

COUNT_CLAIMS_SQL = """
SELECT
    COUNT(*) AS claim_count,
    SUM(
        CASE WHEN EXISTS (
            SELECT 1 FROM payments pay WHERE pay.claim_id = cl.claim_id
        ) THEN 1 ELSE 0 END
    ) AS paid_claim_count
FROM claims cl
JOIN policies p ON p.policy_id = cl.policy_id
WHERE (:product IS NULL OR p.product = :product)
  AND (:claim_type IS NULL OR cl.claim_type = :claim_type)
  AND (:status IS NULL OR cl.status = :status)
  AND (:date_from IS NULL OR cl.occurrence_date >= :date_from)
  AND (:date_to IS NULL OR cl.occurrence_date <= :date_to)
  AND (
        :paid IS NULL
        OR (:paid = 1) = EXISTS (
            SELECT 1 FROM payments pay WHERE pay.claim_id = cl.claim_id
        )
      )
  AND (
        :payment_type IS NULL
        OR EXISTS (
            SELECT 1 FROM payments pay
            WHERE pay.claim_id = cl.claim_id
              AND pay.payment_type = :payment_type
        )
      )
LIMIT 1
"""


def format_brl(value: float) -> str:
    sign = "-" if value < 0 else ""
    integer, frac = f"{abs(value):.2f}".split(".")
    groups: list[str] = []
    while integer:
        groups.append(integer[-3:])
        integer = integer[:-3]
    return f"{sign}R$ {'.'.join(reversed(groups))},{frac}"


def _claimed(amount: float) -> str:
    return (
        f"valor_reivindicado ({CLAIM_AMOUNT_LABEL}): {format_brl(amount)}"
    )


def _render_get_claim(
    rows: Sequence[sqlite3.Row], params: GetClaimParams
) -> tuple[str, Product]:
    if not rows:
        return f"sinistro não encontrado: {params.claim_number}", "All"
    row = rows[0]
    text = "\n".join(
        [
            f"claim_number: {row['claim_number']}",
            f"claim_type: {row['claim_type']}",
            f"status: {row['status']}",
            f"occurrence_date: {row['occurrence_date']}",
            f"notice_date: {row['notice_date']}",
            f"registration_date: {row['registration_date']}",
            _claimed(float(row["claim_amount"])),
            f"product: {row['product']}",
            f"policy_number: {row['policy_number']}",
            "valor pago: consultar "
            f'claims.get_claim_payment(claim_number="{params.claim_number}")',
        ]
    )
    return text, row["product"]


def _render_get_claim_payment(
    rows: Sequence[sqlite3.Row], params: GetClaimPaymentParams
) -> tuple[str, Product]:
    if not rows:
        return f"sinistro não encontrado: {params.claim_number}", "All"
    row = rows[0]
    status = row["status"]
    if row["paid_amount"] is None:
        text = "\n".join(
            [
                f"claim_number: {row['claim_number']}",
                f"status: {status}",
                f"product: {row['product']}",
                f'nenhum pagamento registrado — sinistro em status "{status}"',
            ]
        )
        return text, row["product"]
    text = "\n".join(
        [
            f"claim_number: {row['claim_number']}",
            f"status: {status}",
            f"product: {row['product']}",
            f"paid_amount: {format_brl(float(row['paid_amount']))}",
            f"payment_date: {row['payment_date']}",
            f"payment_type: {row['payment_type']}",
            f"payment_status: {row['payment_status']}",
        ]
    )
    return text, row["product"]


def _render_list_claims(
    rows: Sequence[sqlite3.Row], params: ListClaimsByPolicyParams
) -> tuple[str, Product]:
    if not rows:
        return f"nenhum sinistro nesta apólice: {params.policy_number}", "All"
    blocks: list[str] = []
    for row in rows:
        paid = row["paid_amount"]
        paid_line = (
            f"paid_amount: {format_brl(float(paid))}"
            if paid is not None
            else "paid_amount: nenhum pagamento"
        )
        blocks.append(
            "\n".join(
                [
                    f"claim_number: {row['claim_number']}",
                    f"claim_type: {row['claim_type']}",
                    f"status: {row['status']}",
                    f"occurrence_date: {row['occurrence_date']}",
                    _claimed(float(row["claim_amount"])),
                    paid_line,
                ]
            )
        )
    product: Product = rows[0]["product"]
    return "\n\n".join(blocks), product


def _render_summary(
    rows: Sequence[sqlite3.Row], params: ClaimsSummaryParams
) -> tuple[str, Product]:
    row = rows[0]
    count = int(row["paid_count"] or 0)
    lines = [f"paid_count: {count}"]
    if count:
        lines.extend(
            [
                f"paid_sum: {format_brl(float(row['paid_sum']))}",
                f"paid_avg: {format_brl(float(row['paid_avg']))}",
                f"paid_min: {format_brl(float(row['paid_min']))}",
                f"paid_max: {format_brl(float(row['paid_max']))}",
            ]
        )
    else:
        lines.append("nenhum pagamento nas condições filtradas")
    product: Product = params.product if params.product is not None else "All"
    return "\n".join(lines), product


def _render_policy(
    rows: Sequence[sqlite3.Row], params: GetPolicyParams
) -> tuple[str, Product]:
    if not rows:
        return f"apólice não encontrada: {params.policy_number}", "All"
    row = rows[0]
    text = "\n".join(
        [
            f"policy_number: {row['policy_number']}",
            f"product: {row['product']}",
            f"start_date: {row['start_date']}",
            f"end_date: {row['end_date']}",
            f"status: {row['status']}",
            f"annual_premium: {format_brl(float(row['annual_premium']))}",
        ]
    )
    return text, row["product"]


def _render_count(
    rows: Sequence[sqlite3.Row], params: CountClaimsParams
) -> tuple[str, Product]:
    row = rows[0]
    claim_count = int(row["claim_count"] or 0)
    paid_count = int(row["paid_claim_count"] or 0)
    text = "\n".join(
        [
            f"claim_count: {claim_count}",
            f"paid_claim_count: {paid_count}",
        ]
    )
    product: Product = params.product if params.product is not None else "All"
    return text, product


@dataclass(frozen=True)
class Query:
    sql: str
    params_model: type[BaseModel]
    render: Callable[[Sequence[sqlite3.Row], Any], tuple[str, Product]]


QUERIES: dict[str, Query] = {
    "get_claim": Query(GET_CLAIM_SQL, GetClaimParams, _render_get_claim),
    "get_claim_payment": Query(
        GET_CLAIM_PAYMENT_SQL, GetClaimPaymentParams, _render_get_claim_payment
    ),
    "list_claims_by_policy": Query(
        LIST_CLAIMS_BY_POLICY_SQL, ListClaimsByPolicyParams, _render_list_claims
    ),
    "claims_summary": Query(
        CLAIMS_SUMMARY_SQL, ClaimsSummaryParams, _render_summary
    ),
    "get_policy": Query(GET_POLICY_SQL, GetPolicyParams, _render_policy),
    "count_claims": Query(COUNT_CLAIMS_SQL, CountClaimsParams, _render_count),
}


def citation_call(query: str, params: BaseModel) -> str:
    parts: list[str] = []
    for key, value in params.model_dump(exclude_none=True).items():
        if isinstance(value, date):
            rendered = f'{key}="{value.isoformat()}"'
        elif isinstance(value, bool):
            rendered = f"{key}={str(value).lower()}"
        elif isinstance(value, str):
            rendered = f'{key}="{value}"'
        else:
            rendered = f"{key}={value}"
        parts.append(rendered)
    joined = ", ".join(parts)
    return f"claims.{query}({joined})"


def _sql_params(params: BaseModel) -> dict[str, Any]:
    bound: dict[str, Any] = {}
    for key, value in params.model_dump().items():
        if isinstance(value, date):
            bound[key] = value.isoformat()
        elif isinstance(value, bool):
            bound[key] = int(value)
        else:
            bound[key] = value
    return bound


def _format_validation(query: str, exc: ValidationError) -> str:
    bits: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ())) or "params"
        bits.append(f"{loc}: {err['msg']}")
    return f"invalid parameters for {query}: {'; '.join(bits)}"


def _sqlite_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


def _evidence_id(query: str, params: BaseModel) -> str:
    payload = {"query": query, **params.model_dump(mode="json", exclude_none=True)}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:8]
    return f"claims:{query}:{digest}"


class ClaimsTool:
    name = "claims"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "enum": list(QUERIES)},
        },
        "required": ["query"],
    }

    def __init__(self, path: Path | str | None = None) -> None:
        env = os.getenv("CLAIMS_DB_PATH")
        resolved = path if path is not None else env
        self.path = Path(resolved) if resolved else DEFAULT_DB
        self._coverage_date: date | None = None

    async def run(self, params: dict[str, Any]) -> Evidence:
        if "query" not in params:
            raise InvalidRequest("missing query name")
        payload = {key: value for key, value in params.items() if key != "query"}
        return await self.execute(str(params["query"]), **payload)

    async def execute(self, query: str, **params: Any) -> Evidence:
        spec = QUERIES.get(query)
        if spec is None:
            raise InvalidRequest(f"unknown query {query!r}")
        try:
            parsed = spec.params_model.model_validate(params)
        except ValidationError as exc:
            raise InvalidRequest(_format_validation(query, exc)) from None
        rows = await asyncio.to_thread(self._fetch, spec, _sql_params(parsed))
        text, product = spec.render(rows, parsed)
        if scan(text):
            raise InsurCoError("claims query result contained a PII pattern")
        coverage = self._coverage_date
        if coverage is None:
            raise InsurCoError("coverage date missing after a successful query")
        return Evidence(
            id=_evidence_id(query, parsed),
            document_code="claims.db",
            document_title="Banco de sinistros Indicium InsurCo",
            section=citation_call(query, parsed),
            version="snapshot",
            effective_date=coverage,
            product=product,
            text=text,
            source_kind="claims",
            doc_role="database",
            contains_pii=False,
        )

    def _fetch(
        self, spec: Query, sql_params: dict[str, Any]
    ) -> list[sqlite3.Row]:
        conn = sqlite3.connect(_sqlite_uri(self.path), uri=True)
        try:
            conn.row_factory = sqlite3.Row
            if self._coverage_date is None:
                raw = conn.execute(COVERAGE_SQL).fetchone()[0]
                self._coverage_date = date.fromisoformat(str(raw))
            return list(conn.execute(spec.sql, sql_params))
        finally:
            conn.close()
