import uuid


def new_uuid() -> str:
    """Return a random UUID4 as a 32-char hex string."""
    return uuid.uuid4().hex
