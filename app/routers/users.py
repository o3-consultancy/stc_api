from datetime import timedelta
from typing import Optional, Any, Dict
import re
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, field_validator
from pymongo.errors import DuplicateKeyError
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.dependencies.db import get_db
from app.utils.ids import new_uuid
# imported even if decorators are commented
from app.middleware.auth import public
from datetime import date, time
from fastapi import Query

router = APIRouter(prefix="/users", tags=["users"])

# ---- Models (kept within router as requested) ----


class RegisterUserRequest(BaseModel):
    name: str
    qrId: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_len(cls, v: str) -> str:
        v = v.strip()
        if not (3 <= len(v) <= 50):
            raise ValueError("name must be between 3 and 50 characters")
        return v

    @field_validator("qrId")
    @classmethod
    def qr_required(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("qrId is required")
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


class UserResponseData(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    sysId: str
    qrId: str


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
        # end is inclusive for the calendar date -> add one day and use exclusive upper bound
        end_dt = datetime.combine(
            end_date, time.min, tzinfo=timezone.utc) + timedelta(days=1)
    return start_dt, end_dt


# also ADD THIS import near the top with other datetime imports


# ---- Routes ----

@router.get("/list")
async def list_users(
    startDate: date = Query(..., description="YYYY-MM-DD"),
    endDate: date | None = Query(None, description="YYYY-MM-DD"),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    List users filtered by createdAt date (UTC), sorted by most recent first.
    - Only startDate: returns that single day.
    - startDate + endDate: inclusive date range.
    """
    try:
        start_dt, end_dt = _date_bounds(startDate, endDate)
    except ValueError as e:
        return {"status": "error", "message": str(e)}

    cursor = (
        db["users"]
        .find(
            {"createdAt": {"$gte": start_dt, "$lt": end_dt}},
            projection={"_id": 0, "name": 1, "email": 1,
                        "phone": 1, "sysId": 1, "qrId": 1, "createdAt": 1},
        )
        .sort("createdAt", -1)
    )

    items = [doc async for doc in cursor]
    return {"status": "success", "data": items}


@router.post("/register")
# @public
async def register_user(payload: RegisterUserRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    users = db["users"]
    sys_id = new_uuid()
    now = _utcnow()

    doc: Dict[str, Any] = {
        "sysId": sys_id,
        "qrId": payload.qrId.strip(),
        "name": payload.name.strip(),
        "email": payload.email.lower().strip() if payload.email else None,
        "phone": payload.phone,
        "status": "active",
        "emailVerified": False,
        "phoneVerified": False,
        "createdAt": now,
        "updatedAt": now,
        "lastQuizSubmittedAt": None,
        "quizStats": {"totalQuizzes": 0, "totalCorrectAnswers": 0},
    }
    # drop None for sparse uniques
    doc = {k: v for k, v in doc.items() if v is not None}

    try:
        await users.insert_one(doc)
    except DuplicateKeyError as e:
        msg = "Duplicate value"
        s = str(e)
        if "uq_qrId" in s:
            msg = "qrId already registered"
        elif "uq_email_sparse" in s:
            msg = "email already registered"
        elif "uq_phone_sparse" in s:
            msg = "phone already registered"
        return {"status": "error", "message": msg}

    return {
        "status": "success",
        "message": "User created successfully",
        "systemUserId": sys_id,
        "qrId": doc["qrId"],
    }


@router.get("/by-qr/{qrId}")
# @public
async def get_user_by_qr(qrId: str, db: AsyncIOMotorDatabase = Depends(get_db)):
    users = db["users"]
    doc = await users.find_one(
        {"qrId": qrId.strip()},
        projection={"_id": 0, "name": 1, "email": 1,
                    "phone": 1, "sysId": 1, "qrId": 1},
    )
    if not doc:
        return {"status": "error", "message": "User not found"}
    return {"status": "success", "data": doc}
