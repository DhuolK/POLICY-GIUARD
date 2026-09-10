import logging
from pymongo.errors import PyMongoError, ConfigurationError
from app.extensions import get_client

logger = logging.getLogger(__name__)

def run_transaction(func, *args, **kwargs):
    """
    Executes a function within a MongoDB transaction if transactions are supported
    by the underlying MongoDB deployment (replica set or sharded cluster).
    Falls back to normal execution if transactions are not supported.
    """
    client = get_client()
    if not client:
        # Fallback if client is not initialized yet
        return func(None, *args, **kwargs)
        
    try:
        with client.start_session() as session:
            with session.start_transaction():
                # The callback func must accept session as its first parameter
                result = func(session, *args, **kwargs)
                return result
    except PyMongoError as e:
        err_msg = str(e).lower()
        # If transaction is not supported (e.g. standalone server), fall back gracefully
        if "transaction" in err_msg or "replica set" in err_msg or "sessions are not supported" in err_msg or "session" in err_msg:
            logger.warning("MongoDB deployment does not support transactions. Falling back to non-transactional execution. Error: %s", e)
            return func(None, *args, **kwargs)
        else:
            logger.error("Transaction aborted due to PyMongoError: %s", e)
            raise
