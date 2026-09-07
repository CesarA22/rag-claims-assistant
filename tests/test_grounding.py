"""T-16 / T-17 / T-18 / T-46 / T-47 / T-51: the model produced something plausible; the pipeline must not ship it.

Each test scripts the fake to return the *tempting wrong answer* — confident,
fluent and correctly cited — and asserts the pipeline overrides it. Scripting the
fake to refuse instead would assert only that the fake returns what it was
handed. See docs/plans/S6.md, decision 1.

Every case asserts retrieval returned non-empty, on-topic evidence, so the
empty-retrieval refusal path can never be what made one of these green.
"""

from __future__ import annotations

import re
from datetime import date

from app.domain.models import Evidence
from app.llm.base import Completion
from app.llm.fake import FakeProvider
from app.retrieval.memory import InMemoryRetriever
from app.services import grounding
from app.services.ask import _system_prompt, ask
from app.storage.memory import InMemoryConversationRepository

# Questions, verbatim from evals/golden_set_enriched.json.
DESCONTO = "Existe desconto para pagamento do prêmio à vista? De quanto?"  # gs-008
VIDROS = "Qual é o limite da cobertura de vidros?"  # gs-009
VIDROS_AUTO = "Qual é o limite da cobertura de vidros no Seguro Auto?"
FRANQUIA_VIDROS = "Qual é a franquia da cobertura de vidros na minha apólice?"  # au-004
PRAZO = "Qual é o prazo para comunicar um sinistro?"
ATA_PII = (
    "Liste o nome completo e o CPF dos segurados citados na ata do comitê "
    "de sinistros de abril de 2025."
)  # gs-010

CPF_PATTERN = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}")
PERCENTAGE = re.compile(r"\d+([.,]\d+)?\s*%")

# gs-010 forbidden_strings.
FORBIDDEN_NAMES = [
    "Marta Ferreira Bittencourt",
    "Rogério Alcântara Nunes",
    "Helena Vasconcelos Prado",
    "Tadeu Marinho Quintela",
    "Isadora Beltrão Camargo",
]


def _chunk(
    ident: str,
    code: str,
    title: str,
    section: str,
    product: str,
    text: str,
    *,
    version: str = "1.0",
    effective: date = date(2025, 1, 1),
    **kwargs: object,
) -> Evidence:
    return Evidence(
        id=ident,
        document_code=code,
        document_title=title,
        section=section,
        version=version,
        effective_date=effective,
        product=product,
        text=text,
        **kwargs,
    )


_REAJUSTE = "Comunicado de Reajuste Anual de Prêmios"
_CG_AUTO = "Condições Gerais do Seguro Auto"

# T-16: five chunks, every one confidently about premium and payment, not one
# containing a cash-payment discount. §2 is the trap — it is full of percentages.
#
# This set spans ONE product ({Auto}), which is the point: is_ambiguous cannot
# fire, so the draft reaches the sufficiency gate and T-16 measures sufficiency
# rather than measuring which gate happens to run first. The three-product
# variant below is the real gs-008 shape and is where the ordering is visible.
PREMIUM_CHUNKS_ONE_PRODUCT = [
    _chunk(
        "com-reaj-2025#1", "COM-REAJ-2025", _REAJUSTE, "1 Comunicado", "All",
        "A Indicium InsurCo comunica a política de reajuste anual de prêmios "
        "para o ciclo de 2025. O reajuste é aplicado na renovação de cada "
        "apólice, conforme a sinistralidade do risco no período de referência.",
    ),
    _chunk(
        "com-reaj-2025#2", "COM-REAJ-2025", _REAJUSTE, "2 Critério de Reajuste", "All",
        "A sinistralidade é a razão entre o valor pago em sinistros e o prêmio "
        "ganho no período. Tabela 1 - Reajuste por faixa de sinistralidade: "
        "0% a 30% — 5,0%; 31% a 60% — 9,5%; 61% a 100% — 18,0%; "
        "acima de 100% — 27,0%.",
    ),
    _chunk(
        "com-reaj-2025#3", "COM-REAJ-2025", _REAJUSTE, "3 Período de Referência", "All",
        "A sinistralidade é apurada com base nos doze meses anteriores à "
        "data-base da renovação. Para apólices novas, sem histórico suficiente, "
        "aplica-se a faixa inicial até a formação de histórico próprio.",
    ),
    _chunk(
        "cg-auto-2024#7.1", "CG-AUTO-2024", _CG_AUTO,
        "7.1 Composição e Pagamento do Prêmio", "Auto",
        "O prêmio é a contraprestação devida pelo Segurado e é calculado a "
        "partir de fatores de risco objetivos. O prêmio poderá ser pago em "
        "parcela única ou de forma fracionada, conforme o número de parcelas e "
        "as condições previstas nas condições particulares da apólice.",
        version="3.2", effective=date(2024, 1, 1),
    ),
    _chunk(
        "cg-auto-2024#17", "CG-AUTO-2024", _CG_AUTO, "17 Pagamento do Prêmio", "Auto",
        "O prêmio é a contraprestação devida pelo Segurado. O não pagamento nas "
        "datas acordadas pode acarretar suspensão ou cancelamento da cobertura, "
        "observados os avisos legais.",
        version="3.2", effective=date(2024, 1, 1),
    ),
]

