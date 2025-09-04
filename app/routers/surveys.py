from typing import Optional, Any, Dict
import re
from datetime import datetime, timezone
from bson import ObjectId
from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, field_validator
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError
from app.dependencies.db import get_db
from app.utils.ids import new_uuid

router = APIRouter(prefix="/surveys", tags=["surveys"])

# ---------- Models ----------


class SubmitSurveyRequest(BaseModel):
    qrId: str
    name: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    answers: Dict[str, Any]  # JSON object of question -> answer

    @field_validator("qrId")
    @classmethod
    def qr_required(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("qrId is required")
        return v

    @field_validator("name")
    @classmethod
    def name_len(cls, v: str) -> str:
        v = v.strip()
        if not (3 <= len(v) <= 50):
            raise ValueError("name must be between 3 and 50 characters")
        return v

    @field_validator("phone")
    @classmethod
    def phone_digits(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        digits = re.sub(r"\D", "", v)
        if len(digits) != 10:
            raise ValueError("phone must be exactly 10 digits")
        return digits

    @field_validator("answers")
    @classmethod
    def answers_is_object(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        # Pydantic ensures dict already; additional sanity if needed
        return v


class SurveyItem(BaseModel):
    surveyId: str
    qrId: str
    sysId: str
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    answers: Dict[str, Any]
    submittedAt: datetime


# ---------- Helpers ----------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _get_or_create_user_by_qr(
    db: AsyncIOMotorDatabase, qr_id: str, name: str, email: Optional[str], phone: Optional[str]
) -> Dict[str, Any]:
    """
    Find a user by qrId; if not found, create one with a new sysId.
    Returns the user doc with at least sysId and qrId.
    Handles race via DuplicateKeyError retry.
    """
    users = db["users"]

    # Try find first
    user = await users.find_one(
        {"qrId": qr_id}, projection={"_id": 0, "sysId": 1, "qrId": 1, "name": 1, "email": 1, "phone": 1}
    )
    if user:
        # Optionally update name/email/phone if newly provided
        updates: Dict[str, Any] = {}
        if name and name.strip() and name.strip() != user.get("name", ""):
            updates["name"] = name.strip()
        if email:
            new_email = email.lower().strip()
            if new_email != (user.get("email") or ""):
                updates["email"] = new_email
        if phone:
            if phone != (user.get("phone") or ""):
                updates["phone"] = phone
        if updates:
            updates["updatedAt"] = _utcnow()
            try:
                await users.update_one({"qrId": qr_id}, {"$set": updates})
            except DuplicateKeyError:
                # Ignore conflicting sparse unique updates; keep existing user values
                pass
        # Re-read minimal fields
        user = await users.find_one({"qrId": qr_id}, projection={"_id": 0, "sysId": 1, "qrId": 1})
        return user

    # Create new user
    sys_id = new_uuid()
    now = _utcnow()
    doc = {
        "sysId": sys_id,
        "qrId": qr_id,
        "name": name.strip(),
        "email": email.lower().strip() if email else None,
        "phone": phone,
        "status": "active",
        "emailVerified": False,
        "phoneVerified": False,
        "createdAt": now,
        "updatedAt": now,
        "lastQuizSubmittedAt": None,
        "quizStats": {"totalQuizzes": 0, "totalCorrectAnswers": 0},
    }
    doc = {k: v for k, v in doc.items() if v is not None}
    try:
        await users.insert_one(doc)
        return {"sysId": sys_id, "qrId": qr_id}
    except DuplicateKeyError:
        # Another request created it concurrently—fetch and return
        existing = await users.find_one({"qrId": qr_id}, projection={"_id": 0, "sysId": 1, "qrId": 1})
        if existing:
            return existing
        # Fallback (shouldn't happen)
        raise


# ---------- Routes ----------

@router.post("/submit")
async def submit_survey(payload: SubmitSurveyRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    """
    Create (or find) the user by qrId and store a survey linked by sysId + qrId.
    """
    qr = payload.qrId.strip()
    # Ensure user exists and get sysId
    user = await _get_or_create_user_by_qr(db, qr, payload.name, payload.email, payload.phone)
    sys_id = user["sysId"]

    now = _utcnow()
    survey_doc = {
        "sysId": sys_id,
        "qrId": qr,
        "name": payload.name.strip(),
        "email": payload.email.lower().strip() if payload.email else None,
        "phone": payload.phone,
        "company": (payload.company or "").strip() or None,
        "answers": payload.answers,
        "submittedAt": now,
    }
    survey_doc = {k: v for k, v in survey_doc.items() if v is not None}

    res = await db["surveys"].insert_one(survey_doc)

    return {
        "status": "success",
        "message": "Survey submitted successfully",
        "data": {
            "surveyId": str(res.inserted_id),
            "sysId": sys_id,
            "qrId": qr,
        },
    }


@router.get("/by-qr/{qrId}")
async def list_surveys_by_qr(qrId: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    """
    Return all surveys for a given qrId (most recent first).
    """
    qr = qrId.strip()
    cursor = db["surveys"].find({"qrId": qr}).sort("submittedAt", -1)
    items = []
    async for s in cursor:
        items.append(
            SurveyItem(
                surveyId=str(s["_id"]),
                qrId=s["qrId"],
                sysId=s["sysId"],
                name=s.get("name", ""),
                email=s.get("email"),
                phone=s.get("phone"),
                company=s.get("company"),
                answers=s.get("answers", {}),
                submittedAt=s.get("submittedAt"),
            ).model_dump()
        )

    if not items:
        return {"status": "error", "message": "No surveys found for the provided qrId"}

    return {"status": "success", "data": items}
