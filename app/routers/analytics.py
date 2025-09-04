from __future__ import annotations
from typing import Any, Dict, List, Tuple, Optional
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.dependencies.db import get_db

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_numeric_value(v: Any) -> Tuple[bool, Optional[float]]:
    """Return (True, float_value) for numeric (int/float/decimal or numeric string), else (False, None).
    Excludes booleans explicitly (bool is a subclass of int)."""
    if isinstance(v, bool):
        return False, None
    if isinstance(v, (int, float)):
        try:
            f = float(v)
            if f != float("inf") and f != float("-inf"):
                return True, f
        except Exception:
            pass
        return False, None
    # Try numeric strings
    if isinstance(v, str):
        try:
            if v.strip() == "":
                return False, None
            f = float(v)
            if f != float("inf") and f != float("-inf"):
                return True, f
        except Exception:
            return False, None
    return False, None


@router.get("/company-counts")
async def company_counts(
    db: AsyncIOMotorDatabase = Depends(get_db),
    limit: int = Query(100, ge=1, le=1000),
):
    """Counts of surveys by company with unique users and last submission timestamp."""
    surveys = db["surveys"]
    cursor = surveys.find({}, projection={"company": 1,
                          "sysId": 1, "submittedAt": 1})
    counts: Dict[str, Dict[str, Any]] = {}
    async for s in cursor:
        company = (s.get("company") or "").strip() or "Unknown"
        c = counts.setdefault(company, {
                              "company": company, "surveyCount": 0, "uniqueUsers": set(), "lastSubmittedAt": None})
        c["surveyCount"] += 1
        if s.get("sysId"):
            c["uniqueUsers"].add(s["sysId"])
        ts = s.get("submittedAt")
        if ts and (c["lastSubmittedAt"] is None or ts > c["lastSubmittedAt"]):
            c["lastSubmittedAt"] = ts
    # Format & sort
    rows = []
    for company, rec in counts.items():
        rows.append({
            "company": company,
            "surveyCount": rec["surveyCount"],
            "uniqueUsers": len(rec["uniqueUsers"]),
            "lastSubmittedAt": rec["lastSubmittedAt"],
        })
    rows.sort(key=lambda r: (-r["surveyCount"], r["company"]))
    return {"status": "success", "data": rows[:limit]}


@router.get("/average-scores")
async def average_scores(
    db: AsyncIOMotorDatabase = Depends(get_db),
    minCount: int = Query(1, ge=1, le=1_000_000),
):
    """Per-question averages across numeric answers found in survey 'answers' objects.
    Numeric strings are parsed; booleans are ignored."""
    surveys = db["surveys"]
    cursor = surveys.find({}, projection={"answers": 1})
    sums: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    async for s in cursor:
        answers = s.get("answers") or {}
        if not isinstance(answers, dict):
            continue
        for k, v in answers.items():
            ok, f = _is_numeric_value(v)
            if not ok or f is None:
                continue
            sums[k] = sums.get(k, 0.0) + f
            counts[k] = counts.get(k, 0) + 1
    result = []
    for q, total in sums.items():
        c = counts.get(q, 0)
        if c >= minCount and c > 0:
            result.append(
                {"questionKey": q, "avg": round(total / c, 2), "count": c})
    result.sort(key=lambda x: x["questionKey"])
    return {"status": "success", "data": result}


@router.get("/overview")
async def overview(db: AsyncIOMotorDatabase = Depends(get_db)):
    """High-level analytics suitable for a dashboard."""
    users = db["users"]
    surveys = db["surveys"]
    now = _utcnow()
    week_ago = now - timedelta(days=7)

    # Totals
    total_users = await users.count_documents({})
    total_surveys = await surveys.count_documents({})

    # Last 7 days
    surveys_last_7d = await surveys.count_documents({"submittedAt": {"$gte": week_ago}})

    # Average across all numeric answers
    cursor = surveys.find({}, projection={"answers": 1})
    total_num = 0.0
    total_cnt = 0
    # Also compute top companies while we stream (approx by simple counts)
    company_counter: Dict[str, int] = {}
    async for s in cursor:
        answers = s.get("answers") or {}
        if isinstance(answers, dict):
            for v in answers.values():
                ok, f = _is_numeric_value(v)
                if ok and f is not None:
                    total_num += f
                    total_cnt += 1
        company = (s.get("company") or "").strip() or "Unknown"
        company_counter[company] = company_counter.get(company, 0) + 1

    avg_numeric_score = round(total_num / total_cnt,
                              2) if total_cnt > 0 else None
    top_companies = sorted(
        [{"company": k, "surveyCount": v} for k, v in company_counter.items()],
        key=lambda r: (-r["surveyCount"], r["company"])
    )[:5]

    return {
        "status": "success",
        "data": {
            "totals": {
                "users": total_users,
                "surveys": total_surveys,
                "surveysLast7Days": surveys_last_7d,
            },
            "avgNumericScore": avg_numeric_score,
            "topCompanies": top_companies,
            "generatedAt": now,
        },
    }
