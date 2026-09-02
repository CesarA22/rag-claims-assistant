"""
Tier-0 retrieval evaluation. No LLM, no cost, no variance.

Run this on day 1, before writing a single prompt. If the expected document
does not come back in the top k, no amount of prompt engineering will fix the
answer -- and you will have spent money discovering that the slow way.

    python evals/retrieval_baseline.py --k 5
    python evals/retrieval_baseline.py --k 5 --probes    # unseen-style questions

Baseline result on the provided kit (BM25 only, naive heading-split chunks):
    recall@5 = 9/9 = 100% on the golden set
    ...and it collapses on paraphrased questions. See --probes. That gap is
    the entire argument for adding vectors, and it is measurable rather than
    asserted.
"""
from __future__ import annotations
import argparse, json, math, os, re, sys
from collections import Counter
from dataclasses import dataclass

CORPUS_DIR = os.environ.get("CORPUS_TXT", "corpus_txt")
GOLDEN = os.environ.get("GOLDEN_SET", "evals/golden_set_enriched.json")

DOC_META = re.compile(
    r"Código do documento:\s*(?P<code>[A-Z0-9\-]+)\s+"
    r"Versão:\s*(?P<version>\S+)\s+"
    r"Vigência a partir de:\s*(?P<effective>\d{4}-\d{2}-\d{2})")

HEADING = re.compile(r"^\d+(\.\d+)*\s+[A-ZÀ-Ú]")

PRODUCT_BY_PREFIX = {"CG-AUTO": "Auto", "CG-RES": "Residencial", "CG-EMP": "Empresarial"}


@dataclass
class Chunk:
    code: str
    version: str
    effective: str
    product: str
    heading: str
    text: str


def strip_boilerplate(raw: str) -> list[str]:
    """A line appearing on every page is header/footer, not content (~15% here)."""
    pages = max(raw.count("===== PAGE"), 1)
    lines = [l.strip() for l in raw.splitlines()]
    freq = Counter(l for l in lines if l)
    return [l for l in lines
            if l and not l.startswith("=====") and not (pages > 1 and freq[l] >= pages)]


def load_chunks(corpus_dir: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    for fn in sorted(os.listdir(corpus_dir)):
        if not fn.endswith(".txt"):
            continue
        raw = open(os.path.join(corpus_dir, fn), encoding="utf-8").read()
        m = DOC_META.search(raw)
        if not m:
            print(f"  !! no metadata header in {fn}", file=sys.stderr)
            continue
        code, version, effective = m["code"], m["version"], m["effective"]
        product = next((p for pre, p in PRODUCT_BY_PREFIX.items() if code.startswith(pre)), "All")

        heading, buf = "preâmbulo", []
        for line in strip_boilerplate(raw):
            if HEADING.match(line):
                if buf:
                    chunks.append(Chunk(code, version, effective, product, heading, " ".join(buf)))
                heading, buf = line, []
            else:
                buf.append(line)
        if buf:
            chunks.append(Chunk(code, version, effective, product, heading, " ".join(buf)))
    return chunks


TOKEN = re.compile(r"[a-zà-ú0-9]{3,}")


class BM25:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks, self.k1, self.b = chunks, k1, b
        self.docs = [TOKEN.findall((c.heading + " " + c.text).lower()) for c in chunks]
        self.N = len(self.docs)
        self.df = Counter()
        for d in self.docs:
            self.df.update(set(d))
        self.avgdl = sum(map(len, self.docs)) / max(self.N, 1)

    def search(self, query: str, k: int = 5) -> list[tuple[float, Chunk]]:
        q = TOKEN.findall(query.lower())
        scored = []
        for i, d in enumerate(self.docs):
            tf, s = Counter(d), 0.0
            for w in q:
                if w not in tf:
                    continue
                idf = math.log((self.N - self.df[w] + 0.5) / (self.df[w] + 0.5) + 1)
                denom = tf[w] + self.k1 * (1 - self.b + self.b * len(d) / self.avgdl)
                s += idf * (tf[w] * (self.k1 + 1)) / denom
            scored.append((s, i))
        scored.sort(reverse=True)
        return [(s, self.chunks[i]) for s, i in scored[:k]]


PROBES = [
    ("version trap",  "Em quantos dias o segurado precisa avisar a seguradora sobre um sinistro?"),
    ("paraphrase",    "Se meu carro for levado por bandidos, quanto tempo a seguradora tem para resolver?"),
    ("paraphrase",    "Meu computador queimou por causa de uma tempestade com relâmpagos. Pago franquia?"),
    ("colloquial",    "quanto a seguradora banca se eu bater no carro de outra pessoa"),
    ("unseen-likely", "Qual o teto de indenização para incêndio em imóvel comercial?"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--gate", type=float, default=None, help="fail if recall below this")
    ap.add_argument("--probes", action="store_true", help="run unseen-style probes instead")
    args = ap.parse_args()

    chunks = load_chunks(CORPUS_DIR)
    index = BM25(chunks)
    print(f"{len(chunks)} chunks from {len({c.code for c in chunks})} documents\n")

    if args.probes:
        for label, q in PROBES:
            print(f"[{label}] {q}")
            for s, c in index.search(q, 4):
                print(f"   {s:5.1f}  {c.code} v{c.version} ({c.effective})  {c.heading[:56]}")
            print()
        return 0

    cases = json.load(open(GOLDEN, encoding="utf-8"))
    hits = scored = 0
    for case in cases:
        expected = case.get("docs", [])
        got = [c.code for _, c in index.search(case["pergunta"], args.k)]
        if not expected:
            print(f'{case["id"]}  n/a   (refusal case)  top{args.k}={got}')
            continue
        scored += 1
        ok = all(e in got for e in expected)
        hits += ok
        print(f'{case["id"]}  {"OK  " if ok else "MISS"}  expected={expected}  top{args.k}={got}')

    recall = hits / scored if scored else 0.0
    print(f"\nrecall@{args.k} = {hits}/{scored} = {recall:.0%}")

    if args.gate is not None and recall < args.gate:
        print(f"FAIL: below gate {args.gate:.0%}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
