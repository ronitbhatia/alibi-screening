"""Deterministic extraction of identity evidence for Phase 3."""
import calendar
import re
from collections import defaultdict
from datetime import date

import json
from pathlib import Path

from .extract import sentences, DOB_HINT
from .identity import COUNTRIES, date_parts, fold

MONTH = "(?:" + "|".join(calendar.month_name[1:]) + ")"
DATE = rf"(?:{MONTH} \d{{1,2}}, \d{{4}}|\d{{1,2}} {MONTH} \d{{4}}|\d{{1,2}}/\d{{1,2}}/\d{{4}}|\d{{4}}-\d{{2}}-\d{{2}}|{MONTH} \d{{4}}|\d{{4}}-\d{{2}}|\d{{4}})"
TERRITORIES = {fold(name): code for code, name in json.loads(
    Path(__file__).with_name("territories.json").read_text(encoding="utf-8")).items()}
TERRITORIES.update(COUNTRIES)
TERRITORIES.update({"macao special administrative region of china": "MO", "turkey": "TR",
                    "trinidad and tobago": "TT", "palestina": "PS", "bosnia and herzegovina": "BA"})
TERRITORIES.update({code.lower(): code for code in TERRITORIES.values()})
PLACE = re.compile(r"\b(?:" + "|".join(re.escape(x) for x in sorted(TERRITORIES, key=len, reverse=True)
                                        if len(x) > 2) + r")\b")
CATEGORY_PHRASE = {"sanctions": "a sanctioned individual", "pep": "a politically exposed person",
                   "enforcement": "the subject of enforcement action", "adverse media": "named in adverse media",
                   "soe": "a state-owned enterprise official"}


def values(value):
    if isinstance(value, list):
        return value
    return str(value).split(";") if value else []


def country_code(value):
    return TERRITORIES.get(fold(value))


def profile_countries(hit):
    result = set()
    for key in ("countries", "nationality", "citizenship", "addresses", "place_of_birth"):
        for raw in values(hit.get(key)):
            tail = re.sub(r"\[[^\]]*\]", "", str(raw)).split(",")[-1]
            code = country_code(tail)
            if code:
                result.add(code)
    return result


def bounds(parts):
    year = parts[0]
    month = parts[1] if len(parts) > 1 else 1
    start = date(year, month, parts[2] if len(parts) == 3 else 1)
    month = parts[1] if len(parts) > 1 else 12
    end = date(year, month, parts[2] if len(parts) == 3 else calendar.monthrange(year, month)[1])
    return start, end


def narrative_dobs(text):
    born = re.search(rf"\bborn (?:on |in )?({DATE})\b", text, re.I)
    if born and (parts := date_parts(born[1])):
        return [parts]
    when = re.search(rf"\b{MONTH} (?:\d{{1,2}}, )?(\d{{4}})\b", text, re.I)
    age = re.search(r"\b(?:aged?|age of|who was|he was|she was) (\d{1,3})\b|\b(\d{1,3})[- ]years?[- ](?:old|of age)\b"
                    r"|, (\d{1,3}),|\bage of [^,.\n]+? was (\d{1,3})\b", text, re.I)
    if when and age:
        years = int(next(x for x in age.groups() if x))
        last = int(when[1]) - years
        return [(last-1,), (last,)]
    return []


def web_matches(snippet, phrase, countries):
    for sentence in sentences(fold(snippet)):
        if phrase not in sentence:
            continue
        # Prefer the listing's stated country over incidental locations in the story.
        following = sentence.partition(phrase)[2]
        explicit = re.match(r"\s+in (?:the )?", following)
        if explicit:
            match = PLACE.match(following, explicit.end())
            if match and TERRITORIES[match[0]] in countries:
                return True
        else:
            mentioned = {TERRITORIES[m[0]] for m in PLACE.finditer(sentence)}
            if mentioned and mentioned <= countries:
                return True
    return False


def profile_facts(hit, search_rows, model_reads=False):
    pending = []
    rejected = []
    countries = profile_countries(hit)
    hit_id = hit["hit_id"]
    listed = values(hit.get("dobs"))
    dates = [(date_parts(v), hit_id) for v in listed]
    notes = str(hit.get("profile_notes") or "").strip()
    if not listed and fold(hit.get("entity_type")) == "individual":
        paragraph = re.split(r"\n\s*\n", notes, maxsplit=1)[0]
        # Stop at attributed reports, which can discuss other people's ages.
        paragraph = re.split(r"(?:^|\n)According to ", paragraph, maxsplit=1)[0]
        dates = [(d, hit_id) for d in narrative_dobs(paragraph)]
        if model_reads and not dates:
            pending.extend({"source": hit_id, "field": "dob", "sentence": sentence}
                           for sentence in sentences(paragraph) if DOB_HINT.search(sentence))
        if not dates and not pending:
            categories = values(hit.get("categories"))
            category = hit.get("primary_category") or (categories[0] if categories else "")
            phrase = CATEGORY_PHRASE.get(fold(category), "named on a watchlist")
            for row in search_rows:
                snippet = str(row.get("snippet") or "")
                if web_matches(snippet, phrase, countries):
                    parsed = narrative_dobs(snippet)
                    dates.extend((d, row["result_id"]) for d in parsed)
                    if model_reads and not parsed:
                        pending.extend({"source": row["result_id"], "field": "dob", "sentence": sentence}
                                       for sentence in sentences(snippet) if DOB_HINT.search(sentence))
                else:
                    rejected.append({"source": row["result_id"], "quote": snippet,
                                     "reason": "The result does not establish the listing category in a country tied to this profile."})
    reports = [date_parts(match[1]) for match in re.finditer(
        rf"^According to [^;\n]+; ({MONTH} \d{{1,2}}, \d{{4}}):", notes, re.M)]
    return {"countries": countries, "rejected": rejected, "pending": pending, "dates": dates, "report": min(filter(None, reports), default=()),
            "death": date_parts(hit.get("deceased_date"))}


