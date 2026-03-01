"""
Quick smoke-test for the FOA pipeline.
Runs each test case as a subprocess with a hard wall-clock timeout,
prints a pass/fail table, and dumps foa.json per case.
"""

import json
import os
import subprocess
import sys
import textwrap

CASE_TIMEOUT = 25  # hard wall-clock seconds per case (OS-level kill)

OUT_BASE = "./out/tests"

CASES = [
    # (label, url, expect_nonempty_fields)
    (
        "Grants.gov — path ID",
        "https://www.grants.gov/search-results-detail/355964",
        ["foa_id", "title", "agency", "description"],
    ),
    (
        "Grants.gov — query string oppId",
        "https://www.grants.gov/search-results-detail/355964?oppId=355964",
        ["foa_id", "title", "agency"],
    ),
    (
        "NSF — award API (AWD_ID query param)",
        "https://www.nsf.gov/awardsearch/show-award?AWD_ID=2517085",
        ["foa_id", "title", "description", "award_max"],
    ),
    (
        "NSF — award API (biomedical, tagging check)",
        "https://www.nsf.gov/awardsearch/show-award?AWD_ID=2412345",
        ["foa_id", "title", "description"],
    ),
    (
        "NSF — award API (climate/environment, tagging check)",
        "https://www.nsf.gov/awardsearch/show-award?AWD_ID=2309810",
        ["foa_id", "title", "description"],
    ),
    (
        "NSF — award API (AI/ML, tagging check)",
        "https://www.nsf.gov/awardsearch/show-award?AWD_ID=2319592",
        ["foa_id", "title", "description"],
    ),
]

WIDTH = 46

def run():
    results = []
    for label, url, required_fields in CASES:
        out_dir = os.path.join(
            OUT_BASE,
            label.replace(" ", "_").replace("/", "-").replace("(", "").replace(")", "")
        )
        os.makedirs(out_dir, exist_ok=True)
        print(f"\n{'='*60}")
        print(f"  {label}")
        print(f"  {url}")
        print(f"{'='*60}")

        # Run each case as a subprocess so a hang doesn't block the whole suite
        runner_code = textwrap.dedent(f"""
            import sys, os, json
            sys.path.insert(0, {repr(os.path.dirname(os.path.abspath(__file__)))})
            import main as _main
            _main.TIMEOUT = 15
            from main import ingest, tag, export
            foa = ingest({repr(url)})
            foa["tags"] = tag(foa)
            export(foa, {repr(out_dir)})
            print("__RESULT__" + json.dumps(foa))
        """)

        try:
            proc = subprocess.run(
                [sys.executable, "-c", runner_code],
                capture_output=True, text=True,
                timeout=CASE_TIMEOUT,
                cwd=os.path.dirname(os.path.abspath(__file__))
            )
            # Print live output lines (excluding __RESULT__ marker)
            for line in proc.stdout.splitlines():
                if not line.startswith("__RESULT__"):
                    print(f"  {line}")
            if proc.stderr:
                for line in proc.stderr.splitlines():
                    print(f"  [stderr] {line}")

            if proc.returncode != 0:
                results.append((label, "FAIL", f"exit {proc.returncode}"))
                continue

            # Extract the JSON result embedded in stdout
            result_line = next((l for l in proc.stdout.splitlines() if l.startswith("__RESULT__")), None)
            if not result_line:
                results.append((label, "FAIL", "no __RESULT__ in output"))
                continue

            foa = json.loads(result_line[len("__RESULT__"):])
            missing = [f for f in required_fields if not foa.get(f)]
            status = "PASS" if not missing else f"PARTIAL (empty: {', '.join(missing)})"

            print(f"\n  foa_id     : {foa.get('foa_id', '')}")
            title = foa.get('title', '') or ''
            print(f"  title      : {(title[:65] + '...') if len(title) > 65 else title or '(empty)'}")
            print(f"  agency     : {foa.get('agency', '') or '(empty)'}")
            print(f"  open_date  : {foa.get('open_date', '') or '—'}")
            print(f"  close_date : {foa.get('close_date', '') or '—'}")
            print(f"  award_min  : {foa.get('award_min', '') or '—'}")
            print(f"  award_max  : {foa.get('award_max', '') or '—'}")
            tagged = {k: v for k, v in foa.get("tags", {}).items() if v}
            if tagged:
                print(f"  tags:")
                for cat, vals in tagged.items():
                    print(f"    {cat:<12}: {', '.join(vals)}")
            else:
                print(f"  tags       : (none)")
            results.append((label, status, None))

        except subprocess.TimeoutExpired:
            results.append((label, "FAIL", f"timed out after {CASE_TIMEOUT}s"))
            print(f"\n  ERROR: timed out after {CASE_TIMEOUT}s")
        except Exception as e:
            results.append((label, "FAIL", str(e)))
            print(f"\n  ERROR: {e}")

    # Summary table
    print(f"\n\n{'='*70}")
    print(f"  RESULTS SUMMARY")
    print(f"{'='*70}")
    for label, status, err in results:
        icon = "✓" if status == "PASS" else ("⚠" if status.startswith("PARTIAL") else "✗")
        print(f"  {icon}  {label[:WIDTH]:<{WIDTH}}  {status}")
        if err:
            print(f"       Error: {err[:80]}")
    print(f"{'='*70}\n")

    failures = [r for r in results if r[1] == "FAIL"]
    sys.exit(1 if failures else 0)

if __name__ == "__main__":
    run()
