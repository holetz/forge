#!/usr/bin/env python3
"""Spike H1 re-grade: Flash 1017 as the grader of the 36 saved H1 cases, vs the recorded Opus verdicts.

    regrade.py plan                       list the jobs, their assertions and estimated input tokens (no network)
    regrade.py run [--limit N] [--job J]  grade the next N jobs, ONE call at a time, each in a fresh container
    regrade.py ledger                     measured tokens and US$ so far, and the projection for the rest
    regrade.py compare [--md]             Flash vs recorded Opus, per assertion

No agent is ever run: a job is the saved output and snapshot of a case, wrapped in the eval runner's
grader prompt, sent as one chat-completions request by regrade-call.py inside the disposable image.
Host and token are minted from the Databricks profile at run time and travel to the container by
variable name; nothing of them is written. The cap is code: a call whose worst-case price would take
the ledger past CAP_USD is refused before it is made.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
OUT = RESULTS / "regrade"
LEDGER = OUT / "ledger.json"
MODEL = "system.ai.deepseek-v4-1-flash-1017"
PROFILE = os.environ.get("H1_PROFILE", "ia")
IMAGE = os.environ.get("H1_IMAGE", "forge-h1-opencode:local")
CAP_USD = 1.00
MAX_TOKENS = 8000
PRICE = json.loads((HERE / "prices.json").read_text(encoding="utf-8"))["arms"]["deepseek-v4-1-flash-1017"]
MATERIAL_LIMIT = 60000
RESULT_SET = ("pass", "fail", "unknown")

# Copied verbatim from the eval runner (scripts/run_evals.py at the quenching commit the image was built from).
GRADER_PROMPT = """You grade one run of a coding agent. For EVERY assertion below, decide whether the
material shows it holds. Reply with ONLY a JSON array, one object per assertion, in order:
[{{"assertion": "<the assertion verbatim>", "result": "pass" | "fail" | "unknown",
  "evidence": "<a verbatim excerpt copied from the material that shows the result>"}}]
The evidence must be copied character for character from the material. When no excerpt settles
an assertion, answer "unknown" with an empty evidence.

ASSERTIONS:
{assertions}

MATERIAL:
{material}
"""


def squash(text: str) -> str:
    return " ".join(text.split())


def parse_grades(text: str) -> list | None:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\[", text):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(value, list) and all(isinstance(v, dict) for v in value):
            return value
    return None


def verify_grades(assertions: list[str], raw: list | None, material: str) -> list[dict]:
    """The runner's rule: one grade per assertion; unparsed or unquoted evidence is `unknown`."""
    by_text = {str(g.get("assertion", "")): g for g in raw or []}
    haystack = squash(material)
    grades = []
    for index, assertion in enumerate(assertions):
        grade = by_text.get(assertion)
        if grade is None and raw and index < len(raw):
            named = str(raw[index].get("assertion", ""))
            grade = raw[index] if named not in assertions else None
        if grade is None:
            grades.append({"assertion": assertion, "result": "unknown", "evidence": "",
                           "reason": "the grader returned no grade for this assertion"})
            continue
        result, evidence = grade.get("result"), str(grade.get("evidence") or "")
        entry = {"assertion": assertion, "result": result, "evidence": evidence}
        if result not in RESULT_SET:
            entry.update(result="unknown", reason=f"the grader answered {result!r}")
        elif result != "unknown" and (not squash(evidence) or squash(evidence) not in haystack):
            entry.update(result="unknown", reason="the evidence is not a verbatim excerpt of the material")
        grades.append(entry)
    return grades


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""