def note_check(row):
    text = row.get("text") or ""
    if fold(row.get("channel")) != "kyc" or re.search(r"\b(rejected|expired|failed|pending|declined|not)\b", text, re.I):
        return None
    if not re.search(r"\b(verified|validated|completed?|confirmed|cleared|passed|accepted|approved|successful(?:ly)?|matches)\b", text, re.I):
        return None
    # The browse set's verification notes use uppercase legal names.
    words = re.findall(r"[^\W\d_]+(?:[-’'][^\W\d_]+)*|[^\s\w]", text)
    sequences, current = [], []
    for word in words + ["."]:
        token = word.removesuffix("'s").removesuffix("’s")
        if token.isupper() and token not in {"DOB", "ID", "KYC"}:
            current.append(token)
        elif current:
            sequences.append(" ".join(current))
            current = []
    dob = re.search(rf"(?:\b(?:DOB|born|birth date|birth)(?: (?:recorded|verified|confirmed|on document))?"
                    rf"(?: as| is| of| on)?[: ]+|\()({DATE})\b", text, re.I)
    locations = {TERRITORIES[m[0]] for m in PLACE.finditer(fold(text))}
    return {"date": date_parts(row.get("created_at")), "source": row["note_id"],
            "name": sequences[0] if len(sequences) == 1 else None,
            "dob": date_parts(dob[1]) if dob else (),
            "country": next(iter(locations)) if len(locations) == 1 else None}


class Evidence:
    def __init__(self, dataset, resolver, model_reads=False):
        self.model_reads = model_reads
        self.resolver = resolver
        self.notes = defaultdict(list)
        for row in dataset.rows("notes"):
            self.notes[fold(row.get("requester_email"))].append(row)
        self.web = defaultdict(list)
        for row in dataset.rows("search_results"):
            self.web[fold(row.get("query"))].append(row)

    def customer(self, group, alert):
        records = [self.resolver.records[key] for key in group]
        checks = [
            {"source": row["check_id"], "date": date_parts(row.get("checked_at")),
             "name": row.get("verified_name"), "dob": date_parts(row.get("verified_dob")),
             "country": country_code(row.get("document_country"))}
            for key in group for row in self.resolver.approved_checks.get(key, ())
        ]
        note_text = {}
        locations = []
        for email in sorted({fold(r.get("email")) for r in records} - {""}):
            if self.resolver.links["email"][email].difference(group):
                continue
            for note in self.notes[email]:
                if check := note_check(note):
                    checks.append(check)
                    note_text[check["source"]] = note["text"]
                else:
                    text = fold(note.get("text"))
                    for m in re.finditer(r"(?:liv(?:ed|ing)(?: and work(?:ed|ing))?|residen(?:t|ce|cy|tial activity)|home|job) in (?:the )?", text):
                        if place := PLACE.match(text, m.end()):
                            locations.append((TERRITORIES[place[0]], note["note_id"]))
        if not records:
            records = [{"record_id": alert["alert_id"], "full_name": alert.get("customer_name"),
                        "dob": alert.get("dob"), "residence_country": alert.get("country")}]
        latest = max((c["date"] for c in checks), default=())
        checks = [c for c in checks if c["date"] == latest]
        pending = []
        if self.model_reads:
            for check in checks:
                text = note_text.get(check["source"], "")
                for sentence in sentences(text):
                    if not check["dob"] and DOB_HINT.search(sentence):
                        pending.append({"source": check["source"], "field": "dob", "sentence": sentence})
                    if not check["name"] and re.search(r"\b(?:name|holder|identity|passport|verified|validated)\b", sentence, re.I):
                        pending.append({"source": check["source"], "field": "name", "sentence": sentence})
        for r in records:
            for field in ("residence_country", "nationality"):
                locations.append((country_code(r.get(field)), r["record_id"]))
        locations.extend((c["country"], c["source"]) for c in checks)
        names = [(c["name"], c["source"]) for c in checks]
        # Missing fields on the latest approved check remain unknown. Using a weaker
        # declared DOB here could clear a real hit when a note parser misses a date.
        dates = [(c["dob"], c["source"]) for c in checks]
        return {"pending": pending, "has_record": bool(group),
                "record_country": any(country_code(r.get(f)) for r in records for f in ("residence_country", "nationality")) or any(c["country"] for c in checks), "verified_dob": bool(checks) and all(c["dob"] for c in checks),
                "verified": any(name for name, _ in names), "names": names or [(r.get("full_name"), r["record_id"]) for r in records],
                "dates": dates or [(date_parts(r.get("dob")), r["record_id"]) for r in records],
                "countries": [(v,s) for v,s in locations if v],
                "types": [(r.get("entity_type") or alert.get("entity_type"), r["record_id"]) for r in records]}

    def profile(self, hit):
        query = " ".join(sorted(re.findall(r"[^\W_]+", fold(hit.get("name")))))
        return profile_facts(hit, self.web[query], self.model_reads)
