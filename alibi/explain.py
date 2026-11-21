"""Render the deciding comparisons from their recorded values."""
def date_text(parts):
    return "-".join([str(parts[0]), *(f"{n:02d}" for n in parts[1:])]) if parts else "unknown"


def reason(code, trace):
    customer = trace["customer"]
    def items(seq):
        return ", ".join(dict.fromkeys(str(v) for v in seq if v)) or "unknown"
    names = items(v for v, _ in customer["names"])
    listed = next(iter(trace["listed_names"]), "an unnamed profile").rstrip(".")
    first = f"The customer name is {names} and the listed name is {listed}."
    birth = items(date_text(v) for v, _ in customer["dates"])
    listed_dates = items(date_text(v) for v, _ in trace["listed_dates"])
    location = items(sorted({v for v, _ in customer["countries"]}))
    hit_location = items(trace["listed_countries"])
    shared = items(trace["shared_countries"])
    if code == "DECEASED":
        return f"The profile for {listed} records a death date of {date_text(trace['death_date'])}. This predates screening on {trace['screened_at']}."
    if code == "TEMPORAL_IMPOSSIBILITY":
        event = date_text(trace["earliest_report"])
        if trace["listed_type"] == "company":
            return f"The company record gives an incorporation date of {birth}. The earliest dated report for {listed} is {event}, before incorporation."
        return f"The verified birth date is {birth}. The customer was under 16 at the earliest dated report for {listed} on {event}."
    if code == "ENTITY_TYPE_MISMATCH":
        return f"The customer record identifies {names} as {items(v for v,_ in customer['types'])}. The listing identifies {listed} as {trace['listed_type']}."
    if code == "NAME_MISMATCH_VERIFIED":
        return f"The approved identity check gives the legal name {names}. It conflicts with {listed} and every available listed alias."
    if code == "DOB_MISMATCH":
        return f"The verified customer date is {birth}. Every listed date for {listed} ({listed_dates}) conflicts under the date comparison rule."
    if code == "LOCATION_MISMATCH":
        return f"The customer records tie {names} to {location}. The profile for {listed} is tied to {hit_location}, with no country in common."
    if code == "NAME_LOCATION_MATCH":
        return first + f" The company names agree and both records are tied to {shared}."
    if code.startswith("NAME_DOB"):
        detail = f"The names agree and the customer date {birth} agrees with a listed date ({listed_dates})."
        return first + " " + detail + (f" Both sides are tied to {shared}." if code == "NAME_DOB_LOCATION_MATCH" else "")
    if trace["name_agrees"] and trace["date_agrees"] and trace["disjoint"]:
        return first + f" The names and dates agree, but the country sets ({location} and {hit_location}) are disjoint."
    if any("conflict" in row for row in trace["date_relations"]) and not trace["verified_dob"]:
        return first + f" The recorded date {birth} lacks verification for clearing against {listed_dates}."
    if not trace["name_agrees"]:
        return first + " The identity evidence does not establish a name match or a verified contradiction."
    if trace["name_agrees"] and not trace["listed_dates"] and trace["listed_type"] == "individual":
        return first + " The names agree, but the profile supplies no usable birth date and no verified contradiction settles the hit."
    return first + f" The available dates (customer {birth}, profile {listed_dates}) and locations (customer {location}, profile {hit_location}) leave the identity unresolved."


def rule_path(candidate, candidate_count, divergent):
    """Describe the recorded checks and distinguish eligible alternatives from failures."""
    t = candidate["trace"]
    eligible = t["eligible"]
    customer = t["customer"]
    dob = ", ".join(date_text(v) for v, _ in t["listed_dates"]) or "unknown"
    same_name = "agree" if t["name_agrees"] else "do not establish agreement"
    same_date = "agree" if t["date_agrees"] else "do not establish agreement"
    common = ", ".join(t["shared_countries"]) or "none"
    details = {
        "ENTITY_TYPE_MISMATCH": f"Customer type: {', '.join(str(v or 'unknown') for v, _ in customer['types'])}. Listed type: {t['listed_type'] or 'unknown'}.",
        "DECEASED": f"Listed death date: {date_text(t['death_date'])}. Screened on {t['screened_at']}.",
        "TEMPORAL_IMPOSSIBILITY": f"Earliest qualifying report: {date_text(t['earliest_report'])}. Customer date: {', '.join(date_text(v) for v, _ in customer['dates']) or 'unknown'}.",
        "DOB_MISMATCH": f"Listed dates: {dob}. Pairwise comparisons: {', '.join(x for row in t['date_relations'] for x in row) or 'unknown'}.",
        "NAME_MISMATCH_VERIFIED": f"Names {same_name}. Approved legal name available: {'yes' if customer['verified'] else 'no'}.",
        "LOCATION_MISMATCH": f"Shared countries: {common}. Country sets proven disjoint: {'yes' if t['disjoint'] else 'no'}.",
        "NAME_LOCATION_MATCH": f"Company rule. Listed type: {t['listed_type'] or 'unknown'}. Names {same_name}. Shared countries: {common}.",
        "NAME_DOB_LOCATION_MATCH": f"Names {same_name}; dates {same_date}. Shared countries: {common}.",
        "NAME_DOB_MATCH": f"Names {same_name}; dates {same_date}. Confirmation also requires compatible types and no contradiction.",
        "INSUFFICIENT_DATA": "The available evidence leaves the candidate unresolved." if candidate["code"] == "INSUFFICIENT_DATA" else "A more specific rule settles this candidate.",
        "AMBIGUOUS_CUSTOMER_RECORD": f"{candidate_count} plausible customers. " + ("Their verdicts differ." if divergent else "Their verdicts do not diverge."),
    }
    rows = []
    for code, detail in details.items():
        if code == candidate["code"] or (code == "AMBIGUOUS_CUSTOMER_RECORD" and divergent):
            status = "Selected"
        elif eligible.get(code):
            status = "Eligible alternative"
        else:
            status = "Ruled out"
        rows.append({"code": code, "status": status, "detail": detail})
    return rows