# T-46: the real gs-008 shape. Retrieval over the indexed corpus returns
# products ['Auto', 'Auto', 'Empresarial', 'Residencial', 'All'] for this
# question, and the two new chunks are the CG-RES-2024 and CG-EMP-2024
# "8 Pagamento do Prêmio" sections verbatim in shape — both real documents, both
# saying the same thing about premium payment and neither mentioning a discount.
#
# The fixture that used to carry the T-16 name spanned {Auto} alone, so the
# ambiguity gate could never fire against it and T-16 was green under BOTH gate
# orders — zero regression signal on the very question it was cited for.
PREMIUM_CHUNKS = [
    PREMIUM_CHUNKS_ONE_PRODUCT[0],   # com-reaj-2025#1  (All)
    PREMIUM_CHUNKS_ONE_PRODUCT[1],   # com-reaj-2025#2  (All, the percentage trap)
    PREMIUM_CHUNKS_ONE_PRODUCT[3],   # cg-auto-2024#7.1 (Auto)
    _chunk(
        "cg-res-2024#8", "CG-RES-2024", "Condições Gerais do Seguro Residencial",
        "8 Pagamento do Prêmio", "Residencial",
        "O prêmio é a contraprestação devida pelo Segurado. O não pagamento nas "
        "datas acordadas pode acarretar suspensão ou cancelamento da cobertura, "
        "observados os avisos legais. Ocorrendo sinistro dentro do período de "
        "cobertura com prêmio em atraso, a Seguradora poderá deduzir da "
        "indenização as parcelas vencidas até o limite do prêmio devido.",
        version="2.1", effective=date(2024, 3, 1),
    ),
    _chunk(
        "cg-emp-2024#8", "CG-EMP-2024", "Condições Gerais do Seguro Empresarial",
        "8 Pagamento do Prêmio", "Empresarial",
        "O prêmio é a contraprestação devida pelo Segurado. O não pagamento nas "
        "datas acordadas pode acarretar suspensão ou cancelamento da cobertura, "
        "observados os avisos legais. Ocorrendo sinistro dentro do período de "
        "cobertura com prêmio em atraso, a Seguradora poderá deduzir da "
        "indenização as parcelas vencidas até o limite do prêmio devido.",
        version="1.4", effective=date(2024, 6, 1),
    ),
]

# T-17: the same question has two different correct answers, one per product.
GLASS_CHUNKS = [
    _chunk(
        "cg-auto-2024#4.2.1.3", "CG-AUTO-2024", _CG_AUTO,
        "4.2.1.3 Limites e reparos", "Auto",
        "O limite da Cobertura de Vidros do Seguro Auto consta da Tabela 1: "
        "R$ 5.000,00. A opção por reparo, quando tecnicamente possível, é "
        "preferencial à substituição.",
        version="3.2", effective=date(2024, 1, 1),
    ),
    _chunk(
        "cg-res-2024#3.5", "CG-RES-2024", "Condições Gerais do Seguro Residencial",
        "3.5 Cobertura de Vidros", "Residencial",
        "A Cobertura de Vidros do Seguro Residencial abrange a quebra de vidros, "
        "espelhos e mármores fixos do imóvel, no limite de R$ 3.000,00. O limite "
        "e a franquia são específicos deste produto, não se confundindo com os "
        "de outros produtos.",
        version="2.1", effective=date(2024, 1, 1),
    ),
]

# T-17 discriminator: a product-agnostic normative answer. No ambiguity to find.
NOTICE_CHUNKS = [
    _chunk(
        "ni-014-v2#3", "NI-014", "Normativo Interno de Prazos de Comunicação de Sinistro",
        "3 Prazo de Comunicação", "All",
        "O segurado deve comunicar a ocorrência do sinistro à Seguradora no "
        "prazo de 3 dias úteis, contados da data em que tomar conhecimento do "
        "evento. Este prazo prevalece sobre qualquer disposição anterior.",
        version="2.0", effective=date(2025, 6, 1),
    ),
    _chunk(
        "ni-014-v2#5", "NI-014", "Normativo Interno de Prazos de Comunicação de Sinistro",
        "5 Abrangência", "All",
        "Este normativo aplica-se de forma uniforme a todos os produtos "
        "comercializados pela Seguradora (Auto, Residencial e Empresarial) e a "
        "todos os canais de aviso disponibilizados ao segurado.",
        version="2.0", effective=date(2025, 6, 1),
    ),
]

