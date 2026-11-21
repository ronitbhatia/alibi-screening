"""Rule-only screening decisions. Practice labels are used only by score()."""
from collections import Counter

from .extract import apply_reads
from .explain import reason, rule_path
from .facts import Evidence, bounds, values
from .identity import date_parts, fold, names_agree, parts
from .resolve import Resolver
from .store import linked_hits


def date_relation(left, right):
    if not left or not right:
        return "unknown"
    if left[0] != right[0]:
        return "conflict" if abs(left[0] - right[0]) > 1 else "unknown"
    return "agree" if all(x == y for x, y in zip(left, right)) else "conflict"


def too_early(dob, report, company):
    if not dob or not report:
        return False
    earliest, _ = bounds(dob)
    _, event = bounds(report)
    if company:
        return earliest > event
    age = event.year - earliest.year - ((event.month, event.day) < (earliest.month, earliest.day))
    return age < 16


def decide(customer, hit, profile, screened_at):
    hit_id = hit["hit_id"]
    kind = fold(hit.get("entity_type"))
    company = kind == "company"
    names = [hit.get("name"), *values(hit.get("aliases"))]
    names = [name for name in names if parts(name, company)]
    comparisons = []
    for name, source in customer["names"]:
        if len(parts(name, company)) >= (1 if company else 2) and names:
            comparisons.append(any(names_agree(name, other, company) for other in names))
        else:
            comparisons.append(None)
    name_ok = bool(comparisons) and all(x is True for x in comparisons)
    name_conflict = customer["verified"] and bool(comparisons) and all(x is False for x in comparisons)
    dates = customer["dates"]
    date_states = [[date_relation(d, other) for other, _ in profile["dates"]] for d, _ in dates]
    dob_ok = bool(date_states) and all("agree" in row for row in date_states)
    dob_conflict = bool(date_states) and all(row and all(x == "conflict" for x in row) for row in date_states)
    locations = {v for v, _ in customer["countries"]}
    shared = locations & profile["countries"]
    disjoint = bool(locations and profile["countries"] and not shared)
    types = {fold(v) for v, _ in customer["types"]}
    known = {"company", "individual"}
    same_type = types == {kind} and kind in known
    screen = date_parts(screened_at)
    death = profile["death"]
    past_death = bool(death and screen and bounds(death)[1] < bounds(screen)[0])
    temporal = same_type and bool(dates) and all(too_early(d, profile["report"], company) for d, _ in dates)
    can_confirm = same_type and name_ok and not (disjoint or past_death or temporal or dob_conflict)

    record_backed = customer.get("has_record", True)
    verified_dob = customer.get("verified_dob", customer["verified"])
    hard_date = verified_dob or (company and record_backed)
    name_ids = [s for _, s in customer["names"]]
    dob_ids = [s for _, s in dates]
    date_ids = dob_ids + [s for _, s in profile["dates"]]
    # One witness per country for disjoint sets, one common country for overlap.
    country_witness = {}
    for value, source in customer["countries"]:
        country_witness.setdefault(value, source)
    country_ids = ([country_witness[min(shared)]] if shared else list(country_witness.values()))
    agreement_ids = dob_ids + [next(s for other, s in profile["dates"] if date_relation(d, other) == "agree")
                               for d, _ in dates] if dob_ok else []
    type_ids = [s for _, s in customer["types"]]
    # First applicable proof wins. Keep every check for replay and explanation.
    checks = [
        ("DIFFERENT_ENTITY", "ENTITY_TYPE_MISMATCH", record_backed and bool(types) and types <= known and kind in known and kind not in types, type_ids),
        ("DIFFERENT_ENTITY", "DECEASED", past_death, []),
        ("DIFFERENT_ENTITY", "TEMPORAL_IMPOSSIBILITY", hard_date and temporal, dob_ids),
        ("DIFFERENT_ENTITY", "DOB_MISMATCH", hard_date and dob_conflict, date_ids),
        ("DIFFERENT_ENTITY", "NAME_MISMATCH_VERIFIED", name_conflict, name_ids),
        ("DIFFERENT_ENTITY", "LOCATION_MISMATCH", record_backed and customer.get("record_country", True) and disjoint and not (name_ok and dob_ok), country_ids),
        ("SAME_ENTITY", "NAME_LOCATION_MATCH", can_confirm and company and bool(shared), name_ids + country_ids),
        ("SAME_ENTITY", "NAME_DOB_LOCATION_MATCH", can_confirm and not company and dob_ok and bool(shared), name_ids + agreement_ids + country_ids),
        ("SAME_ENTITY", "NAME_DOB_MATCH", can_confirm and not company and dob_ok, name_ids + agreement_ids),
    ]
    decision, code, _, sources = next((c for c in checks if c[2]),
        ("REVIEW", "INSUFFICIENT_DATA", True, name_ids + date_ids + country_ids))
    trace = {"eligible": {c: bool(ok) for _, c, ok, _ in checks},
                      "customer": customer, "listed_names": names, "listed_type": kind,
                      "listed_dates": profile["dates"], "listed_countries": sorted(profile["countries"]),
                      "earliest_report": profile["report"], "death_date": death, "screened_at": screened_at,
                      "name_agrees": name_ok, "date_relations": date_states, "shared_countries": sorted(shared),
                      "date_agrees": dob_ok, "disjoint": disjoint, "verified_dob": hard_date}
    if code == "INSUFFICIENT_DATA":
        sources = name_ids + [s for d,s in dates + profile["dates"] if d]
        if disjoint or company:
            sources += country_ids
    return {"decision": decision, "code": code, "reason": reason(code, trace),
            "evidence": sorted({hit_id, *sources}), "trace": trace}


