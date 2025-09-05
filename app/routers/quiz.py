from datetime import datetime, timezone, date, time, timedelta
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, field_validator
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.dependencies.db import get_db

router = APIRouter(prefix="/quiz", tags=["quiz"])

# ---- Models ----


class SubmitQuizRequest(BaseModel):
    qrId: str
    correctAnswers: int

    @field_validator("qrId")
    @classmethod
    def qr_required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("qrId is required")
        return v

    @field_validator("correctAnswers")
    @classmethod
    def non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("correctAnswers must be >= 0")
        return v


# ---- Helpers ----

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _date_bounds(start_date: date, end_date: date | None) -> tuple[datetime, datetime]:
    """Return [start_dt, end_dt) UTC bounds for date-only filtering."""
    start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    if end_date is None:
        end_dt = start_dt + timedelta(days=1)
    else:
        if end_date < start_date:
            raise ValueError("endDate cannot be earlier than startDate")
        end_dt = datetime.combine(
            end_date, time.min, tzinfo=timezone.utc) + timedelta(days=1)
    return start_dt, end_dt


# ---- Validators ----

@router.get("/validate/{qrId}")
async def validate_quiz_eligibility(qrId: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    """
    Return whether a quiz can be taken for this qrId (only if **no** quiz exists).
    """
    qr = (qrId or "").strip()
    if not qr:
        return {"status": "error", "message": "qrId is required"}

    existing = await db["quiz_results"].find_one({"qrId": qr}, projection={"_id": 1})
    eligible = existing is None
    return {"status": "success", "eligible": eligible}


# ---- Queries ----

@router.get("/list")
async def list_quiz_results(
    startDate: date = Query(..., description="YYYY-MM-DD"),
    endDate: date | None = Query(None, description="YYYY-MM-DD"),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    List quiz submissions filtered by submittedAt date (UTC), sorted by most recent first.
    """
    try:
        start_dt, end_dt = _date_bounds(startDate, endDate)
    except ValueError as e:
        return {"status": "error", "message": str(e)}

    cursor = (
        db["quiz_results"]
        .find(
            {"submittedAt": {"$gte": start_dt, "$lt": end_dt}},
            projection={"_id": 0, "sysId": 1, "qrId": 1,
                        "correctAnswers": 1, "submittedAt": 1},
        )
        .sort("submittedAt", -1)
    )
    items = [doc async for doc in cursor]
    return {"status": "success", "data": items}


@router.get("/by-qr/{qrId}")
async def get_quiz_by_qr(
    qrId: str,
    startDate: date | None = Query(None, description="Optional YYYY-MM-DD"),
    endDate: date | None = Query(None, description="Optional YYYY-MM-DD"),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Get quiz submissions for a specific qrId (newest first).
    Optionally filter by submittedAt date (UTC).
    """
    qr = (qrId or "").strip()
    if not qr:
        return {"status": "error", "message": "qrId is required"}

    query: dict = {"qrId": qr}
    if startDate is not None:
        try:
            start_dt, end_dt = _date_bounds(startDate, endDate)
        except ValueError as e:
            return {"status": "error", "message": str(e)}
        query["submittedAt"] = {"$gte": start_dt, "$lt": end_dt}

    cursor = (
        db["quiz_results"]
        .find(query, projection={"_id": 0, "sysId": 1, "qrId": 1, "correctAnswers": 1, "submittedAt": 1})
        .sort("submittedAt", -1)
    )
    items = [doc async for doc in cursor]
    return {"status": "success", "data": items}


# ---- Commands ----

@router.post("/submit")
async def submit_quiz(payload: SubmitQuizRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    """
    Submit quiz results **once per qrId**.
    """
    users = db["users"]
    quizzes = db["quiz_results"]

    qr = payload.qrId.strip()
    correct = int(payload.correctAnswers)

    # Validate user exists
    user = await users.find_one({"qrId": qr}, projection={"_id": 0, "sysId": 1})
    if not user:
        return {"status": "error", "message": "User not found for the provided qrId"}

    # Enforce single submission per qrId
    already = await quizzes.find_one({"qrId": qr}, projection={"_id": 1})
    if already:
        return {"status": "error", "message": "Quiz already submitted for this QR"}

    now = _utcnow()

    await quizzes.insert_one({"sysId": user["sysId"], "qrId": qr, "correctAnswers": correct, "submittedAt": now})

    await users.update_one(
        {"qrId": qr},
        {
            "$inc": {"quizStats.totalQuizzes": 1, "quizStats.totalCorrectAnswers": correct},
            "$set": {"updatedAt": now, "lastQuizSubmittedAt": now},
        },
    )

    return {"status": "success", "message": "Quiz submitted successfully", "data": {"qrId": qr, "correctAnswers": correct}}
