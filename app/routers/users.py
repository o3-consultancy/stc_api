from typing import Optional, Any, Dict
import re
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, field_validator
from pymongo.errors import DuplicateKeyError
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.dependencies.db import get_db
from app.utils.ids import new_uuid
from app.middleware.auth import public

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


# ---- Routes ----

@router.post("/register")
@public
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

    # NOTE: Returning message per your spec (typo preserved if needed)
    return {
        "status": "success",
        "message": "User cerated successfully",
        "systemUserId": sys_id,
        "qrId": doc["qrId"],
    }


@router.get("/by-qr/{qrId}")
@public
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
