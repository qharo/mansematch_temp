# app/database.py
import os
import logging
from typing import Optional
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.collection import Collection
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# Read environment variables once at module load for consistency
MONGO_URI_FROM_ENV: Optional[str] = os.getenv("MONGO_URI")
MONGO_DB_NAME_FROM_ENV: Optional[str] = os.getenv("MONGO_DB_NAME")
MONGO_REPORTS_COLLECTION_FROM_ENV: Optional[str] = os.getenv("MONGO_REPORTS_COLLECTION")

mongo_client: Optional[MongoClient] = None
db: Optional[Database] = None
reports_collection: Optional[Collection] = None

def connect_to_mongo():
    global mongo_client, db, reports_collection

    if reports_collection is not None:
        logger.debug("MongoDB connection already established.")
        return

    # Use the module-level variables
    current_mongo_uri = MONGO_URI_FROM_ENV
    current_mongo_db_name = MONGO_DB_NAME_FROM_ENV
    current_mongo_reports_collection = MONGO_REPORTS_COLLECTION_FROM_ENV

    # --- Enhanced Logging ---
    logger.info(f"Raw MONGO_URI from env (repr): {repr(current_mongo_uri)}")
    logger.info(f"Raw MONGO_DB_NAME from env (repr): {repr(current_mongo_db_name)}")
    logger.info(f"Raw MONGO_REPORTS_COLLECTION from env (repr): {repr(current_mongo_reports_collection)}")
    # --- End Enhanced Logging ---

    if not current_mongo_uri or not current_mongo_db_name or not current_mongo_reports_collection:
        logger.critical(
            "MongoDB environment variables not fully set. MongoDB integration will be disabled."
        )
        if not current_mongo_uri: logger.warning("MONGO_URI is missing or empty in environment.")
        if not current_mongo_db_name: logger.warning("MONGO_DB_NAME is missing or empty in environment.")
        if not current_mongo_reports_collection: logger.warning("MONGO_REPORTS_COLLECTION is missing or empty in environment.")
        return

    # Defensive stripping of whitespace from URI
    # This is a good practice regardless of the current issue.
    effective_mongo_uri = current_mongo_uri.strip()
    if effective_mongo_uri != current_mongo_uri:
        logger.warning(f"MONGO_URI had leading/trailing whitespace. Original (repr): {repr(current_mongo_uri)}, Stripped (repr): {repr(effective_mongo_uri)}")
    else:
        logger.info("MONGO_URI has no leading/trailing whitespace detected by strip().")


    try:
        log_uri_display = effective_mongo_uri # Use the stripped URI for display logic
        if "@" in log_uri_display and ("mongodb://" in log_uri_display or "mongodb+srv://" in log_uri_display):
            parts = log_uri_display.split("@", 1)
            scheme_user_pass = parts[0].split("://", 1)
            if len(scheme_user_pass) == 2:
                scheme, user_pass_str = scheme_user_pass
                if ":" in user_pass_str:
                    user = user_pass_str.split(":", 1)[0]
                    log_uri_display = f"{scheme}://{user}:<PASSWORD>@{parts[1]}"
                else:
                    log_uri_display = f"{scheme}://<USER>@{parts[1]}"

        logger.info(f"Attempting to connect to MongoDB. Obfuscated URI for log: '{log_uri_display}', DB: '{current_mongo_db_name}'")

        # Use the potentially stripped URI for connection
        mongo_client = MongoClient(effective_mongo_uri, serverSelectionTimeoutMS=5000)
        mongo_client.admin.command('ping')

        db = mongo_client[current_mongo_db_name]
        reports_collection = db[current_mongo_reports_collection]

        logger.info(
            f"Successfully connected to MongoDB. Database: {current_mongo_db_name}, "
            f"Collection: {current_mongo_reports_collection}"
        )
    except Exception as e:
        # Log the repr of the URI that actually caused the failure
        logger.error(f"Failed to connect to MongoDB. URI used for connection (repr): {repr(effective_mongo_uri)}. Error: {e}. MongoDB integration will be disabled.")
        mongo_client = None
        db = None
        reports_collection = None

def close_mongo_connection():
    global mongo_client, db, reports_collection # Added db and reports_collection here
    if mongo_client:
        mongo_client.close()
        mongo_client = None
        db = None # Reset db
        reports_collection = None # Reset reports_collection
        logger.info("MongoDB connection closed.")
