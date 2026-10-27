"""Screening progress derived from saved assessment stages and criterion matches."""


def screening_progress(run, assessments, *, valid_score):
    criteria = {item["id"] for item in (run.inputs.get("profile") or {}).get("criteria", [])}
    parsed = matched = scored = read_skipped = match_skipped = score_skipped = 0
    stage_counts = {}
    for item in assessments:
        stage_counts[item.status] = stage_counts.get(item.status, 0) + 1
        read = bool(item.parse_result_ids) or item.status in {"indexing", "matching", "scoring", "completed"}
        matches = len({entry.get("requirement_id") for entry in item.matches} & criteria)
        score = item.status == "completed" and valid_score(item.score)
        parsed += int(read)
        matched += matches
        scored += int(score)
        if item.status in {"failed", "needs_review"}:
            read_skipped += int(not read)
            match_skipped += len(criteria) - matches
            score_skipped += int(not score)
    stage = next((name for name in ("parsing", "indexing", "matching", "scoring") if stage_counts.get(name)), None)
    if stage is None and run.status == "running":
        stage = "parsing" if stage_counts.get("pending") else "ranking"
    skipped = read_skipped + match_skipped + score_skipped
    return {"stage": stage, "parsed": parsed, "criteria_completed": matched,
        "criteria_total": len(criteria) * len(assessments), "scored": scored,
        "read_skipped": read_skipped, "criteria_skipped": match_skipped, "score_skipped": score_skipped,
        "completed_units": parsed + matched + scored + skipped,
        "total_units": len(assessments) * (len(criteria) + 2), "skipped_units": skipped}
