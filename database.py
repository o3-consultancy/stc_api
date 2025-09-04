import os
import asyncio
from datetime import datetime, timezone
from typing import Tuple

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("DB_NAME", "stc-api")

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


async def connect_to_mongo() -> Tuple[AsyncIOMotorClient, AsyncIOMotorDatabase]:
    global _client, _db
    if _client and _db:
        return _client, _db

    if not MONGODB_URI:
        raise RuntimeError("MONGODB_URI is not set")

    _client = AsyncIOMotorClient(
        MONGODB_URI,
        serverSelectionTimeoutMS=8000,
        uuidRepresentation="standard",
    )

    # Verify connectivity
    await _client.admin.command("ping")

    _db = _client[DB_NAME]

    # Ensure indexes (idempotent)
    await _db["users"].create_index("qrId", unique=True, name="uq_qrId")
    await _db["users"].create_index("sysId", unique=True, name="uq_sysId")
    # Sparse unique for optional fields
    await _db["users"].create_index("email", unique=True, sparse=True, name="uq_email_sparse")
    await _db["users"].create_index("phone", unique=True, sparse=True, name="uq_phone_sparse")

    await _db["quiz_results"].create_index([("qrId", 1), ("submittedAt", -1)], name="qr_ts")

    return _client, _db


async def close_mongo_connection() -> None:
    global _client
    if _client:
        _client.close()
        _client = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
