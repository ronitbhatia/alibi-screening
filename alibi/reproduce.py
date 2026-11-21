"""Compare five separate system/run processes on the same dataset."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from .batch import atomic_writer, protect_inputs, trace_path, write_row
from .store import DataError, REASONS, index, open_dataset

RUNNER = Path(__file__).resolve().parents[1] / "system" / "run"
RUN_COUNT = 5


def load_rows(path, expected):
    with path.open(encoding="utf-8") as stream:
        rows = index((json.loads(line) for line in stream), "hit_id")
    if rows.keys() != expected:
        raise DataError(f"{path.name}: missing {len(expected - rows.keys())} or extra "
                        f"{len(rows.keys() - expected)} hit IDs")
    return rows


def comparison_inputs(trace):
    inputs = {}
    for candidate in trace["candidates"]:
        group = ",".join(candidate["records"]) or "screened alert"
        for field, value in candidate["trace"].items():
            if field == "customer":
                for key, facts in value.items():
                    inputs[f"{group}.customer.{key}"] = facts
            elif field != "eligible":
                inputs[f"{group}.{field}"] = value
    return inputs


def reconcile(rows, traces):
    outcomes = [(r["decision"], r["reason_code"]) for r in rows]
    if len(rows) != RUN_COUNT or len(traces) != RUN_COUNT:
        raise DataError("Incomplete run series")
    if any(code not in REASONS.get(decision, ()) for decision, code in outcomes):
        raise DataError("Invalid decision or reason code in run output")
    counts = Counter(outcomes)
    result = dict(rows[0])
    trace = dict(traces[0])
    runs = []
    baseline = comparison_inputs(traces[0]) if len(counts) > 1 else {}
    changed = set()
    for i, ((decision, code), raw, detail) in enumerate(zip(outcomes, rows, traces), 1):
        if (detail["decision"], detail["code"]) != (decision, code):
            raise DataError("Run verdict and its trace disagree")
        trial = {"run": i, "decision": decision, "reason_code": code}
        if len(counts) > 1:
            current = comparison_inputs(detail)
            delta = {k: {"run_1": baseline.get(k), "this_run": current.get(k)}
                     for k in sorted(baseline.keys() | current.keys()) if baseline.get(k) != current.get(k)}
            changed.update(delta)
            trial.update(reason=raw["reason"], evidence=raw["evidence"], candidates=detail["candidates"],
                         differences_from_run_1=delta)
        runs.append(trial)
    trace["runs"] = runs
    trace["agreement_count"] = max(counts.values())
    trace["unstable"] = len(counts) > 1
    if trace["unstable"]:
        distribution = ", ".join(f"{n} × {d}/{c}" for (d, c), n in sorted(counts.items()))
        reason = (f"The five runs for {result['hit_id']} produced {distribution}. "
                  "A reviewer must resolve the conflicting results.")
        trace["instability_explanation"] = (
            "Run inputs or comparisons changed: " + ", ".join(sorted(changed))
            if changed else "Recorded inputs match, but the rule outputs differ; the cause is undetermined.")
        evidence = sorted({ref for r in rows for ref in r["evidence"]})
        result.update(decision="REVIEW", reason_code="INSUFFICIENT_DATA", reason=reason, evidence=evidence)
        trace.update(decision="REVIEW", code="INSUFFICIENT_DATA", reason=reason, evidence=evidence)
    return result, trace


def reproduce(source, output):
    report_path = output.with_name(output.stem + ".reproducibility.json")
    protect_inputs(source, [output, trace_path(output), report_path])
    source = source.resolve()
    with open_dataset(source) as dataset:
        expected = set(index(dataset.rows("hits"), "hit_id"))
    if not expected:
        raise DataError("No hits available for the reproducibility check")

    verdicts, traces, runs = [], [], []
    with tempfile.TemporaryDirectory(prefix="alibi-five-runs-") as tmp:
        for n in range(1, RUN_COUNT + 1):
            # Each directory starts empty, including the relative OpenRouter cache.
            work = Path(tmp) / str(n)
            work.mkdir()
            path = work / "predictions.jsonl"
            started = time.monotonic()
            subprocess.run([str(RUNNER), str(source), str(path)], cwd=work, check=True, timeout=1800)
            elapsed = time.monotonic() - started
            verdicts.append(load_rows(path, expected))
            traces.append(load_rows(trace_path(path), expected))
            runs.append({"run": n, "seconds": round(elapsed, 3), "hit_count": len(expected),
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            print(f"Run {n}/5: {len(expected)} hits in {elapsed:.1f}s", flush=True)

    unstable = []
    class_agreement = 0
    with atomic_writer(report_path) as report_file, atomic_writer(output) as results, atomic_writer(trace_path(output)) as audit:
        for hit_id in sorted(expected):
            rows = [r[hit_id] for r in verdicts]
            result, trace = reconcile(rows, [r[hit_id] for r in traces])
            if trace["unstable"]:
                unstable.append(hit_id)
            class_agreement += len({r["decision"] for r in rows}) == 1
            write_row(results, result)
            write_row(audit, trace)
        report = {"source": source.name, "hit_count": len(expected), "runs": runs,
                  "api_key_present": bool(os.getenv("OPENROUTER_API_KEY", "").strip()),
                  "python_hash_seed": 0, "cache": "fresh per run",
                  "class_unanimous": class_agreement, "class_and_code_unanimous": len(expected)-len(unstable),
                  "unstable_hit_ids": unstable, "identical_verdict_files": len({r["sha256"] for r in runs}) == 1}
        json.dump(report, report_file, indent=2)
        report_file.write("\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        report = reproduce(args.source, args.output)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Reproducibility check failed: {exc}\n")
    print(f"{report['class_and_code_unanimous']}/{report['hit_count']} hits unanimous on decision and code.")


if __name__ == "__main__":
    main()