def jobs() -> list[dict]:
    """The 36 cases, the re-run replacing the original when one exists (as report.py does)."""
    found = []
    for arm_dir in sorted(p for p in RESULTS.iterdir() if p.is_dir() and p.name not in ("rerun", "regrade")):
        for grading in sorted(arm_dir.rglob("grading.json")):
            suite_dir = grading.parent
            suite = suite_dir.relative_to(arm_dir).as_posix()
            again = RESULTS / "rerun" / arm_dir.name / suite / "grading.json"
            redone = {c["case"]: c for c in json.loads(again.read_text(encoding="utf-8"))} if again.is_file() else {}
            for case in json.loads(grading.read_text(encoding="utf-8")):
                where = again.parent if case["case"] in redone else suite_dir
                case = redone.get(case["case"], case)
                record = case["arms"]["with"]
                output = read(where / f"{case['case']}.with.output.txt")
                snap = read(where / f"{case['case']}.with.snapshot.txt")
                material = f"# AGENT OUTPUT\n{output[-MATERIAL_LIMIT:]}\n\n# WORKSPACE AFTER THE RUN\n{snap[-MATERIAL_LIMIT:]}"
                assertions = [a["assertion"] for a in record["assertions"]]
                prompt = GRADER_PROMPT.format(assertions="\n".join(f"- {a}" for a in assertions), material=material)
                found.append({"job": f"{arm_dir.name}/{suite}/{case['case']}", "arm": arm_dir.name, "suite": suite,
                              "case": case["case"], "assertions": assertions, "opus": [a["result"] for a in record["assertions"]],
                              "opusUnknown": record.get("error") is not None, "material": material, "prompt": prompt,
                              "estInput": len(prompt) // 4 + 1})
    return sorted(found, key=lambda j: (j["estInput"], j["job"]))


def usd(prompt: int, cached: int, completion: int) -> float:
    return ((prompt - cached) * PRICE["input"] + cached * PRICE["cacheRead"] + completion * PRICE["output"]) / 1e6


def worst_case(job: dict) -> float:
    return usd(int(job["estInput"] * 1.25), 0, MAX_TOKENS)


def ledger() -> list[dict]:
    return json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.is_file() else []


def projection(todo: list[dict], done: list[dict]) -> dict:
    """Remaining cost from the measured calls: input by the measured chars-to-token ratio, output by the measured maximum."""
    if not done:
        return {"remainingUsd": None}
    ratio = sum(d["promptTokens"] for d in done) / sum(d["estInput"] for d in done)
    out = max(d["completionTokens"] for d in done)
    remaining = sum(usd(int(j["estInput"] * ratio), 0, out) for j in todo)
    spent = sum(d["usd"] for d in done)
    return {"ratio": round(ratio, 3), "maxCompletion": out, "remainingUsd": round(remaining, 4),
            "totalUsd": round(spent + remaining, 4)}


def secrets() -> dict:
    token = json.loads(subprocess.run(["databricks", "auth", "token", "-p", PROFILE, "--force-refresh", "-o", "json"],
                                      capture_output=True, text=True, check=True).stdout)["access_token"]
    host = json.loads(subprocess.run(["databricks", "auth", "env", "--profile", PROFILE],
                                     capture_output=True, text=True, check=True).stdout)["env"]["DATABRICKS_HOST"]
    return {"DATABRICKS_HOST": host, "DATABRICKS_TOKEN": token}


def call(job: dict) -> dict:
    """One request in one fresh container: no mount, the token by name, the script on -c."""
    request = {"model": MODEL, "messages": [{"role": "user", "content": job["prompt"]}],
               "max_tokens": MAX_TOKENS, "temperature": 0}
    env = dict(os.environ, **secrets())
    source = (HERE / "regrade-call.py").read_text(encoding="utf-8")
    cmd = ["docker", "run", "--rm", "-i", "--network", "bridge", "--user", f"{os.getuid()}:{os.getgid()}",
           "-e", "HOME=/tmp/h1home", "-e", "DATABRICKS_HOST", "-e", "DATABRICKS_TOKEN",
           "--label", "forge.spike=h1", "--entrypoint", "python3", IMAGE, "-I", "-c", source]
    done = subprocess.run(cmd, input=json.dumps(request), capture_output=True, text=True, env=env, timeout=900)
    try:
        answer = json.loads(done.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        answer = {"status": None, "error": "no JSON from the container, exit %s" % done.returncode}
    for secret in (env["DATABRICKS_HOST"], env["DATABRICKS_TOKEN"]):
        if "error" in answer:
            answer["error"] = str(answer["error"]).replace(secret, "<redacted>")
    return answer


def cmd_plan() -> int:
    todo = jobs()
    print(f"{len(todo)} jobs, {sum(len(j['assertions']) for j in todo)} assertions, "
          f"~{sum(j['estInput'] for j in todo)} input tokens (chars/4)")
    for j in todo:
        print(f"{j['estInput']:>7}  {len(j['assertions']):>2}  {j['job']}")
    return 0 if len(todo) == 36 else 1


def cmd_ledger() -> int:
    done, todo = ledger(), [j for j in jobs() if not (OUT / f"{j['job']}.json").is_file()]
    print(json.dumps({"calls": len(done), "promptTokens": sum(d["promptTokens"] for d in done),
                      "cachedTokens": sum(d["cachedTokens"] for d in done),
                      "completionTokens": sum(d["completionTokens"] for d in done),
                      "usd": round(sum(d["usd"] for d in done), 6), "capUsd": CAP_USD,
                      "remainingJobs": len(todo), **projection(todo, done)}, indent=2))
    return 0


def cmd_run(args: list[str]) -> int:
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else 10**6
    only = args[args.index("--job") + 1] if "--job" in args else None
    OUT.mkdir(parents=True, exist_ok=True)
    made = 0
    for job in jobs():
        if only and job["job"] != only:
            continue
        path = OUT / f"{job['job']}.json"
        if path.is_file():
            continue
        if made >= limit:
            break
        done = ledger()
        spent, worst = sum(d["usd"] for d in done), worst_case(job)
        if spent + worst > CAP_USD:
            print(f"STOP before {job['job']}: spent {spent:.4f} + worst case {worst:.4f} > cap {CAP_USD:.2f}")
            return 1
        answer = call(job)
        made += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        if "error" in answer:
            print(f"{job['job']}: call failed: {answer.get('status')} {answer['error']}")
            return 1
        use = answer["usage"]
        prompt, completion = int(use.get("prompt_tokens") or 0), int(use.get("completion_tokens") or 0)
        cached = int((use.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        grades = verify_grades(job["assertions"], parse_grades(answer["content"]), job["material"])
        cost = usd(prompt, cached, completion)
        record = {"job": job["job"], "finish": answer["finish"], "promptTokens": prompt, "cachedTokens": cached,
                  "completionTokens": completion, "estInput": job["estInput"], "usd": cost}
        path.write_text(json.dumps({**record, "reply": answer["content"], "grades": grades}, indent=1) + "\n", encoding="utf-8")
        LEDGER.write_text(json.dumps(done + [record], indent=1) + "\n", encoding="utf-8")
        todo = [j for j in jobs() if not (OUT / f"{j['job']}.json").is_file()]
        print(f"{job['job']}: prompt {prompt} cached {cached} completion {completion} finish {answer['finish']} "
              f"US$ {cost:.5f}; total US$ {spent + cost:.5f}; {len(todo)} left; projection {projection(todo, done + [record])}")
        proj = projection(todo, done + [record])
        if proj["remainingUsd"] is not None and spent + cost + proj["remainingUsd"] > CAP_USD:
            print("STOP: the projection for the remaining jobs would cross the cap")
            return 1
    return 0


def rows() -> list[dict]:
    out = []
    for j in jobs():
        path = OUT / f"{j['job']}.json"
        if not path.is_file():
            continue
        flash = json.loads(path.read_text(encoding="utf-8"))
        for i, text in enumerate(j["assertions"]):
            out.append({"job": j["job"], "arm": j["arm"], "suite": j["suite"], "assertion": text,
                        "opus": j["opus"][i], "flash": flash["grades"][i]["result"],
                        "flashReason": flash["grades"][i].get("reason", ""),
                        "flashEvidence": flash["grades"][i]["evidence"]})
    return out


def pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.1f}%)" if d else "-"


def cmd_compare(args: list[str]) -> int:
    data = rows()
    if not data:
        print("nothing graded yet")
        return 1
    md = "--md" in args
    agree = lambda rs: sum(r["opus"] == r["flash"] for r in rs)
    print(f"{'| ' if md else ''}group | assertions | agree")
    if md:
        print("| --- | --- | --- |")
    groups = [("ALL", data)] + [(f"arm {a}", [r for r in data if r["arm"] == a]) for a in sorted({r["arm"] for r in data})] \
        + [(f"suite {s}", [r for r in data if r["suite"] == s]) for s in sorted({r["suite"] for r in data})]
    for name, rs in groups:
        print(f"{'| ' if md else ''}{name} | {len(rs)} | {pct(agree(rs), len(rs))}{' |' if md else ''}")
    kinds = {}
    for r in data:
        kinds[(r["opus"], r["flash"])] = kinds.get((r["opus"], r["flash"]), 0) + 1
    print("\nconfusion (opus -> flash):", {f"{a}->{b}": n for (a, b), n in sorted(kinds.items())})
    # case approval: every assertion passes
    cases = {}
    for r in data:
        c = cases.setdefault(r["job"], {"opus": True, "flash": True})
        c["opus"] &= r["opus"] == "pass"
        c["flash"] &= r["flash"] == "pass"
    same = sum(c["opus"] == c["flash"] for c in cases.values())
    print(f"case approval agreement: {pct(same, len(cases))}; "
          f"Flash approves {sum(c['flash'] for c in cases.values())}, Opus approves {sum(c['opus'] for c in cases.values())}")
    risky = [r for r in data if r["flash"] == "pass" and r["opus"] == "fail"]
    print(f"risky direction (Flash pass, Opus fail): {len(risky)}")
    print("\ndisagreements:")
    for r in data:
        if r["opus"] != r["flash"]:
            note = f" [{r['flashReason']}]" if r["flashReason"] else ""
            print(f"- {r['job']} :: {r['assertion']} :: opus {r['opus']} / flash {r['flash']}{note}")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        return 2
    verb, rest = argv[0], argv[1:]
    return {"plan": lambda: cmd_plan(), "ledger": lambda: cmd_ledger(), "run": lambda: cmd_run(rest),
            "compare": lambda: cmd_compare(rest)}.get(verb, lambda: 2)()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
