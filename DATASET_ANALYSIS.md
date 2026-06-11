# Dataset Quality Analysis — `final_combined_dataset.jsonl`

**Purpose:** Fine-tuning local models (target: Qwen3.6-27B, hybrid reasoning model) on the stock-prediction / financial-analysis domain.
**Verdict:** Usable for SFT after cleaning, but **15% of the raw file was defective** and topical diversity is heavily skewed toward mega-cap tech. The cleaned set (2,536 examples, ~4.6M tokens) is enough for a LoRA/QLoRA domain adaptation of a 27B model, but is *not* enough — and not grounded enough — to teach genuine stock *prediction*. Details and recommendations below.

---

## 1. Raw dataset overview

| Metric | Value |
|---|---|
| Raw lines | 2,988 |
| Parseable records | 2,986 (2 malformed JSON lines) |
| Schema | `{"prompt": ..., "completion": ...}` only — consistent |
| Prompt length | 159–861 chars (avg 334, ~84 tokens) |
| Completion length | 546–14,307 chars (avg 7,274, ~1,800 tokens) |
| Estimated total tokens | ~5.7M raw → ~4.6M after cleaning |

## 2. Quality issues found

### Critical (fixed or removed by `prepare_dataset.py`)

1. **449 duplicate prompts (15% of the dataset).** 59 prompt texts appear 2–100 times. The worst: one portfolio-strategy prompt repeated **100×**, a TSLA reasoning-critique prompt **55×**, an NVDA metacognitive-review prompt **45×**. Most duplicates have *paraphrased but substantively identical* completions — this teaches the model to memorize one answer per prompt and badly skews the loss toward a few templates. **Action: deduplicated on exact prompt (kept first occurrence) → 448 removed.**
2. **13 records with generation-artifact leakage.** Completions ending in ` ```jsonl ` blocks or a following record's raw `{` — the synthetic-generation script bled adjacent JSON into the answer text. **Action: artifacts stripped (10 recoverable), unrecoverable/truncated remainder dropped.**
3. **2 unparseable JSON lines** — dropped.
4. **~5 truncated completions** (cut mid-sentence, 546–900 chars vs. 7K average) — dropped.

### Moderate (fixed)

5. **28 records with template placeholders** — literal `[Current Date]` in prompts/completions (including records 0–2, i.e. the very first lines of the file). A model trained on these will emit `[Current Date]` verbatim. **Action: replaced with a neutral phrase.**
6. **Double-escaped newlines** — some records store literal `\n` text instead of newlines, producing one-line walls of text. **Action: repaired.**

### Structural / diversity issues (NOT fixable by cleaning — see §4)

7. **Heavy ticker concentration.** NVDA/NVIDIA appears in **15–16% of all prompts** (455 records). Top-10 tickers ≈ 45% of the data. Tech sector ≈ 42% of prompts; finance ≈ 5%, energy ≈ 6%. Only ~640 unique ticker-like symbols, most appearing once or twice. The model will be noticeably better at analyzing NVDA than at a random mid-cap bank.
8. **Single response style.** 100% of completions are long markdown reports with headers/bold; 99% use bullets. There are **zero** short answers, conversational follow-ups, refusals, "insufficient data" responses, or multi-turn exchanges. The fine-tuned model will produce a 1,800-token report for every input, including "is AAPL a buy?".
9. **No temporal grounding.** Only 0.2% of records contain a real calendar date; prices/levels are synthetic-plausible, not historical fact. This is actually *safer* (less stale-fact memorization) but means the dataset teaches analysis *methodology*, not market knowledge — set expectations accordingly.
10. **Uniform completion length** — 97% of completions fall in 1K–3K tokens (none >5K, almost none <500). No length diversity signal.
11. **No reasoning traces.** Completions are polished final reports with no separated chain-of-thought. Relevant for the Qwen3.6 target — see §3.

### What is genuinely good

- Prompts are uniformly specific (100% contain a clear action verb + ticker/scenario; no vague one-liners).
- Completions are quantitatively dense: 99% contain percentages, 91% contain explicit price targets, 90% express confidence/probability, 52% give bull/base/bear scenarios.
- Broad *task-type* coverage: technical analysis, fundamental/DCF valuation, options strategies, portfolio construction, earnings previews, backtest critique, alternative data, metacognitive review of analyses.
- AI-disclaimer contamination is near zero (the few "as an AI" hits are legitimate phrases like "MSFT's position as an AI leader").

## 3. Fit for Qwen3.6-27B (reasoning model)

Qwen3.6-27B uses the ChatML (`<|im_start|>…<|im_end|>`) template with hybrid thinking mode (`<think>…</think>` blocks, toggleable via `enable_thinking` / `<|think_on|>` / `<|think_off|>`).

Key implication: **this dataset has no genuine reasoning traces**, so it must be trained in **non-thinking mode** (`enable_thinking=False` when applying the chat template — the template then inserts the empty `<think></think>` pair itself). Do **not** wrap answer text in fake `<think>` tags: training a reasoning model on fabricated thoughts degrades its native reasoning. Mixing ~10–20% general thinking-mode data (e.g. open reasoning sets) alongside this domain data is the standard way to preserve thinking ability during domain SFT.

Deliverables produced by `prepare_dataset.py`:

| File | Records | Format |
|---|---|---|
| `cleaned_dataset.jsonl` | 2,536 | original `prompt`/`completion`, cleaned |
| `qwen3.6_sft_train.jsonl` | 2,410 | `messages` (system/user/assistant), ChatML-ready |
| `qwen3.6_sft_val.jsonl` | 126 | same, held-out 5% |

Suggested training config (LoRA/QLoRA via Unsloth, LLaMA-Factory, or PEFT — all support the `messages` format):

- `max_seq_length: 4096` (longest example ≈ 3.7K tokens — nothing gets truncated)
- LoRA r=16–32, α=32, dropout 0.05, target all linear projections
- 2–3 epochs, lr 1e-4–2e-4 (LoRA), cosine schedule, effective batch ≈ 32
- Apply template with `enable_thinking=False`; mask loss to assistant tokens only
- ~4.6M training tokens × 3 epochs ≈ 14M tokens — appropriate scale for LoRA domain adaptation; do **not** full-fine-tune on a set this small (catastrophic forgetting risk)

Note: the original task mentioned 9B-class models — this dataset works for those too (same files, same config; a 27B will extract more from it, a 9B will need the diversity fixes below more urgently).

## 4. Recommended improvements (priority order)

1. **Rebalance tickers/sectors.** Cap any single ticker at ~3% of examples. Generate new examples for under-represented sectors: financials, healthcare, REITs, utilities, industrials, small/mid-caps, international ADRs.
2. **Add response-style diversity (~20–30% of the set):** short direct answers, "I'd need X data to answer that" responses, multi-turn dialogues (follow-up questions on a prior analysis), and explicit uncertainty/refusal cases ("predict tomorrow's exact price" → calibrated pushback). Without these, the model becomes a one-trick report generator.
3. **Add genuine reasoning traces** for a subset (500–1,000 examples) by distilling from a strong reasoning model (let it think, keep the `<think>` block), enabling thinking-mode SFT — this is the single highest-leverage upgrade for a Qwen3.6 target.
4. **Add negative/contrastive examples:** flawed analyses with critiques (some metacognitive-review examples exist — expand them), bad-trade post-mortems, and bear cases that turned out right.
5. **Ground a subset in real historical data** (point-in-time prices with dates, with known outcomes) if the goal is actual prediction calibration rather than analysis style.
6. **Scale:** 2.5K examples is the floor for noticeable domain adaptation via LoRA. 8–15K well-balanced examples is the realistic target for robust behavior on a 9B–27B model.

## 5. Bottom line

- **Quality after cleaning:** Good — consistent schema, specific prompts, quantitatively rich completions, artifacts removed.
- **Diversity:** Insufficient as-is. Task-type diversity is decent; ticker/sector/style/length diversity is poor (NVDA-heavy, single response register).
- **Enough to train a local 9B–27B model?** Yes for *style/methodology adaptation* via LoRA (the model will learn to produce structured, scenario-based financial analysis). No for *predictive capability* — there is no temporally-grounded outcome data, and no dataset of this kind makes a model able to predict stock prices.
