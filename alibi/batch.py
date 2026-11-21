"""Single-run interface shared by judging and the reproducibility harness."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile

from .extract import Extractor
from .rules import adjudicate
from .store import DataError, TABLES, open_dataset


def trace_path(output):
    return output.with_name(output.stem + ".trace.jsonl")


def protect_inputs(source, outputs):
    inputs = [source] if source.is_file() else [source / (table + ".jsonl") for table in TABLES]
    if source.is_dir():
        inputs.extend([source / "name_variants.json", source / "POLICY.md"])
    paths = [path.resolve() for path in outputs]
    if len(paths) != len(set(paths)) or set(paths).intersection(p.resolve() for p in inputs):
        raise DataError("Output paths overlap each other or an input table")


@contextmanager
def atomic_writer(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            yield stream
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def write_row(stream, row):
    stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def run(source, output):
    sidecar = trace_path(output)
    protect_inputs(source, [output, sidecar])
    with tempfile.TemporaryDirectory(prefix="alibi-cache-") as cache, open_dataset(source) as dataset:
        extractor = Extractor.from_env()
        extractor.cache = Path(cache)
        rows = sorted(adjudicate(dataset, extractor), key=lambda r: r["hit_id"])
    with atomic_writer(output) as verdicts, atomic_writer(sidecar) as traces:
        for row in rows:
            submission = {k: row[k] for k in ("hit_id", "alert_id", "customer_record_id", "decision", "reason", "evidence")}
            submission["reason_code"] = row["code"]
            write_row(verdicts, submission)
            write_row(traces, row)


def main():
    parser = argparse.ArgumentParser(description="Adjudicate one screening dataset")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        run(args.source, args.output)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Run failed: {exc}\n")


if __name__ == "__main__":
    main()