def adjudicate(dataset, extractor=None):
    alerts, pairs = linked_hits(dataset)
    resolver = Resolver(dataset)
    evidence = Evidence(dataset, resolver, model_reads=extractor is not None)
    resolutions = {key: resolver.resolve(alert) for key, alert in alerts.items()}
    profiles = {}
    customers = {}
    for hit, alert in pairs:
        profiles[hit["hit_id"]] = evidence.profile(hit)
        resolution = resolutions[alert["alert_id"]]
        for group in resolution.candidates or ((),):
            key = (resolution.alert_id, group)
            if key not in customers:
                customers[key] = evidence.customer(group, alert)
    pending = (req for facts in (*customers.values(), *profiles.values()) for req in facts["pending"])
    reads = extractor.batch(pending) if extractor is not None else {}
    customer_reads = {key: apply_reads(facts, reads, profile=False) for key, facts in customers.items()}
    for hit, alert in pairs:
        resolution = resolutions[alert["alert_id"]]
        profile = profiles[hit["hit_id"]]
        profile_audit, profile_missing = apply_reads(profile, reads, profile=True)
        candidates = []
        for group in resolution.candidates or ((),):
            key = (resolution.alert_id, group)
            verdict = decide(customers[key], hit, profile, alert.get("screened_at"))
            audit, missing = customer_reads[key]
            if extractor is not None:
                verdict["trace"]["model_reads"] = audit + profile_audit
                verdict["trace"]["model"] = extractor.model
                verdict["trace"]["seed"] = extractor.seed
            code = verdict["code"]
            independent = (code in {"DECEASED", "ENTITY_TYPE_MISMATCH"}
                           or code == "NAME_MISMATCH_VERIFIED" and "name" not in missing
                           or code == "TEMPORAL_IMPOSSIBILITY" and "dob" not in missing
                           or code == "DOB_MISMATCH" and "dob" not in (missing | profile_missing))
            if (missing or profile_missing) and not independent:
                sources = sorted({r["source"] for r in audit + profile_audit if r["status"] != "verified"})
                verdict.update(decision="REVIEW", code="INSUFFICIENT_DATA",
                               evidence=sorted({hit["hit_id"], *sources}),
                               reason=(f"The comparison with {hit.get('name') or hit['hit_id']} depends on "
                                       f"unresolved identity evidence in {', '.join(sources)}. "
                                       "A reviewer must verify the source wording before deciding the hit."))
            candidates.append({"records": group, **verdict})
        classes = {c["decision"] for c in candidates}
        decision, code = candidates[0]["decision"], candidates[0]["code"]
        if len(classes) > 1:
            decision, code = "REVIEW", "AMBIGUOUS_CUSTOMER_RECORD"
        explanation = candidates[0]["reason"]
        decisive_candidates = candidates
        if len(classes) > 1:
            a = candidates[0]
            b = next(c for c in candidates if c["decision"] != a["decision"])
            decisive_candidates = (a, b)
            listed_name = str(hit.get("name") or hit["hit_id"]).rstrip(".")
            explanation = (f"{a['records'][0]} yields {a['decision']} ({a['code']}) for {listed_name}. "
                           f"{b['records'][0]} yields {b['decision']} ({b['code']}) for the same hit. "
                           "The alert cannot distinguish these customers.")
        elif len(candidates) > 1:
            explanation += f" All {len(candidates)} candidate customers lead to this decision."
        sources = {s for c in decisive_candidates for s in c["evidence"]}
        if code == "AMBIGUOUS_CUSTOMER_RECORD":
            sources.update(c["records"][0] for c in (a, b))
        if any(c["code"] == "DECEASED" for c in decisive_candidates):
            sources.add(alert["alert_id"])
        for candidate in candidates:
            candidate["rule_path"] = rule_path(candidate, len(resolution.candidates), len(classes) > 1)
        yield {"screened_name": alert.get("customer_name"), "listed_name": hit.get("name"),
               "category": hit.get("primary_category") or next(iter(values(hit.get("categories"))), ""),
               "rejected_facts": profile["rejected"], "hit_id": hit["hit_id"], "alert_id": alert["alert_id"], "decision": decision, "code": code,
               "customer_record_id": resolution.customer_record_id, "evidence": sorted(sources), "reason": explanation,
               "resolution_route": resolution.route, "candidates": candidates}


