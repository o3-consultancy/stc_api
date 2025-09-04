"""
Simple outbox pattern helpers.

- enqueue_outbox(db, topic, payload): store an event to be processed later.
- process_outbox_batch(db, handler, limit=20): pull PENDING events, call handler(event), mark DONE/FAILED.
- A minimal outbox_log is kept for auditing.

You can wire `process_outbox_batch` to a background task / Cloud Run job / cron.
"""

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

STATUS_PENDING = "PENDING"
STATUS_PROCESSING = "PROCESSING"
STATUS_DONE = "DONE"
STATUS_FAILED = "FAILED"


def _utcnow():
    return datetime.now(timezone.utc)


async def enqueue_outbox(db: AsyncIOMotorDatabase, topic: str, payload: Dict[str, Any]) -> str:
    res = await db["outbox"].insert_one(
        {
            "topic": topic,
            "payload": payload,
            "status": STATUS_PENDING,
            "createdAt": _utcnow(),
            "lastUpdatedAt": _utcnow(),
            "attempts": 0,
        }
    )
    return str(res.inserted_id)


async def process_outbox_batch(
    db: AsyncIOMotorDatabase,
    handler: Callable[[Dict[str, Any]], "Any"],
    limit: int = 20,
) -> Dict[str, int]:
    """
    Pull a small batch of pending events, process with handler(event),
    mark DONE/FAILED, and append to outbox_log.
    """
    processed = {"done": 0, "failed": 0}

    for _ in range(limit):
        # Atomically claim one event
        evt = await db["outbox"].find_one_and_update(
            {"status": STATUS_PENDING},
            {
                "$set": {"status": STATUS_PROCESSING, "lastUpdatedAt": _utcnow()},
                "$inc": {"attempts": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if not evt:
            break

        try:
            await maybe_await(handler(evt))
            await db["outbox"].update_one(
                {"_id": evt["_id"]},
                {"$set": {"status": STATUS_DONE, "lastUpdatedAt": _utcnow()}},
            )
            await db["outbox_log"].insert_one(
                {
                    "outboxId": evt["_id"],
                    "topic": evt["topic"],
                    "status": STATUS_DONE,
                    "at": _utcnow(),
                }
            )
            processed["done"] += 1
        except Exception as e:
            await db["outbox"].update_one(
                {"_id": evt["_id"]},
                {
                    "$set": {
                        "status": STATUS_FAILED,
                        "lastUpdatedAt": _utcnow(),
                        "error": repr(e),
                    }
                },
            )
            await db["outbox_log"].insert_one(
                {
                    "outboxId": evt["_id"],
                    "topic": evt["topic"],
                    "status": STATUS_FAILED,
                    "error": repr(e),
                    "at": _utcnow(),
                }
            )
            processed["failed"] += 1

    return processed


async def maybe_await(x):
    if hasattr(x, "__await__"):
        return await x
    return x
