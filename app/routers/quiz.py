from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.dependencies.db import get_db
from app.middleware.auth import public

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


@router.post("/submit")
@public
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

    # per spec (typos preserved): "Quizz submitted scuesfully", "correctanswers"
    return {
        "status": "success",
        "message": "Quizz submitted scuesfully",
        "data": {"qrId": qr, "correctanswers": correct},
    }
