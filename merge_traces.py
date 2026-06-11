#!/usr/bin/env python3
"""Merge hand-written reasoning traces (traces/chunk_*.jsonl) with the cleaned
dataset and build thinking-mode SFT files for Qwen3.6-27B.

Each trace was written by reading the individual example (model-authored, not
script-generated). This script only assembles, validates, and splits.

Validation per example:
  - every index 0..N-1 has exactly one trace
  - trace length within sane bounds (>= 400 chars, <= 3000 chars)
  - trace contains no markdown headers, no <think> nesting, no JSON artifacts
  - trace is not a verbatim copy of the completion opening

Outputs:
  qwen3.6_sft_train_reasoning.jsonl
  qwen3.6_sft_val_reasoning.jsonl
"""

import glob
import json
import random
import sys

CLEANED = "cleaned_dataset.jsonl"
TRAIN_OUT = "qwen3.6_sft_train_reasoning.jsonl"
VAL_OUT = "qwen3.6_sft_val_reasoning.jsonl"

SYSTEM_PROMPT = (
    "You are an expert financial analyst specializing in equity research, "
    "technical analysis, and trading strategy. Reason carefully through the "
    "evidence before answering, then provide structured, quantitative "
    "analysis with explicit assumptions, scenario probabilities, and risk "
    "disclosures. Your analysis is educational and not financial advice."
)


def main():
    records = []
    with open(CLEANED) as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    n = len(records)

    traces = {}
    dupes = []
    for path in sorted(glob.glob("traces/chunk_*.jsonl")):
        with open(path) as f:
            for ln, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    print(f"BAD JSON: {path}:{ln}")
                    continue
                idx = obj["idx"]
                if idx in traces:
                    dupes.append(idx)
                traces[idx] = obj["trace"].strip()

    missing = [i for i in range(n) if i not in traces]
    out_of_range = [i for i in traces if i < 0 or i >= n]

    bad_quality = []
    for i, trace in traces.items():
        if i >= n:
            continue
        problems = []
        if len(trace) < 400:
            problems.append(f"too_short({len(trace)})")
        if len(trace) > 3000:
            problems.append(f"too_long({len(trace)})")
        if "<think" in trace or "</think" in trace:
            problems.append("nested_think")
        if trace.lstrip().startswith("#") or "\n#" in trace:
            problems.append("markdown_header")
        if '"idx"' in trace or "```" in trace:
            problems.append("artifact")
        comp_start = records[i]["completion"][:200].strip()
        if comp_start and comp_start[:120] in trace:
            problems.append("copies_completion")
        if problems:
            bad_quality.append((i, problems))

    print(f"Cleaned examples:   {n}")
    print(f"Traces collected:   {len(traces)}")
    print(f"Missing indices:    {len(missing)}" + (f" -> {missing[:30]}" if missing else ""))
    print(f"Duplicate indices:  {len(set(dupes))}")
    print(f"Out of range:       {out_of_range}")
    print(f"Quality flags:      {len(bad_quality)}")
    for i, probs in bad_quality[:20]:
        print(f"  idx {i}: {probs}")

    if missing or out_of_range:
        print("\nNOT building output files — fix gaps first.")
        sys.exit(1)

    examples = []
    for i, rec in enumerate(records):
        assistant = f"<think>\n{traces[i]}\n</think>\n\n{rec['completion']}"
        examples.append({
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": rec["prompt"]},
                {"role": "assistant", "content": assistant},
            ]
        })

    random.seed(42)
    random.shuffle(examples)
    n_val = max(1, len(examples) // 20)
    val, train = examples[:n_val], examples[n_val:]

    for path, split in ((TRAIN_OUT, train), (VAL_OUT, val)):
        with open(path, "w") as f:
            for ex in split:
                f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    trace_toks = sorted(len(t) // 4 for t in traces.values())
    max_total = max(sum(len(m["content"]) for m in ex["messages"]) // 4 for ex in examples)
    print(f"\nWrote {len(train)} train / {len(val)} val")
    print(f"Trace tokens: min={trace_toks[0]} median={trace_toks[len(trace_toks)//2]} max={trace_toks[-1]}")
    print(f"Longest full example: ~{max_total} tokens")


if __name__ == "__main__":
    main()
