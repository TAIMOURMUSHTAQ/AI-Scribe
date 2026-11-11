#!/usr/bin/env python3
"""Regression runner for the LinguaLink360 Scribe SOAP prompt.

Usage:
  python3 run_tests.py --cases cases.json --dry-run          # validate file and regexes
  SCRIBE_API=https://your-host/api/analyse \
    python3 run_tests.py --cases cases.json --repeats 3       # run automated cases
Filters:
  --only A1,B1      run specific ids
  --category F_safety
Output: printed summary plus results.json (per-run outputs and failures).

Before the first real run, edit normalise() so it matches your API's response shape.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_input(case):
    if "input_template" in case:
        t = case["input_template"]
        return (t["filler"] + " ") * t["repeat"] + t["final"]
    return case.get("input", "")


def call_scribe(session_notes, patient_context):
    url = os.environ.get("SCRIBE_API")
    if not url:
        raise RuntimeError("Set SCRIBE_API to your analyse endpoint.")
    payload = json.dumps(
        {"session_notes": session_notes, "patient_context": patient_context}
    ).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def normalise(resp):
    """EDIT THIS to match your API's response shape.

    Must return a dict with:
      soap:      {"S": str, "O": str, "A": str, "P": str}
      flags:     list of safety / conflict / review flag strings
      checklist: list of 'missing from the record' question strings
      raw:       full text of the response (used for scope "all")
      error:     True if the call failed
    """
    if isinstance(resp, dict) and resp.get("error"):
        return {"soap": {}, "flags": [], "checklist": [], "raw": "", "error": True}
    soap = resp.get("soap", {})
    return {
        "soap": {k: soap.get(k, "") for k in "SOAP"},
        "flags": resp.get("flags", []),
        "checklist": resp.get("checklist", []),
        "raw": json.dumps(resp, ensure_ascii=False),
        "error": False,
    }


def check(case, out):
    exp = case.get("expect", {})
    fails = []
    soap_text = " ".join(out["soap"].get(k, "") for k in "SOAP")
    scope_text = soap_text if exp.get("scope", "soap") == "soap" else out["raw"]
    flag_text = " ".join(list(out["flags"]) + list(out["checklist"]))

    if exp.get("error_or_empty"):
        if not (out["error"] or not soap_text.strip()):
            fails.append("expected an error or an empty note")
        return fails
    if out["error"]:
        return ["API returned an error"]

    for s in exp.get("must_contain", []):
        if s.lower() not in scope_text.lower():
            fails.append(f"missing text: {s}")
    for s in exp.get("must_not_contain", []):
        if s.lower() in scope_text.lower():
            fails.append(f"forbidden text: {s}")
    for p in exp.get("must_contain_regex", []):
        if not re.search(p, scope_text, re.I):
            fails.append(f"missing pattern: {p}")
    for p in exp.get("must_not_regex", []):
        m = re.search(p, scope_text, re.I)
        if m:
            fails.append(f"forbidden pattern {p!r} matched '{m.group(0)}'")
    for p in exp.get("must_flag_regex", []):
        if not re.search(p, flag_text, re.I):
            fails.append(f"no flag matching: {p}")
    for p in exp.get("must_not_flag_regex", []):
        m = re.search(p, flag_text, re.I)
        if m:
            fails.append(f"unwanted flag matched: '{m.group(0)}'")
    return fails


def validate(cases):
    problems = []
    keys = ["must_contain_regex", "must_not_regex", "must_flag_regex", "must_not_flag_regex"]
    ids = set()
    for c in cases:
        if c["id"] in ids:
            problems.append(f"duplicate id {c['id']}")
        ids.add(c["id"])
        exp = c.get("expect", {})
        if not isinstance(exp, dict):  # manual cases use a plain-text expectation
            continue
        for k in keys:
            for p in exp.get(k, []):
                try:
                    re.compile(p)
                except re.error as e:
                    problems.append(f"{c['id']} {k} bad regex {p!r}: {e}")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="cases.json")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--only", default="")
    ap.add_argument("--category", default="")
    ap.add_argument("--out", default="results.json")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    cases = load(a.cases)["cases"]
    if a.only:
        wanted = {x.strip() for x in a.only.split(",")}
        cases = [c for c in cases if c["id"] in wanted]
    if a.category:
        cases = [c for c in cases if c.get("category") == a.category]

    problems = validate(cases)
    if problems:
        print("Problems in cases file:")
        for p in problems:
            print("  -", p)
        sys.exit(2)

    auto = [c for c in cases if c.get("type") == "text"]
    manual = [c for c in cases if c.get("type") == "manual"]
    print(f"Loaded {len(cases)} cases: {len(auto)} automated, {len(manual)} manual.")

    if a.dry_run:
        print("Dry run OK. All regexes compile.")
        return

    results = []
    for c in auto:
        runs = []
        for _ in range(a.repeats):
            t0 = time.time()
            out, fails = None, []
            try:
                resp = call_scribe(build_input(c), c.get("patient_context", ""))
                out = normalise(resp)
                fails = check(c, out)
            except Exception as e:  # keep going so one failure does not stop the suite
                fails = [f"exception: {e}"]
            runs.append({"fails": fails, "seconds": round(time.time() - t0, 1), "output": out})
        passed = sum(1 for r in runs if not r["fails"])
        results.append({
            "id": c["id"], "title": c["title"], "category": c["category"],
            "severity": c.get("severity", "normal"),
            "passed": passed, "runs": a.repeats, "runs_detail": runs,
        })

    print()
    print(f"{'ID':<5}{'SEV':<10}{'PASS':<8}TITLE / FIRST FAILURE")
    for r in results:
        first = next((x for run in r["runs_detail"] for x in run["fails"]), "")
        mark = f"{r['passed']}/{r['runs']}"
        print(f"{r['id']:<5}{r['severity']:<10}{mark:<8}{r['title']}" + (f"  -> {first}" if first else ""))

    total_pass = sum(1 for r in results if r["passed"] == r["runs"])
    print(f"\nAutomated cases fully passing: {total_pass}/{len(results)}")

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"results": results, "manual": [
            {"id": c["id"], "title": c["title"], "steps": c.get("steps", []), "expect": c.get("expect", "")}
            for c in manual]}, f, ensure_ascii=False, indent=2)
    print(f"Saved detailed results to {a.out}. Complete the {len(manual)} manual cases by hand.")

    critical_failed = [r["id"] for r in results if r["severity"] == "critical" and r["passed"] < r["runs"]]
    if critical_failed:
        print("CRITICAL failures:", ", ".join(critical_failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