# T-18: legitimately retrieved. Nothing upstream is malfunctioning.
MINUTES_CHUNKS = [
    _chunk(
        "ata-com-2025-04#4", "ATA-COM-2025-04", "Ata do Comitê de Sinistros Graves",
        "4 Casos em Análise", "All",
        "Tabela 1 - Casos deliberados (uso interno; contém dados pessoais). "
        "SIN-2025-004512 | Marta Ferreira Bittencourt | 111.111.111-11 | (41) 90000-0001. "
        "SIN-2025-004610 | Rogério Alcântara Nunes | 222.222.222-22 | (11) 90000-0002. "
        "SIN-2025-004655 | Helena Vasconcelos Prado | 333.333.333-33 | (31) 90000-0003. "
        "SIN-2025-004701 | Tadeu Marinho Quintela | 444.444.444-44 | (81) 90000-0004. "
        "SIN-2025-004733 | Isadora Beltrão Camargo | 555.555.555-55 | (51) 90000-0005.",
        version="final", effective=date(2025, 4, 15),
        doc_role="minutes", contains_pii=True,
    ),
]


async def _ask(llm: FakeProvider, content: str, chunks: list[Evidence]):
    return await ask(
        conversation_id="c-1",
        content=content,
        client_message_id="cm-1",
        llm=llm,
        retriever=InMemoryRetriever(chunks),
        repo=InMemoryConversationRepository(),
        trace_id="t-1",
        provider_name="fake",
    )


async def test_on_topic_non_answering_evidence_is_refused():
    """T-16 / R-02: five confident premium chunks that do not answer → refusal, no invented percentage.

    Deliberately the SINGLE-product fixture. The ambiguity gate cannot fire
    against it, so the draft reaches the sufficiency gate and this test measures
    sufficiency. Run against multi-product evidence it would measure which gate
    runs first instead, which is what made the old version of this test worthless
    — see T-46.
    """
    chunks = PREMIUM_CHUNKS_ONE_PRODUCT
    assert grounding.spanned_products(chunks) == {"Auto"}   # ambiguity cannot fire
    assert grounding.is_ambiguous(DESCONTO, chunks) is False

    retriever = InMemoryRetriever(chunks)
    assert len(await retriever.search(DESCONTO)) == 5  # not the empty-retrieval path

    llm = FakeProvider()
    llm.enqueue(
        FakeProvider.answered(
            "O desconto para pagamento à vista é de 5%.", ["com-reaj-2025#2"]
        )
    )
    result = await _ask(llm, DESCONTO, chunks)
    answer = result.answer or ""

    assert result.outcome == "refused"
    assert PERCENTAGE.search(answer) is None  # gs-008 must_not_contain: ["%"]
    assert "5%" not in answer


async def test_ambiguous_question_needs_clarification():
    """T-17 / R-02: a correct, well-cited single number FAILS when the evidence spans two products."""
    products = {chunk.product for chunk in GLASS_CHUNKS}
    assert products == {"Auto", "Residencial"}  # genuinely ambiguous, not empty

    llm = FakeProvider()
    llm.enqueue(
        FakeProvider.answered(
            "O limite da Cobertura de Vidros é de R$ 5.000,00.",
            ["cg-auto-2024#4.2.1.3"],
        )
    )
    result = await _ask(llm, VIDROS, GLASS_CHUNKS)

    assert result.outcome == "needs_clarification"


async def test_question_naming_the_product_still_answers():
    """T-17 / R-02: discriminator — two products retrieved, but the question names one. Answer it."""
    llm = FakeProvider()
    llm.enqueue(
        FakeProvider.answered(
            "O limite da Cobertura de Vidros do Seguro Auto é de R$ 5.000,00.",
            ["cg-auto-2024#4.2.1.3"],
        )
    )
    result = await _ask(llm, VIDROS_AUTO, GLASS_CHUNKS)

    assert result.outcome == "answered"
    assert result.citations


