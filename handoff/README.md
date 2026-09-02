# InsurCo handoff bundle

    cursor-rules/                 → unzip into .cursor/rules/  (6 layered rule files)
    corpus_txt/                   → the 13 PDFs pre-extracted to text, for grepping
    evals/golden_set_candidate.json   → the original, untouched
    evals/golden_set_enriched.json    → + expected doc codes, grading method, traps
    evals/retrieval_baseline.py       → tier-0 retrieval eval, no LLM, no cost

## Run the baseline now

    cd handoff
    python evals/retrieval_baseline.py --k 5 --gate 0.9
    python evals/retrieval_baseline.py --probes

Expected on this kit: recall@5 = 9/9 = 100% with BM25 alone, and total failure
on the paraphrase probes. That gap is the argument for adding vectors, and it
is measured rather than asserted.

Wire the first command into CI as your tier-0 gate. Point CORPUS_TXT at your
own ingest output once you have one, and the same script measures your real
pipeline instead of the naive baseline.
