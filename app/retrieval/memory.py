from datetime import date

from app.domain.models import Evidence

FIXED_CHUNKS: list[Evidence] = [
    Evidence(
        id="cg-auto-2024#2.1",
        document_code="CG-AUTO-2024",
        document_title="Condições Gerais do Seguro Auto",
        section="2.1 Vigência",
        version="3.2",
        effective_date=date(2024, 1, 1),
        product="Auto",
        text=(
            "A vigência padrão de uma apólice de Seguro Auto é de 12 meses, "
            "contados a partir das 24 horas da data de início informada na apólice "
            "até as 24 horas da data de término. A renovação não é automática e "
            "depende de manifestação expressa das partes."
        ),
        superseded=False,
    ),
    Evidence(
        id="ni-014-v2#3",
        document_code="NI-014",
        document_title="Normativo Interno de Prazos de Comunicação de Sinistro",
        section="3 Prazo de Comunicação",
        version="2.0",
        effective_date=date(2025, 6, 1),
        product="All",
        text=(
            "O segurado deve comunicar a ocorrência do sinistro à Seguradora no "
            "prazo de 3 dias úteis, contados da data em que tomar conhecimento "
            "do evento. Este prazo prevalece sobre qualquer disposição anterior "
            "em contrário, inclusive sobre o prazo previsto na versão 1.0 deste "
            "normativo."
        ),
        superseded=False,
    ),
]


class InMemoryRetriever:
    """Two fixed corpus chunks. Replaced by hybrid.py."""

    def __init__(self, chunks: list[Evidence] | None = None) -> None:
        self.chunks = list(chunks) if chunks is not None else list(FIXED_CHUNKS)

    async def search(
        self,
        query: str,
        *,
        product: str | None = None,
        k: int = 5,
    ) -> list[Evidence]:
        results = self.chunks
        if product is not None:
            results = [c for c in results if c.product in (product, "All")]
        return results[:k]
