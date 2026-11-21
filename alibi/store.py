"""Read-only adapters for the screening development and evaluation data."""
from collections import Counter
from contextlib import contextmanager
import json
from pathlib import Path

from zipfile import ZipFile

from .identity import dataset_names

from .workbook import Workbook

# Required columns are the relationship keys. Other source columns pass through.
TABLES = {
    "alerts": ("Alerts", ("alert_id", "hit_ids")),
    "hits": ("Hits", ("hit_id", "alert_id")),
    "customers": ("Customers", ("record_id", "email")),
    "accounts": ("Accounts", ("account_id", "customer_id", "login_handle")),
    "onboarding": ("Onboarding", ("account_id", "customer_id", "kyc_applicant_id")),
    "kyc_checks": ("KYC checks", ("applicant_id", "check_id")),
    "notes": ("Notes", ("requester_email", "note_id")),
    "search_results": ("Search results", ("query", "result_id")),
    "practice_labels": ("Practice labels", ("hit_id", "alert_id", "decision", "reason_codes")),
}

REASONS = {
    "DIFFERENT_ENTITY": {"TEMPORAL_IMPOSSIBILITY", "DOB_MISMATCH", "LOCATION_MISMATCH",
                         "NAME_MISMATCH_VERIFIED", "ENTITY_TYPE_MISMATCH", "DECEASED"},
    "SAME_ENTITY": {"NAME_DOB_LOCATION_MATCH", "NAME_DOB_MATCH", "NAME_LOCATION_MATCH"},
    "REVIEW": {"INSUFFICIENT_DATA", "AMBIGUOUS_CUSTOMER_RECORD"},
}

LIST_FIELDS = {
    "alerts": ("hit_ids",),
    "hits": ("aliases", "dobs", "countries", "categories", "addresses"),
    "practice_labels": ("reason_codes", "customer_record_ids", "evidence"),
}


class DataError(ValueError):
    pass


def normalize(table, row, where):
    if table == "alerts" and "customer" in row:
        customer = row["customer"]
        if not isinstance(customer, dict):
            raise DataError(f"{where}: expected a customer object")
        for key in ("name", "entity_type", "dob", "country", "city"):
            row["customer_name" if key == "name" else key] = customer.get(key)
    missing = set(TABLES[table][1]) - row.keys()
    if missing:
        raise DataError(f"{where}: missing fields {sorted(missing)}")
    for field in LIST_FIELDS.get(table, ()):
        value = row.get(field)
        if value is None:
            row[field] = []
        elif isinstance(value, str):
            row[field] = [part.strip() for part in value.split(";") if part.strip()]
        elif isinstance(value, (int, float)) and field == "dobs":
            row[field] = [str(value)]
        elif not isinstance(value, list):
            raise DataError(f"{where}: expected a list in {field}")
    return row


class Dataset:
    def __init__(self, path, workbook=None):
        self.path = path
        self.workbook = workbook

    def available(self, table):
        if self.workbook:
            return TABLES[table][0] in self.workbook.sheets
        return (self.path / f"{table}.jsonl").is_file()

    def rows(self, table):
        sheet, keys = TABLES[table]
        if not self.available(table):
            raise DataError(f"Missing input table: {table}")
        if self.workbook:
            rows = self.workbook.rows(sheet)
            header = next(rows, ())
            if not set(keys).issubset(header):
                raise DataError(f"{sheet}: missing required headers")
            for number, values in enumerate(rows, 2):
                if any(value is not None for value in values):
                    row = {key: value for key, value in zip(header, values) if key}
                    yield normalize(table, row, f"{sheet} row {number}")
            return
        with (self.path / f"{table}.jsonl").open(encoding="utf-8-sig") as source:
            for number, line in enumerate(source, 1):
                if line.strip():
                    where = f"{table}.jsonl line {number}"
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise DataError(f"{where}: invalid JSON") from exc
                    if not isinstance(row, dict):
                        raise DataError(f"{where}: expected an object")
                    yield normalize(table, row, where)


@contextmanager
def open_dataset(path):
    path = Path(path)
    if path.is_dir():
        with dataset_names(path / "name_variants.json"):
            yield Dataset(path)
    elif path.is_file() and path.suffix.lower() == ".xlsx":
        with ZipFile(path) as archive:
            yield Dataset(path, Workbook(archive))
    else:
        raise DataError(f"Expected a workbook or JSONL directory: {path}")


def index(rows, key):
    result = {}
    for row in rows:
        value = row[key]
        if not isinstance(value, str) or not value or value in result:
            raise DataError(f"Duplicate or invalid {key}: {value!r}")
        result[value] = row
    return result


def linked_hits(dataset):
    """Return the alert index and (hit, alert) pairs after validating both directions."""
    alerts = index(dataset.rows("alerts"), "alert_id")
    hits = index(dataset.rows("hits"), "hit_id")
    declared = {}
    for alert_id, alert in alerts.items():
        ids = alert["hit_ids"]
        if "hit_count" in alert and alert["hit_count"] != len(ids):
            raise DataError(f"{alert_id}: hit_count disagrees with hit_ids")
        for hit_id in ids:
            if not isinstance(hit_id, str) or hit_id in declared:
                raise DataError(f"{alert_id}: repeated or invalid hit ID")
            declared[hit_id] = alert_id
    if set(declared) != hits.keys():
        raise DataError("Listed hit IDs differ from the IDs in the hits table")
    for hit_id, hit in hits.items():
        if hit["alert_id"] != declared[hit_id]:
            raise DataError(f"{hit_id}: contradictory parent alert")
    return alerts, [(hit, alerts[hit["alert_id"]]) for hit in hits.values()]


def inspect(dataset):
    alerts, pairs = linked_hits(dataset)
    parents = {hit["hit_id"]: alert["alert_id"] for hit, alert in pairs}
    counts = {"alerts": len(alerts), "hits": len(pairs)}
    for table in TABLES:
        if table not in counts and table != "practice_labels":
            counts[table] = sum(1 for _ in dataset.rows(table))
    decisions = Counter()
    if dataset.available("practice_labels"):
        labels = index(dataset.rows("practice_labels"), "hit_id")
        for hit_id, label in labels.items():
            if hit_id not in parents or parents[hit_id] != label["alert_id"]:
                raise DataError(f"{hit_id}: practice label has no matching hit/alert")
            codes = label["reason_codes"]
            if not codes or not set(codes).issubset(REASONS.get(label["decision"], set())):
                raise DataError(f"{hit_id}: invalid practice decision or reason")
            decisions[label["decision"]] += 1
        counts["practice_labels"] = len(labels)
    return {"source": str(dataset.path), "rows": counts, "linked_hits": len(pairs),
            "practice_decisions": dict(decisions)}
