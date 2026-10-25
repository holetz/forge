#!/usr/bin/env python3
"""Aggregate spike H1 results: one row per arm x suite, plus one total row per arm.

    report.py <results-dir> [--json]

Reads <results>/<arm>/<suite>/grading.json (run_evals.py) and the `H1-USAGE {json}` line the
container entrypoint leaves at the end of each <case>.with.output.txt, and prices the tokens with
prices.json beside this file. Grader tokens are not captured by the runner and are not counted.
The Opus grader's tokens are therefore an ESTIMATE and an upper bound: per graded case, the
OpenCode overhead measured on a one-step call (GRADER_OVERHEAD) plus the grader material at
4 characters per token (run_evals.py caps output and snapshot at 60000 characters each), all
priced as fresh Opus input, plus GRADER_OUTPUT output tokens.
A case re-run serially under <results>/rerun/<arm>/<suite>/ (after an infrastructure failure:
token refresh or grader exit) replaces the original case; the row counts it under `rerun`.
"""
import json
import re
import sys
from pathlib import Path

USAGE = re.compile(r"^H1-USAGE (\{.*\})\s*$", re.M)
CLASSES = ("input", "output", "reasoning", "cacheRead", "cacheWrite")
GRADER_PROMPT_CHARS, MATERIAL_LIMIT = 548, 60000
GRADER_OVERHEAD, GRADER_OUTPUT = 15500, 1000


def grader_estimate(where: Path, case: dict) -> int:
    """Estimated grader input tokens for one graded case (0 when the arm failed before grading)."""
    if "error" in case["arms"]["with"]:
        return 0
    chars = GRADER_PROMPT_CHARS + sum(len(a["assertion"]) + 3 for a in case["arms"]["with"]["assertions"])
    for kind in ("output", "snapshot"):
        f = where / f"{case['case']}.with.{kind}.txt"
        chars += min(len(f.read_text(encoding="utf-8", errors="replace")), MATERIAL_LIMIT) if f.is_file() else 0
    return GRADER_OVERHEAD + chars // 4


def usage_of(path: Path) -> dict | None:
    found = USAGE.findall(path.read_text(encoding="utf-8", errors="replace")) if path.is_file() else []
    return json.loads(found[-1]) if found else None


def usd(tokens: dict, price: dict | None) -> float | None:
    if price is None:
        return None
    return sum(tokens[k] * price["output" if k == "reasoning" else k] for k in CLASSES) / 1e6


def suite_row(arm: str, suite_dir: Path, price: dict | None, rerun_dir: Path) -> dict:
    cases = [(c, suite_dir) for c in json.loads((suite_dir / "grading.json").read_text(encoding="utf-8"))]
    again = rerun_dir / "grading.json"
    redone = {c["case"]: c for c in json.loads(again.read_text(encoding="utf-8"))} if again.is_file() else {}
    cases = [(redone[c["case"]], rerun_dir) if c["case"] in redone else (c, d) for c, d in cases]
    results, approved, errors, metered = [], 0, 0, 0
    tokens = dict.fromkeys(CLASSES, 0)
    duration, grader_in, graded = 0, 0, 0
    for case, where in cases:
        record = case["arms"]["with"]
        grades = [a["result"] for a in record["assertions"]]
        results += grades
        approved += bool(grades) and all(g == "pass" for g in grades)
        errors += "error" in record
        duration += record.get("durationMs") or 0
        g = grader_estimate(where, case)
        grader_in += g
        graded += bool(g)
        use = usage_of(where / f"{case['case']}.with.output.txt")
        if use:
            metered += 1
            for k in CLASSES:
                tokens[k] += int(use.get(k) or 0)
    return {"arm": arm, "suite": suite_dir.relative_to(suite_dir.parent.parent).as_posix(),
            "cases": len(cases), "approved": approved, "rerun": len(redone),
            "caseApprovalRate": round(approved / len(cases), 4) if cases else 0.0,
            "assertions": len(results),
            "passRate": round(results.count("pass") / len(results), 4) if results else 0.0,
            "unknown": results.count("unknown"), "errors": errors, "metered": metered,
            "tokens": tokens, "totalTokens": sum(tokens.values()),
            "usd": usd(tokens, price), "durationMs": duration,
            "graderEstInput": grader_in, "graderEstOutput": graded * GRADER_OUTPUT,
            "graderEstUsd": (grader_in * GRADER_PRICE["input"] + graded * GRADER_OUTPUT * GRADER_PRICE["output"]) / 1e6}


def total_row(arm: str, rows: list[dict], price: dict | None) -> dict:
    tokens = {k: sum(r["tokens"][k] for r in rows) for k in CLASSES}
    cases = sum(r["cases"] for r in rows)
    assertions = sum(r["assertions"] for r in rows)
    approved = sum(r["approved"] for r in rows)
    passed = sum(round(r["passRate"] * r["assertions"]) for r in rows)
    return {"arm": arm, "suite": "ALL", "cases": cases, "approved": approved,
            "caseApprovalRate": round(approved / cases, 4) if cases else 0.0,
            "assertions": assertions, "passRate": round(passed / assertions, 4) if assertions else 0.0,
            "unknown": sum(r["unknown"] for r in rows), "errors": sum(r["errors"] for r in rows),
            "rerun": sum(r["rerun"] for r in rows), "metered": sum(r["metered"] for r in rows), "tokens": tokens,
            "totalTokens": sum(tokens.values()), "usd": usd(tokens, price),
            "durationMs": sum(r["durationMs"] for r in rows),
            **{k: sum(r[k] for r in rows) for k in ("graderEstInput", "graderEstOutput", "graderEstUsd")}}


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--json"]
    if len(args) != 1 or not Path(args[0]).is_dir():
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    results = Path(args[0])
    global GRADER_PRICE
    prices = json.loads((Path(__file__).with_name("prices.json")).read_text(encoding="utf-8"))["arms"]
    GRADER_PRICE = prices["opus-5-5"]
    rows = []
    for arm_dir in sorted(p for p in results.iterdir() if p.is_dir() and p.name != "rerun"):
        arm, price = arm_dir.name, prices.get(arm_dir.name)
        mine = [suite_row(arm, g.parent, price, results / "rerun" / arm / g.parent.relative_to(arm_dir))
                for g in sorted(arm_dir.glob("*/*/grading.json"))]
        rows += mine + ([total_row(arm, mine, price)] if mine else [])
    if not rows:
        print(f"report.py: no grading.json under {results}", file=sys.stderr)
        return 1
    if "--json" in argv:
        print(json.dumps(rows, indent=2))
        return 0
    head = f"{'arm':<24} {'suite':<17} {'cases':>5} {'caseApprovalRate':>16} {'passRate':>8} {'unknown':>7} {'errors':>6} {'rerun':>5} {'tokens':>11} {'cacheRead':>11} {'US$':>8} {'graderTok~':>10} {'graderUS$~':>10}"
    print(head)
    for r in rows:
        cost = "n/a" if r["usd"] is None else f"{r['usd']:.4f}"
        print(f"{r['arm']:<24} {r['suite']:<17} {r['cases']:>5} {r['caseApprovalRate']:>16.2%} {r['passRate']:>8.2%} "
              f"{r['unknown']:>7} {r['errors']:>6} {r['rerun']:>5} {r['totalTokens']:>11} {r['tokens']['cacheRead']:>11} {cost:>8} {r['graderEstInput'] + r['graderEstOutput']:>10} {r['graderEstUsd']:>10.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