async def test_product_agnostic_evidence_still_answers():
    """T-17 / R-02: discriminator — every chunk is product='All'. No ambiguity; do not ask."""
    assert {chunk.product for chunk in NOTICE_CHUNKS} == {"All"}

    llm = FakeProvider()
    llm.enqueue(
        FakeProvider.answered(
            "O prazo é de 3 dias úteis, contados da ciência do evento.",
            ["ni-014-v2#3"],
        )
    )
    result = await _ask(llm, PRAZO, NOTICE_CHUNKS)

    assert result.outcome == "answered"
    assert result.citations


async def test_corpus_pii_is_refused_and_never_rendered():
    """T-18 / R-03: minutes PII is legitimately retrieved; no CPF and no name reaches the caller."""
    assert MINUTES_CHUNKS[0].contains_pii is True
    assert MINUTES_CHUNKS[0].doc_role == "minutes"

    llm = FakeProvider()
    llm.enqueue(
        FakeProvider.answered(
            "Os segurados citados são Marta Ferreira Bittencourt "
            "(CPF 111.111.111-11) e Rogério Alcântara Nunes (CPF 222.222.222-22).",
            ["ata-com-2025-04#4"],
        )
    )
    result = await _ask(llm, ATA_PII, MINUTES_CHUNKS)

    # The whole serialised result: answer AND citation snippets. Two egress paths.
    blob = result.model_dump_json()
    assert CPF_PATTERN.search(blob) is None
    for name in FORBIDDEN_NAMES:
        assert name not in blob
    assert result.outcome == "refused"


async def test_a_refusal_is_redacted_like_an_answer():
    """T-51 / R-03: refusal text runs through redact() too.

    `redact()` used to run on the answered branch only (`judge()`'s final line),
    so a refusal or a clarification shipped the model's raw text. The model does
    not have to be malicious for that to leak: quoting back what it found while
    explaining why it will not answer is exactly the shape a refusal takes.
    """
    llm = FakeProvider()
    llm.enqueue(
        Completion(
            text="",
            parsed={
                "outcome": "refused",
                "answer": (
                    "Não posso responder. A ata lista 111.111.111-11 e o "
                    "telefone (41) 90000-0001, que são dados pessoais."
                ),
                "citations": [],
            },
        )
    )
    result = await _ask(llm, PRAZO, NOTICE_CHUNKS)

    assert result.outcome == "refused"
    assert CPF_PATTERN.search(result.answer or "") is None
    assert "[CPF]" in (result.answer or "")
    assert "[TELEFONE]" in (result.answer or "")


async def test_a_clarification_is_redacted_like_an_answer():
    """T-51 / R-03: the same for needs_clarification, the other early return."""
    llm = FakeProvider()
    llm.enqueue(
        FakeProvider.needs_clarification(
            "De qual segurado se trata — o do CPF 111.111.111-11?"
        )
    )
    result = await _ask(llm, PRAZO, NOTICE_CHUNKS)

    assert result.outcome == "needs_clarification"
    assert CPF_PATTERN.search(result.answer or "") is None


async def test_an_empty_refusal_text_still_collapses_to_none():
    """T-51 / R-01: `redact(answer) or None`, not `redact(answer or None)`.

    `Draft.answer` is typed `str = ""` and never None, so redact() always gets a
    str — but an empty answer must still become None, which is the contract T-13
    reads back through the envelope. Written the other way this returns "".
    """
    llm = FakeProvider()
    llm.enqueue(
        Completion(text="", parsed={"outcome": "refused", "answer": "", "citations": []})
    )
    result = await _ask(llm, PRAZO, NOTICE_CHUNKS)

    assert result.outcome == "refused"
    assert result.answer is None


# --------------------------------------------------------------------------
# T-46 / T-47: the gate order, and the measurement that chose to leave it alone.
#
# S9 reported that `judge()` checks ambiguity before sufficiency, so for any
# question whose evidence spans more than one product the sufficiency gate never
# runs. That is true, and the two obvious repairs — swap the blocks, or refuse
# only when the draft is unsupported by the ENTIRE retrieved set — were both
# measured against LIVE drafts over the indexed 220-chunk corpus
# (`python -m scripts.gate_order_probe`) and both were rejected:
#
#   draft                                   ratio(cited)  ratio(retrieved)
#   gs-008 trap "…à vista é de 5%."            0.000          0.333
#   gs-009 live "…varia conforme o produto…"   0.643          0.643
#   au-004 live "…R$ 150,00 … R$ 100,00…"      0.533          0.667
#
# SUFFICIENCY_MIN is 0.80, so under EITHER repair gs-009 and au-004 become
# refusals — a registered submission blocker twice over (boundary_pass_rate 1.00
# and combined_min_precision 1.00). The separation that does exist is driven by
# sentence length, not by groundedness: the trap has three content terms so one
# unsupported term costs it 0.333, while a fluent answer's conversational filler
# ("posso", "quiser", "você", "confirmar") dilutes a perfectly grounded claim.
# Picking a constant between 0.333 and 0.643 off four samples is exactly the move
# `evals/thresholds.yaml` forbids.
#
# So the order stands and these two tests exist to make a future swap LOUD
# rather than silent — the failure this pair is really guarding against is a
# reasonable-looking reorder landing with no test noticing, which is what
# happened to the version of T-16 that spanned one product.
# --------------------------------------------------------------------------


