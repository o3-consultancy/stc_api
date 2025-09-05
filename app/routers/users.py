from typing import Optional, Any, Dict
import re
from datetime import datetime, timezone, date, time, timedelta
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, field_validator
from pymongo.errors import DuplicateKeyError
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.dependencies.db import get_db
from app.utils.ids import new_uuid

router = APIRouter(prefix="/users", tags=["users"])

# ---- Models ----


class RegisterUserRequest(BaseModel):
    name: str
    qrId: str
    company: Optional[str] = None
    phoneCountryCode: str
    phoneNumber: str

    @field_validator("name")
    @classmethod
    def name_len(cls, v: str) -> str:
        v = (v or "").strip()
        if not (3 <= len(v) <= 50):
            raise ValueError("name must be between 3 and 50 characters")
        return v

    @field_validator("qrId")
    @classmethod
    def qr_required(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("qrId is required")
        return v

    @field_validator("phoneCountryCode")
    @classmethod
    def cc_valid(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("phoneCountryCode is required")
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
        if not (4 <= len(v) <= 15):
            raise ValueError("phoneNumber must be 4-15 digits")
        return v


class UserResponseData(BaseModel):
    name: str
    company: Optional[str] = None
    phoneCountryCode: str
    phoneNumber: str
    phoneE164: str
    sysId: str
    qrId: str


# ---- Helpers ----

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_e164(cc: str, number: str) -> str:
    cc = cc.strip()
    if not cc.startswith("+"):
        cc = "+" + cc
    digits = re.sub(r"\D", "", number or "")
    return f"{cc}{digits}"


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


# ---- Routes ----

@router.get("/list")
async def list_users(
    startDate: date = Query(..., description="YYYY-MM-DD"),
    endDate: date | None = Query(None, description="YYYY-MM-DD"),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    List users filtered by createdAt date (UTC), sorted by most recent first.
    """
    try:
        start_dt, end_dt = _date_bounds(startDate, endDate)
    except ValueError as e:
        return {"status": "error", "message": str(e)}

    cursor = (
        db["users"]
        .find(
            {"createdAt": {"$gte": start_dt, "$lt": end_dt}},
            projection={
                "_id": 0,
                "name": 1,
                "company": 1,
                "phoneCountryCode": 1,
                "phoneNumber": 1,
                "phoneE164": 1,
                "sysId": 1,
                "qrId": 1,
                "createdAt": 1,
            },
        )
        .sort("createdAt", -1)
    )

    items = [doc async for doc in cursor]
    return {"status": "success", "data": items}


@router.post("/register")
async def register_user(payload: RegisterUserRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    """
    Manual user registration (normally created via survey).
    Enforces uniqueness for qrId and phoneE164.
    """
    users = db["users"]
    sys_id = new_uuid()
    now = _utcnow()

    cc = payload.phoneCountryCode
    num = payload.phoneNumber
    e164 = _to_e164(cc, num)

    # Pre-check uniqueness
    if await users.find_one({"qrId": payload.qrId.strip()}, projection={"_id": 1}):
        return {"status": "error", "message": "qrId already registered"}
    if await users.find_one({"phoneE164": e164}, projection={"_id": 1}):
        return {"status": "error", "message": "phone already registered"}

    doc: Dict[str, Any] = {
        "sysId": sys_id,
        "qrId": payload.qrId.strip(),
        "name": payload.name.strip(),
        "company": (payload.company or "").strip() or None,
        "phoneCountryCode": cc,
        "phoneNumber": num,
        "phoneE164": e164,
        "status": "active",
        "createdAt": now,
        "updatedAt": now,
        "lastQuizSubmittedAt": None,
        "quizStats": {"totalQuizzes": 0, "totalCorrectAnswers": 0},
    }

    try:
        await users.insert_one(doc)
    except DuplicateKeyError as e:
        s = str(e)
        if "qrId" in s:
            msg = "qrId already registered"
        elif "phoneE164" in s:
            msg = "phone already registered"
        else:
            msg = "Duplicate value"
        return {"status": "error", "message": msg}

    return {"status": "success", "message": "User created successfully", "systemUserId": sys_id, "qrId": doc["qrId"]}


@router.get("/by-qr/{qrId}")
async def get_user_by_qr(qrId: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    users = db["users"]
    doc = await users.find_one(
        {"qrId": qrId.strip()},
        projection={
            "_id": 0,
            "name": 1,
            "company": 1,
            "phoneCountryCode": 1,
            "phoneNumber": 1,
            "phoneE164": 1,
            "sysId": 1,
            "qrId": 1,
        },
    )
    if not doc:
        return {"status": "error", "message": "User not found"}
    return {"status": "success", "data": doc}
