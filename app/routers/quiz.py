from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.dependencies.db import get_db
from app.middleware.auth import public

from datetime import date, time, timedelta
from fastapi import Query

router = APIRouter(prefix="/quiz", tags=["quiz"])

# ---- Models (kept within router as requested) ----


class SubmitQuizRequest(BaseModel):
    qrId: str
    correctAnswers: int

    @field_validator("qrId")
    @classmethod
    def qr_required(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("qrId is required")
        return v

    @field_validator("correctAnswers")
    @classmethod
    def non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("correctAnswers must be >= 0")
        return v


def _utcnow():
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


@router.post("/submit")
# @public
async def submit_quiz(payload: SubmitQuizRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    users = db["users"]
    quizzes = db["quiz_results"]

    qr = payload.qrId.strip()
    correct = int(payload.correctAnswers)

    user = await users.find_one({"qrId": qr}, projection={"_id": 0, "sysId": 1})
    if not user:
        return {"status": "error", "message": "User not found for the provided qrId"}

    now = _utcnow()

    await quizzes.insert_one(
        {"sysId": user["sysId"], "qrId": qr,
            "correctAnswers": correct, "submittedAt": now}
    )

    await users.update_one(
        {"qrId": qr},
        {
            "$inc": {
                "quizStats.totalQuizzes": 1,
                "quizStats.totalCorrectAnswers": correct,
            },
            "$set": {"updatedAt": now, "lastQuizSubmittedAt": now},
        },
    )

    return {
        "status": "success",
        "message": "Quiz submitted successfully",
        "data": {"qrId": qr, "correctAnswers": correct},
    }
