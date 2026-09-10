from pymongo import ReturnDocument
from app.extensions import get_db


def get_next_sequence(sequence_name):
    """Atomically consume and return the next number in a sequence.

    Only call this when a number is actually being persisted — calling it to
    pre-fill a form burns numbers on every page view and leaves gaps.
    """
    db = get_db()
    counter = db.counters.find_one_and_update(
        {"_id": sequence_name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    return counter["seq"]


def peek_next_sequence(sequence_name):
    """Return what the next number *would* be, without consuming it.

    Used for form previews. Two users can be shown the same preview, so the
    value is never authoritative — the POST handler reallocates on collision.
    """
    db = get_db()
    counter = db.counters.find_one({"_id": sequence_name})
    return (counter.get("seq", 0) if counter else 0) + 1
