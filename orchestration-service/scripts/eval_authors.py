"""Precision/recall of author merges against eval/author_pairs.csv (hand-labelled)."""

import csv
import sys

rows = [r for r in csv.DictReader(open(sys.argv[1] if len(sys.argv) > 1 else "../eval/author_pairs.csv",
                                       encoding="utf-8")) if r["same"] in ("y", "n")]
tp = sum(r["predicted_same"] == "y" and r["same"] == "y" for r in rows)
fp = sum(r["predicted_same"] == "y" and r["same"] == "n" for r in rows)
fn = sum(r["predicted_same"] == "n" and r["same"] == "y" for r in rows)
tn = sum(r["predicted_same"] == "n" and r["same"] == "n" for r in rows)
precision = tp / (tp + fp) if tp + fp else 0.0
recall = tp / (tp + fn) if tp + fn else 0.0
print(f"labelled pairs: {len(rows)}  TP={tp} FP={fp} FN={fn} TN={tn}")
print(f"precision={precision:.2f} recall={recall:.2f}")
for r in rows:
    if (r["predicted_same"] == "y") != (r["same"] == "y"):
        print("MISTAKE:", r["name_key"], r["pmid_a"], r["pmid_b"], "predicted", r["predicted_same"], "rule", r["rule_b"])
