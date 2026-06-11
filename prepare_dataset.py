#!/usr/bin/env python3
"""Clean final_combined_dataset.jsonl and convert it to Qwen3.6-27B SFT format.

Pipeline:
  1. Parse JSONL, drop malformed lines.
  2. Repair double-escaped newlines (literal "\\n" stored as text).
  3. Strip JSONL artifact leakage (raw ```jsonl blocks / trailing JSON objects
     that bled into completions during dataset generation).
  4. Replace [Current Date]-style placeholders with a neutral reference.
  5. Drop truncated/too-short completions (< 800 chars after repair).
  6. Deduplicate on exact prompt text (keep first occurrence).
  7. Emit:
       - cleaned_dataset.jsonl        (prompt/completion, cleaned)
       - qwen3.6_sft_train.jsonl      (messages format, 95%)
       - qwen3.6_sft_val.jsonl        (messages format, 5%)

Qwen3.6 is a hybrid reasoning model. These completions contain no genuine
<think> traces, so the data is emitted for NON-thinking-mode SFT: train with
enable_thinking=False (the chat template then inserts the empty <think></think>
pair itself). Do NOT fabricate think blocks from answer text.
"""

import json
import random
import re
from collections import Counter

SRC = "final_combined_dataset.jsonl"
CLEANED = "cleaned_dataset.jsonl"
TRAIN = "qwen3.6_sft_train.jsonl"
VAL = "qwen3.6_sft_val.jsonl"

SYSTEM_PROMPT = (
    "You are an expert financial analyst specializing in equity research, "
    "technical analysis, and trading strategy. Provide structured, "
    "quantitative analysis with explicit assumptions, scenario probabilities, "
    "and risk disclosures. Your analysis is educational and not financial advice."
)

PLACEHOLDER_RE = re.compile(
    r"\[Current Date\]|\[DATE\]|\[Insert[^\]]*\]|\[Placeholder[^\]]*\]"
    r"|\[TICKER\]|\[Company\]",
    re.IGNORECASE,
)


def repair_text(text: str) -> str:
    # Double-escaped control chars stored as literal text.
    if "\\n" in text:
        text = text.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
    return text


def strip_jsonl_artifacts(completion: str):
    """Remove generation artifacts; return (text, was_corrupted_beyond_repair)."""
    pos = completion.find("```jsonl")
    if pos != -1:
        completion = completion[:pos].rstrip().rstrip("*-# ").rstrip()
    # Trailing "}\n{" — a following JSON record bled into this completion.
    m = re.search(r"\}\s*\n\s*\{", completion[-400:])
    if m:
        completion = completion[: len(completion) - 400 + m.start()].rstrip()
        if completion.endswith("}") and completion.count("{") < completion.count("}"):
            completion = completion[:-1].rstrip()
    return completion


def main():
    records, parse_errors = [], 0
    with open(SRC) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and "prompt" in obj and "completion" in obj:
                    records.append(obj)
                else:
                    parse_errors += 1
            except json.JSONDecodeError:
                parse_errors += 1

    stats = Counter(parse_errors=parse_errors)
    cleaned, seen_prompts = [], set()

    for rec in records:
        prompt = repair_text(rec["prompt"]).strip()
        completion = repair_text(rec["completion"]).strip()

        before = len(completion)
        completion = strip_jsonl_artifacts(completion)
        if len(completion) < before:
            stats["jsonl_artifacts_stripped"] += 1

        if PLACEHOLDER_RE.search(prompt) or PLACEHOLDER_RE.search(completion):
            prompt = PLACEHOLDER_RE.sub("the present analysis date", prompt)
            completion = PLACEHOLDER_RE.sub("the present analysis date", completion)
            stats["placeholders_fixed"] += 1

        if len(completion) < 800:
            stats["dropped_truncated_or_short"] += 1
            continue

        if prompt in seen_prompts:
            stats["dropped_duplicate_prompt"] += 1
            continue
        seen_prompts.add(prompt)

        cleaned.append({"prompt": prompt, "completion": completion})

    with open(CLEANED, "w") as f:
        for rec in cleaned:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    qwen = [
        {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": rec["prompt"]},
                {"role": "assistant", "content": rec["completion"]},
            ]
        }
        for rec in cleaned
    ]

    random.seed(42)
    random.shuffle(qwen)
    n_val = max(1, len(qwen) // 20)
    val, train = qwen[:n_val], qwen[n_val:]

    for path, split in ((TRAIN, train), (VAL, val)):
        with open(path, "w") as f:
            for rec in split:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    total_tokens = sum(
        (len(r["prompt"]) + len(r["completion"])) // 4 for r in cleaned
    )
    print(f"Input records:            {len(records)} (+{parse_errors} unparseable)")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"Cleaned records:          {len(cleaned)}")
    print(f"Train / Val:              {len(train)} / {len(val)}")
    print(f"Approx. total tokens:     {total_tokens:,}")


if __name__ == "__main__":
    main()
