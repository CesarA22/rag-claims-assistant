"""Deterministic gates between a model draft and the answer that ships.

Three judgments the pipeline makes in code, never in the prompt, because
`00-architecture.mdc` requires citation validation and PII handling to be
deterministic:

- **PII** — the question asks for personal data and the retrieved evidence
  carries it. Refuse. Legitimate retrieval, illegitimate answer.
- **Ambiguity** — the evidence spans more than one product and the question named
  none. Ask, do not pick. gs-009 fails a correct, well-cited single number.
- **Sufficiency** — the answer's own vocabulary must appear in the evidence it
  cites. gs-008's trap is five confident on-topic chunks that do not answer, so
  "retrieval returned nothing" is not the check.

`judge()` runs ambiguity *before* sufficiency, which means sufficiency is
unreachable for any question whose evidence spans more than one product. That is
a real gap, it is measured, and both obvious repairs were rejected on evidence
rather than on taste — see `judge()`'s docstring and DECISIONS.md. It is written
here too because this module is where someone looking for the sufficiency gate
will arrive, and the gate not running is the thing they need to know.

The sufficiency test is lexical, and its limits are stated in DECISIONS.md rather
than implied: it catches an answer that introduces a subject the cited text never
mentions, which is the gs-008 failure. It does not catch a fluent paraphrase that
reuses the source's words to say something the source does not. That needs an
entailment judgment, and a second model call is not affordable inside the
US$0.05 ceiling.
"""

from __future__ import annotations

import re
import unicodedata

from app.domain.models import Evidence

# Coverage below this refuses. Not 1.0: a grounded answer legitimately reaches
# for a connective the source did not use, and T-01's own answer would fail an
# all-or-nothing rule.
SUFFICIENCY_MIN = 0.8

_MIN_TERM_LEN = 4

# Portuguese function words that clear the length filter but carry no claim.
_STOPWORDS = frozenset(
    """
    para como que com por uma dos das nos nas pelo pela pelos pelas este esta
    esse essa isso aquele aquela sobre quando onde qual quais existe quanto
    quantos quantas deve devem pode podem esta estao estar ser sao tem teem seu
    sua seus suas nao sim mais menos muito todo toda todos todas entre desde ate
    apos antes cada outro outra outros outras mesmo mesma caso conforme segundo
    """.split()
)

_PII_REQUEST = re.compile(
    r"\b(nomes?\s+completos?|nomes?\b|cpf|telefones?|e-?mails?|"
    r"dados\s+pessoais|segurados?\s+citados?|identifica\w*)\b"
)

_PRODUCTS = ("Auto", "Residencial", "Empresarial")


def normalize(text: str) -> str:
    """Lowercase, strip accents, collapse to alphanumerics and spaces."""
    folded = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", stripped).strip()


def content_terms(text: str) -> set[str]:
    return {
        token
        for token in normalize(text).split()
        if len(token) >= _MIN_TERM_LEN and token not in _STOPWORDS and not token.isdigit()
    }


def requests_personal_data(question: str) -> bool:
    return _PII_REQUEST.search(normalize(question)) is not None


def evidence_carries_pii(evidence: list[Evidence]) -> bool:
    return any(item.contains_pii or item.doc_role == "minutes" for item in evidence)


def is_pii_request(question: str, evidence: list[Evidence]) -> bool:
    """gs-010: the minutes are legitimately retrieved; the answer is still refused."""
    return requests_personal_data(question) and evidence_carries_pii(evidence)


def spanned_products(evidence: list[Evidence]) -> set[str]:
    """Products the evidence actually discriminates between. 'All' is not one."""
    return {item.product for item in evidence if item.product != "All"}


def names_a_product(question: str) -> bool:
    haystack = normalize(question)
    return any(
        re.search(rf"\b{normalize(product)}\b", haystack) for product in _PRODUCTS
    )


def is_ambiguous(question: str, evidence: list[Evidence]) -> bool:
    """gs-009: two products in evidence, none named in the question → ask."""
    return len(spanned_products(evidence)) > 1 and not names_a_product(question)


def grounded_ratio(answer_text: str, cited: list[Evidence]) -> float:
    """Share of the answer's content terms that appear in the text it cites."""
    terms = content_terms(answer_text)
    if not terms:
        return 1.0
    supported = set()
    for item in cited:
        supported |= content_terms(item.text)
        supported |= content_terms(item.section)
    return len(terms & supported) / len(terms)


def is_supported(answer_text: str, cited: list[Evidence]) -> bool:
    return grounded_ratio(answer_text, cited) >= SUFFICIENCY_MIN
