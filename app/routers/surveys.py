from typing import Optional, Any, Dict, Literal
import re
from datetime import datetime, timezone, date, time, timedelta
from bson import ObjectId
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, field_validator
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError
from app.dependencies.db import get_db
from app.utils.ids import new_uuid

router = APIRouter(prefix="/surveys", tags=["surveys"])

# ---------- Models ----------

Interest = Literal["Smart Finance", "Bueniss Portal Service", "None"]


class SubmitSurveyRequest(BaseModel):
    qrId: str
    name: str
    company: Optional[str] = None
    phoneCountryCode: str
    phoneNumber: str
    interest: Interest
    thoughtsOnStc: Optional[str] = None
    answers: Dict[str, Any]  # JSON object of question -> answer

    @field_validator("qrId")
    @classmethod
    def qr_required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("qrId is required")
        return v

    @field_validator("name")
    @classmethod
    def name_len(cls, v: str) -> str:
        v = (v or "").strip()
        if not (3 <= len(v) <= 50):
            raise ValueError("name must be between 3 and 50 characters")
        return v

    @field_validator("phoneCountryCode")
    @classmethod
    def cc_valid(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("phoneCountryCode is required")
        # allow "+971" or "971" -> store with leading '+'
        m = re.fullmatch(r"\+?\d{1,3}", v)
        if not m:
            raise ValueError(
                "phoneCountryCode must be 1-3 digits, optionally prefixed with +")
        if not v.startswith("+"):
            v = "+" + v
        return v

    @field_validator("phoneNumber")
    @classmethod
    def num_valid(cls, v: str) -> str:
        v = re.sub(r"\D", "", (v or ""))
        # E.164 total length up to 15; accept 4-15 digits in local number
        if not (4 <= len(v) <= 15):
            raise ValueError("phoneNumber must be 4-15 digits")
        return v

    @field_validator("thoughtsOnStc")
    @classmethod
    def blurb_max(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        v = v.strip()
        if len(v) > 140:
            raise ValueError("thoughtsOnStc must be at most 140 characters")
        return v

    @field_validator("answers")
    @classmethod
    def answers_is_object(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        return v


class SurveyItem(BaseModel):
    surveyId: str
    qrId: str
    sysId: str
    name: str
    company: Optional[str] = None
    phoneCountryCode: str
    phoneNumber: str
    phoneE164: str
    interest: Interest
    raffleEligible: bool
    # NOTE: we *store* datetime in Mongo, but *return* date here:
    raffleDate: Optional[date] = None
    thoughtsOnStc: Optional[str] = None
    answers: Dict[str, Any]
    submittedAt: datetime


# ---------- Helpers ----------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _today_utc_midnight() -> datetime:
    """Return today's date at 00:00:00 UTC as a datetime (Mongo-safe)."""
    d = datetime.now(timezone.utc).date()
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


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


def _to_e164(cc: str, number: str) -> str:
    cc = cc.strip()
    if not cc.startswith("+"):
        cc = "+" + cc
    digits = re.sub(r"\D", "", number or "")
    return f"{cc}{digits}"


async def _get_or_create_user_by_qr(
    db: AsyncIOMotorDatabase,
    qr_id: str,
    name: str,
    company: Optional[str],
    phone_cc: str,
    phone_num: str,
    phone_e164: str,
) -> Dict[str, Any]:
    """
    Find a user by qrId; if not found, create one with a new sysId.
    Enforce uniqueness on phoneE164 across users.
    Returns doc {"sysId","qrId"} on success.
    """
    users = db["users"]

    # If user exists by QR, make sure phone is either unset or matches
    existing_by_qr = await users.find_one({"qrId": qr_id}, projection={"_id": 0, "sysId": 1, "phoneE164": 1})
    if existing_by_qr:
        # If phone on file conflicts, reject
        if existing_by_qr.get("phoneE164") and existing_by_qr["phoneE164"] != phone_e164:
            raise ValueError("phone already registered to a different user")
        # Optionally backfill company/name/phone
        updates: Dict[str, Any] = {}
        if company:
            updates["company"] = company.strip()
        if name:
            updates["name"] = name.strip()
        if phone_e164 and not existing_by_qr.get("phoneE164"):
            updates.update({"phoneCountryCode": phone_cc,
                           "phoneNumber": phone_num, "phoneE164": phone_e164})
        if updates:
            updates["updatedAt"] = _utcnow()
            try:
                await users.update_one({"qrId": qr_id}, {"$set": updates})
            except DuplicateKeyError:
                pass
        return {"sysId": existing_by_qr["sysId"], "qrId": qr_id}

    # Enforce uniqueness by phoneE164
    conflict = await users.find_one({"phoneE164": phone_e164}, projection={"_id": 1})
    if conflict:
        raise ValueError("phone already registered")

    # Create new user
    sys_id = new_uuid()
    now = _utcnow()
    user_doc = {
        "sysId": sys_id,
        "qrId": qr_id,
        "name": name.strip(),
        "company": (company or "").strip() or None,
        "phoneCountryCode": phone_cc,
        "phoneNumber": phone_num,
        "phoneE164": phone_e164,
        "status": "active",
        "createdAt": now,
        "updatedAt": now,
        "lastQuizSubmittedAt": None,
        "quizStats": {"totalQuizzes": 0, "totalCorrectAnswers": 0},
    }
    try:
        await users.insert_one(user_doc)
    except DuplicateKeyError as e:
        msg = "Duplicate value"
        s = str(e)
        if "qrId" in s:
            msg = "qrId already registered"
        elif "phoneE164" in s:
            msg = "phone already registered"
        raise ValueError(msg)

    return {"sysId": sys_id, "qrId": qr_id}


# ---------- Routes ----------

@router.get("/validate-phone")
async def validate_phone(
    cc: str = Query(..., description="Country code, e.g. +971 or 971"),
    number: str = Query(..., description="Local/national number"),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Validate whether a **survey already exists** for this phone (E.164).
    """
    # Normalize & basic validation
    cc = SubmitSurveyRequest.cc_valid(cc)  # reuse validator logic
    number = SubmitSurveyRequest.num_valid(number)
    e164 = _to_e164(cc, number)

    exists = await db["surveys"].find_one({"phoneE164": e164}, projection={"_id": 1}) is not None
    return {"status": "success", "exists": exists}


@router.get("/list")
async def list_surveys(
    startDate: date = Query(..., description="YYYY-MM-DD"),
    endDate: date | None = Query(None, description="YYYY-MM-DD"),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    List surveys filtered by submittedAt date (UTC), sorted by most recent first.
    """
    try:
        start_dt, end_dt = _date_bounds(startDate, endDate)
    except ValueError as e:
        return {"status": "error", "message": str(e)}

    projection = {
        "_id": 1,
        "qrId": 1,
        "sysId": 1,
        "name": 1,
        "company": 1,
        "phoneCountryCode": 1,
        "phoneNumber": 1,
        "phoneE164": 1,
        "interest": 1,
        "raffleEligible": 1,
        "raffleDate": 1,  # stored as datetime at midnight UTC
        "thoughtsOnStc": 1,
        "answers": 1,
        "submittedAt": 1,
    }

    cursor = db["surveys"].find(
        {"submittedAt": {"$gte": start_dt, "$lt": end_dt}},
        projection=projection
    ).sort("submittedAt", -1)

    items = []
    async for s in cursor:
        # Convert stored datetime -> date for API response
        raffle_dt = s.get("raffleDate")
        raffle_date: Optional[date] = raffle_dt.date(
        ) if isinstance(raffle_dt, datetime) else None

        items.append(
            SurveyItem(
                surveyId=str(s["_id"]),
                qrId=s.get("qrId"),
                sysId=s.get("sysId"),
                name=s.get("name"),
                company=s.get("company"),
                phoneCountryCode=s.get("phoneCountryCode"),
                phoneNumber=s.get("phoneNumber"),
                phoneE164=s.get("phoneE164"),
                interest=s.get("interest"),
                raffleEligible=bool(s.get("raffleEligible")),
                raffleDate=raffle_date,
                thoughtsOnStc=s.get("thoughtsOnStc"),
                answers=s.get("answers", {}),
                submittedAt=s.get("submittedAt"),
            ).model_dump()
        )

    return {"status": "success", "data": items}


@router.post("/submit")
async def submit_survey(payload: SubmitSurveyRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    """
    Create (or find) the user by qrId and store a survey linked by sysId + qrId.
    Enforces:
    - unique phoneE164 (system-wide)
    - only **one survey** per phone (checked in `surveys`)
    """
    qr = payload.qrId.strip()
    cc = payload.phoneCountryCode
    num = payload.phoneNumber
    e164 = _to_e164(cc, num)

    # Block if a survey already exists for this phone
    exists = await db["surveys"].find_one({"phoneE164": e164}, projection={"_id": 1})
    if exists:
        return {"status": "error", "message": "A survey has already been submitted for this phone number"}

    # Create/find user (also enforces phone uniqueness across users)
    try:
        user_ref = await _get_or_create_user_by_qr(
            db,
            qr_id=qr,
            name=payload.name,
            company=payload.company,
            phone_cc=cc,
            phone_num=num,
            phone_e164=e164,
        )
    except ValueError as ve:
        return {"status": "error", "message": str(ve)}

    sys_id = user_ref["sysId"]
    now = _utcnow()

    raffle_eligible = payload.interest == "None"
    survey_doc = {
        "sysId": sys_id,
        "qrId": qr,
        "name": payload.name.strip(),
        "company": (payload.company or "").strip() or None,
        "phoneCountryCode": cc,
        "phoneNumber": num,
        "phoneE164": e164,
        "interest": payload.interest,
        "raffleEligible": raffle_eligible,
        "raffleDate": _today_utc_midnight() if raffle_eligible else None,  # <-- store DATETIME
        "thoughtsOnStc": payload.thoughtsOnStc,
        "answers": payload.answers,
        "submittedAt": now,
    }

    res = await db["surveys"].insert_one(survey_doc)

    return {
        "status": "success",
        "message": "Survey submitted successfully",
        "data": {"surveyId": str(res.inserted_id), "sysId": sys_id, "qrId": qr},
    }


@router.get("/by-qr/{qrId}")
async def list_surveys_by_qr(qrId: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    """
    Return all surveys for a given qrId (most recent first).
    """
    qr = (qrId or "").strip()
    cursor = db["surveys"].find({"qrId": qr}).sort("submittedAt", -1)
    items = []
    async for s in cursor:
        raffle_dt = s.get("raffleDate")
        raffle_date: Optional[date] = raffle_dt.date(
        ) if isinstance(raffle_dt, datetime) else None

        items.append(
            SurveyItem(
                surveyId=str(s["_id"]),
                qrId=s["qrId"],
                sysId=s["sysId"],
                name=s.get("name", ""),
                company=s.get("company"),
                phoneCountryCode=s.get("phoneCountryCode"),
                phoneNumber=s.get("phoneNumber"),
                phoneE164=s.get("phoneE164"),
                interest=s.get("interest"),
                raffleEligible=bool(s.get("raffleEligible")),
                raffleDate=raffle_date,
                thoughtsOnStc=s.get("thoughtsOnStc"),
                answers=s.get("answers", {}),
                submittedAt=s.get("submittedAt"),
            ).model_dump()
        )

    if not items:
        return {"status": "error", "message": "No surveys found for the provided qrId"}

    return {"status": "success", "data": items}
