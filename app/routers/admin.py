from __future__ import annotations
from typing import Optional, Any, Dict, List
import hashlib
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from motor.motor_asyncio import AsyncIOMotorDatabase
from app.dependencies.db import get_db

router = APIRouter(prefix="/admin/keys", tags=["admin-keys"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_plain_key(length_bytes: int = 24) -> str:
    """URL-safe random token for dashboard access."""
    return secrets.token_urlsafe(length_bytes)


class GenerateKeysRequest(BaseModel):
    count: int = 1
    label: Optional[str] = None

    @field_validator("count")
    @classmethod
    def valid_count(cls, v: int) -> int:
        if not (1 <= v <= 1000):
            raise ValueError("count must be between 1 and 1000")
        return v


class GenerateKeysResponseItem(BaseModel):
    key: str
    label: Optional[str] = None
    createdAt: datetime


class ValidateKeyRequest(BaseModel):
    key: str


class ValidateKeyResponse(BaseModel):
    valid: bool


@router.post("")
async def generate_keys(payload: GenerateKeysRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    """
    Generate one or more dashboard access keys (plaintext returned once).
    Keys are stored hashed; no expiry / usage limits are enforced.
    """
    keys = db["keys"]
    now = _utcnow()

    out: List[Dict[str, Any]] = []
    for _ in range(payload.count):
        plain = _new_plain_key()
        doc = {
            "hash": _sha256_hex(plain),
            "label": (payload.label or None),
            "createdAt": now,
        }
        try:
            await keys.insert_one(doc)
            out.append(
                {"key": plain, "label": payload.label, "createdAt": now})
        except Exception:
            # Extremely unlikely hash collision—skip this one
            continue

    return {"status": "success", "message": "Keys generated successfully", "data": out}


@router.post("/validate")
async def validate_key(payload: ValidateKeyRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    """
    Validate a dashboard key: returns success if the key's hash exists in DB.
    """
    keys = db["keys"]
    h = _sha256_hex(payload.key)

    doc = await keys.find_one({"hash": h}, projection={"_id": 1})
    if not doc:
        return {"status": "error", "message": "Invalid key"}

    resp = ValidateKeyResponse(valid=True).model_dump()
    return {"status": "success", "message": "Key validated", "data": resp}