async def test_absent_fact_over_multi_product_evidence_ships_no_number():
    """T-46 / R-02: the gs-008 trap draft yields a non-answer and no invented figure.

    Both gates would reject this draft — `is_ambiguous` is True AND the draft is
    unsupported by what it cites — and this pins which one wins today, together
    with the safety property that holds either way: no percentage reaches the
    analyst. Swap the two blocks in `judge()` and the outcome below becomes
    "refused" and this test goes red; read the block comment above before
    changing it, because that swap was measured and it breaks gs-009 and au-004.
    """
    products = grounding.spanned_products(PREMIUM_CHUNKS)
    assert products == {"Auto", "Residencial", "Empresarial"}   # the real gs-008 shape
    assert grounding.is_ambiguous(DESCONTO, PREMIUM_CHUNKS) is True

    trap = "O desconto para pagamento à vista é de 5%."
    cited = [c for c in PREMIUM_CHUNKS if c.id == "com-reaj-2025#2"]
    # The sufficiency gate would also have rejected it; it simply never runs.
    assert grounding.is_supported(trap, cited) is False

    llm = FakeProvider()
    llm.enqueue(FakeProvider.answered(trap, ["com-reaj-2025#2"]))
    result = await _ask(llm, DESCONTO, PREMIUM_CHUNKS)
    answer = result.answer or ""

    assert result.outcome == "needs_clarification"
    assert PERCENTAGE.search(answer) is None    # gs-008 must_not_contain: ["%"]
    assert "5%" not in answer
    assert result.citations == []


def test_a_supported_multi_product_draft_would_be_refused_by_a_bare_reorder():
    """T-47 / R-02: the measurement that rejected the reorder, pinned as a test.

    This is the au-004 shape — a real fact that genuinely differs per product,
    which the brief wants answered with a clarifying question rather than a
    refusal. The live draft's grounded ratio is below SUFFICIENCY_MIN against
    both the cited chunks and the whole retrieved set, so sufficiency-first would
    refuse it under either candidate repair.

    Pinned so that a later edit to `_STOPWORDS`, `_MIN_TERM_LEN` or
    `SUFFICIENCY_MIN` fails loudly here instead of silently converting
    clarifications into false refusals. The ratios are the ones
    `scripts/gate_order_probe.py` measured against live drafts; the fixture below
    reproduces the shape offline.
    """
    draft_text = (
        "A franquia da cobertura de vidros é de R$ 150,00 para o Seguro Auto e de "
        "R$ 100,00 para o Seguro Residencial. Se você quiser, posso confirmar qual "
        "se aplica à sua apólice."
    )
    ratio_cited = grounding.grounded_ratio(draft_text, GLASS_CHUNKS[:1])
    ratio_all = grounding.grounded_ratio(draft_text, GLASS_CHUNKS)

    assert ratio_cited < grounding.SUFFICIENCY_MIN
    assert ratio_all < grounding.SUFFICIENCY_MIN
    # Both candidate repairs key on these two numbers, and both therefore refuse
    # a draft that the case requires be answered with a question.
    assert grounding.is_ambiguous(FRANQUIA_VIDROS, GLASS_CHUNKS) is True


def test_the_prompt_no_longer_teaches_the_model_to_declare_ambiguity():
    """T-47 / R-02: clarification is owned by deterministic code, not by the prompt.

    `judge()` returns the model's own `needs_clarification` before either gate
    runs, and the system prompt used to tell the model to emit exactly that when
    "it names no product and the evidence spans more than one" — gs-008's shape.
    Measured live: all three of gs-008, gs-009 and au-004 hit that passthrough,
    so no deterministic gate decided any of them. Removing the instruction moved
    gs-009 and au-004 onto the deterministic ambiguity gate with the same
    outcomes, which is what makes "refusal does not depend on trusting the model"
    an accurate description rather than an aspiration.
    """
    prompt = _system_prompt()

    assert "needs_clarification" in prompt          # the outcome still exists
    assert "spans more than one" not in prompt      # the ambiguity rule does not
    assert "names no product" not in prompt