def error_cost(truth, predicted):
    if truth == predicted:
        return 0
    if predicted == "DIFFERENT_ENTITY":
        return 60
    if {truth, predicted} == {"SAME_ENTITY", "REVIEW"}:
        return 2
    if truth == "DIFFERENT_ENTITY" and predicted in {"REVIEW", "SAME_ENTITY"}:
        return 3
    raise ValueError(f"Unexpected decision pair: {truth}, {predicted}")


def score(predictions, labels):
    by_hit = {p["hit_id"]: p for p in predictions}
    correct = exact = unsafe = total = 0
    misses = []
    baseline = cost = 0
    for label in labels:
        total += 1
        p = by_hit[label["hit_id"]]
        same_class = p["decision"] == label["decision"]
        correct += same_class
        incurred = error_cost(label["decision"], p["decision"])
        baseline += error_cost(label["decision"], "REVIEW")
        cost += incurred
        unsafe += not same_class and p["decision"] == "DIFFERENT_ENTITY"
        if same_class and p["code"] in label["reason_codes"]:
            exact += 1
        else:
            if same_class:
                category = "reason_code"
            elif p["decision"] == "DIFFERENT_ENTITY":
                category = "wrong_clearance"
            elif p["decision"] == "REVIEW":
                category = "over_referral"
            else:
                category = "false_confirmation"
            misses.append({"classification": category, "cost": incurred, "hit_id": p["hit_id"], "predicted": [p["decision"], p["code"]],
                           "expected": [label["decision"], label["reason_codes"]],
                           "reference_reason": label.get("reference_reason"),
                           "candidate_rules": [c["code"] for c in p["candidates"]]})
    return {"hits": len(by_hit), "decisions": dict(Counter(p["decision"] for p in by_hit.values())),
            "practice_hits": total, "class_correct": correct, "class_and_code_correct": exact,
            "unsafe_clearances": unsafe, "all_review_cost": baseline,
            "system_cost": cost, "unpriced_errors": 0,
            "cost_saved": baseline-cost,
            "decision_score": max(0, (baseline-cost)/baseline) if baseline else None,
            "misses": misses}
