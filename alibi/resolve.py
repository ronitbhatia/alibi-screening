"""Resolve each alert independently of hit profiles and practice labels."""
from collections import defaultdict
from dataclasses import dataclass
import re

from .identity import country, date_parts, fold, names_agree
from .store import index


@dataclass(frozen=True)
class Resolution:
    alert_id: str
    route: str
    candidates: tuple[tuple[str, ...], ...]  # record IDs grouped by person/company

    @property
    def customer_record_id(self):
        if len(self.candidates) > 1:
            return "AMBIGUOUS"
        return self.candidates[0][0] if self.candidates else None

    def as_dict(self):
        return {**vars(self), "customer_record_id": self.customer_record_id}


class Resolver:
    def __init__(self, dataset):
        self.records = index(dataset.rows("customers"), "record_id")
        parent = {key: key for key in self.records}

        def root(key):
            while parent[key] != key:
                parent[key] = parent[parent[key]]
                key = parent[key]
            return key

        self.links = {field: defaultdict(set) for field in ("account_ref", "login_handle", "email")}
        self.years = defaultdict(set)
        phone_dobs = {}
        for key, row in self.records.items():
            if row.get("email"):
                self.links["email"][fold(row["email"])].add(key)
            dob = date_parts(row.get("dob"))
            if dob:
                self.years[dob[0]].add(key)
            phone = re.sub(r"\D", "", str(row.get("phone") or ""))
            if phone and len(dob) == 3:
                other = phone_dobs.setdefault((phone, dob), key)
                parent[root(key)] = root(other)

        holders = set()
        for row in dataset.rows("accounts"):
            key = row["customer_id"]
            if key in parent:
                holders.add((row["account_id"], key))
                for route, column in (("account_ref", "account_id"), ("login_handle", "login_handle")):
                    if row.get(column):
                        self.links[route][fold(row[column])].add(key)
        applicants = defaultdict(set)
        for row in dataset.rows("onboarding"):
            if (row["account_id"], row["customer_id"]) in holders and row["kyc_applicant_id"]:
                applicants[row["kyc_applicant_id"]].add(row["customer_id"])
        documents = {}
        self.approved_checks = defaultdict(list)
        for row in dataset.rows("kyc_checks"):
            doc = str(row.get("document_number") or "").strip()
            if fold(row.get("result")) == "approved":
                for key in sorted(applicants.get(row["applicant_id"], ())):
                    self.approved_checks[key].append(row)
                    if doc:
                        other = documents.setdefault(doc, key)
                        parent[root(key)] = root(other)
        groups = defaultdict(list)
        for key in self.records:
            groups[root(key)].append(key)
        self.group = {}
        for members in groups.values():
            group = tuple(sorted(members))
            for key in group:
                self.group[key] = group

    def fits(self, alert, row):
        kind = fold(alert.get("entity_type"))
        if kind and kind != fold(row.get("entity_type")):
            return False
        if alert.get("dob"):
            a, b = date_parts(alert["dob"]), date_parts(row.get("dob"))
            if not a or not b or any(i != j for i, j in zip(a, b)):
                return False
        location = country(alert.get("country"))
        if location and location not in (country(row.get("nationality")), country(row.get("residence_country"))):
            return False
        city = fold(alert.get("city"))
        if city and city != fold(row.get("city")) and not re.search(
                rf"(?<!\w){re.escape(city)}(?!\w)", fold(row.get("address"))):
            return False
        return any(names_agree(alert.get("customer_name"), row.get(field), kind == "company")
                   for field in ("full_name", "preferred_name", "login_name"))

    def resolve(self, alert):
        for route, lookup in self.links.items():
            keys = lookup.get(fold(alert.get(route)))
            if keys:
                groups = {self.group[key] for key in keys}
                if len(groups) > 1:
                    groups = {self.group[key] for key in keys if self.fits(alert, self.records[key])}
                return Resolution(alert["alert_id"], route, tuple(sorted(groups)))
        # Keep stale identifiers visible as unresolved links.
        if any(alert.get(route) for route in self.links):
            return Resolution(alert["alert_id"], "unresolved_link", ())
        dob = date_parts(alert.get("dob"))
        keys = self.years.get(dob[0], ()) if dob else self.records
        groups = {self.group[key] for key in keys if self.fits(alert, self.records[key])}
        return Resolution(alert["alert_id"], "screened_details", tuple(sorted(groups)))


def resolve_dataset(dataset):
    resolver = Resolver(dataset)
    return [resolver.resolve(alert) for alert in dataset.rows("alerts")]
