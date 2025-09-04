from typing import Optional, Tuple
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from app.core.config import get_settings

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
    # users
    await db["users"].create_index("qrId", unique=True, name="uq_qrId")
    await db["users"].create_index("sysId", unique=True, name="uq_sysId")
    await db["users"].create_index("email", unique=True, sparse=True, name="uq_email_sparse")
    await db["users"].create_index("phone", unique=True, sparse=True, name="uq_phone_sparse")

    # quiz_results
    await db["quiz_results"].create_index([("qrId", 1), ("submittedAt", -1)], name="qr_ts")

    # outbox
    await db["outbox"].create_index([("status", 1), ("createdAt", 1)], name="status_created")
    await db["outbox"].create_index([("topic", 1), ("status", 1)], name="topic_status")


async def close_mongo_connection() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


async def get_db() -> AsyncIOMotorDatabase:
    """FastAPI dependency to inject DB."""
    _, db = await connect_to_mongo()
    return db
