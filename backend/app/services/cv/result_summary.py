"""Bounded chat projection of the existing verified screening results."""
from app.services.cv.screening import valid_aggregate_score


def summarize_results(results, *, limit=5, offset=0, view="ranked"):
    if (type(limit) is not int or not 1 <= limit <= 20 or type(offset) is not int
        or not 0 <= offset <= 10000 or view not in {"ranked", "issues", "all"}):
        raise ValueError("Use limit 1–20, a nonnegative offset up to 10000, and ranked/issues/all view.")
    ranked, incomplete = results["ranked"], results["incomplete"]
    issues = [item for item in incomplete if item["status"] in {"failed", "needs_review"} or item.get("error_code")]
    items = ranked if view == "ranked" else issues if view == "issues" else [*ranked, *incomplete]
    ranked_ids = {item["id"] for item in ranked}
    candidates = []
    for item in items[offset:offset + limit]:
        score = item.get("score")
        valid = valid_aggregate_score(score)
        current = item["id"] in ranked_ids and not item["outdated"] and valid
        candidates.append({"application_id": item["application_id"], "name": item["name"],
            "status": item["status"], "outdated": item["outdated"],
            "rank": item.get("rank") if current else None,
            "score": score["aggregate"] if current else None,
            "saved_score": score["aggregate"] if valid and not current else None,
            "score_status": "current" if current else "older" if item["outdated"] else "withheld" if valid else "not_scored",
            "essential_criteria_to_review": len(score.get("must_have_review_flags", [])) if current else None,
            "warnings": item.get("warnings", []), "error_code": item.get("error_code")})
    more = offset + limit < len(items)
    return {"job_id": results["job_id"], "status": "available" if results["run"] else "not_started",
        "run": results["run"], "view": view, "candidates": candidates,
        "counts": {"ranked": len(ranked), "unranked": len(incomplete), "processing_issues": len(issues),
                   "outdated": sum(bool(item["outdated"]) for item in [*ranked, *incomplete])},
        "warnings": results["warnings"], "has_more": more, "next_offset": offset + limit if more else None,
        "matching_count": len(items), "screening_started": False}
