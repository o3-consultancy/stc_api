from typing import Optional, Tuple
import logging

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import OperationFailure, PyMongoError

from app.core.config import get_settings

logger = logging.getLogger("db")
logger.setLevel(logging.INFO)

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


async def connect_to_mongo() -> Tuple[AsyncIOMotorClient, AsyncIOMotorDatabase]:
    """Create a global Motor client & DB, ensure indexes (idempotent)."""
    global _client, _db
    if _client is not None and _db is not None:
        return _client, _db

    settings = get_settings()
    if not settings.MONGO_URI.startswith(("mongodb://", "mongodb+srv://")):
        raise RuntimeError(
            "MONGO_URI must start with mongodb:// or mongodb+srv://")

    _client = AsyncIOMotorClient(
        settings.MONGO_URI,
        serverSelectionTimeoutMS=10_000,
        uuidRepresentation="standard",
    )

    # Connectivity check
    await _client.admin.command("ping")

    _db = _client[settings.MONGO_DB]

    if settings.DB_CREATE_INDEXES:
        await _ensure_indexes(_db)

    return _client, _db


async def _ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """
    Create/ensure all required indexes for the current schema.
    Safe to run repeatedly.
    """
    try:
        # -------------------------
        # users
        # -------------------------
        users = db["users"]
        await users.create_index([("qrId", ASCENDING)], unique=True, name="uq_qrId")
        await users.create_index([("sysId", ASCENDING)], unique=True, name="uq_sysId")
        # New phone model: unique E.164 for global uniqueness
        await users.create_index([("phoneE164", ASCENDING)], unique=True, name="uq_phone_e164")
        # Helpful for list views by date
        await users.create_index([("createdAt", DESCENDING)], name="ix_users_createdAt")

        # -------------------------
        # surveys
        # -------------------------
        surveys = db["surveys"]
        # Fast date filtering & ordering
        await surveys.create_index([("submittedAt", DESCENDING)], name="ix_surveys_submittedAt")
        # Common lookups
        await surveys.create_index([("qrId", ASCENDING), ("submittedAt", DESCENDING)], name="ix_surveys_qr_submittedAt")
        await surveys.create_index([("sysId", ASCENDING), ("submittedAt", DESCENDING)], name="ix_surveys_sys_submittedAt")
        await surveys.create_index([("phoneE164", ASCENDING)], name="ix_surveys_phone_e164")
        await surveys.create_index([("company", ASCENDING), ("submittedAt", DESCENDING)], name="ix_surveys_company_submittedAt")

        # -------------------------
        # quiz_results
        # -------------------------
        quiz = db["quiz_results"]
        await quiz.create_index([("submittedAt", DESCENDING)], name="ix_quiz_submittedAt")
        # Enforce single submission per qrId at DB level (falls back to non-unique if legacy duplicates exist)
        try:
            await quiz.create_index([("qrId", ASCENDING)], unique=True, name="uq_quiz_qrId")
        except OperationFailure as e:
            logger.warning(
                "Could not create unique index uq_quiz_qrId (legacy duplicates?). "
                "Falling back to non-unique. Error: %s",
                e,
            )
            await quiz.create_index([("qrId", ASCENDING)], name="ix_quiz_qrId")

        # (Legacy composite kept for backwards compatibility if you relied on it)
        # await quiz.create_index([("qrId", ASCENDING), ("submittedAt", DESCENDING)], name="qr_ts")

        # -------------------------
        # keys (admin dashboard access keys)
        # -------------------------
        keys = db["keys"]
        # Existing design uses a 'hash' field; keep unique here.
        await keys.create_index([("hash", ASCENDING)], unique=True, name="uq_hash")
        await keys.create_index([("label", ASCENDING)], name="ix_keys_label")
        await keys.create_index([("createdAt", DESCENDING)], name="ix_keys_createdAt")

        # -------------------------
        # outbox (if used by services/outbox.py)
        # -------------------------
        outbox = db["outbox"]
        await outbox.create_index([("status", ASCENDING), ("createdAt", ASCENDING)], name="ix_outbox_status_created")
        await outbox.create_index([("topic", ASCENDING), ("status", ASCENDING)], name="ix_outbox_topic_status")

        logger.info("MongoDB indexes ensured successfully.")
    except PyMongoError as e:
        logger.error("Error while creating MongoDB indexes: %s", e)


async def close_mongo_connection() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


async def get_db() -> AsyncIOMotorDatabase:
    """FastAPI dependency to inject DB."""
    _, db = await connect_to_mongo()
    return db
