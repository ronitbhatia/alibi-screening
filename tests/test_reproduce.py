import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from alibi.batch import trace_path
from alibi.reproduce import RUNNER, load_rows, reconcile, reproduce
from alibi.store import DataError, TABLES


class ReproducibilityTests(unittest.TestCase):
    def test_disagreement_on_code_alone_goes_to_review(self):
        codes = ["NAME_DOB_MATCH"] * 4 + ["NAME_DOB_LOCATION_MATCH"]
        rows = [dict(hit_id="H", decision="SAME_ENTITY", reason_code=c, evidence=["H"], reason=c)
                for c in codes]
        traces = [dict(decision="SAME_ENTITY", code=c, candidates=[
            {"records": ["C"], "trace": {"shared_countries": ["GB"] if i == 4 else []}}])
                  for i, c in enumerate(codes)]
        final, trace = reconcile(rows, traces)
        self.assertEqual((final["decision"], final["reason_code"]), ("REVIEW", "INSUFFICIENT_DATA"))
        self.assertEqual(trace["agreement_count"], 4)
        self.assertEqual(trace["runs"][4]["differences_from_run_1"]["C.shared_countries"]["this_run"], ["GB"])

    def test_missing_or_duplicate_hit_is_an_incomplete_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.jsonl"
            for lines in ([], ['{"hit_id":"H"}'] * 2):
                path.write_text("\n".join(lines))
                with self.assertRaises(DataError):
                    load_rows(path, {"H"})

    def test_five_processes_on_unlabeled_jsonl_and_failed_retry(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            root = Path(tmp)
            source = root / "input data"
            source.mkdir()
            for table in TABLES:
                if table != "practice_labels":
                    (source / f"{table}.jsonl").touch()
            (source / "alerts.jsonl").write_text('{"alert_id":"A","customer_name":"Sample","hit_ids":["H"]}')
            (source / "hits.jsonl").write_text('{"hit_id":"H","alert_id":"A","name":"Example"}')
            output = root / "results" / "dev.jsonl"
            report = reproduce(source, output)
            self.assertEqual(report["class_and_code_unanimous"], 1)
            self.assertTrue(report["identical_verdict_files"])
            self.assertEqual(len(json.loads(trace_path(output).read_text())["runs"]), 5)
            self.assertNotIn("candidates", json.loads(output.read_text()))
            previous = output.read_bytes()
            (source / "hits.jsonl").write_text("")
            process = subprocess.run([RUNNER, source, output], cwd=tmp, capture_output=True)
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(output.read_bytes(), previous)
