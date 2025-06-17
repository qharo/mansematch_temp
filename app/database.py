# app/database.py
import os
import logging
from typing import Optional

from pymongo import MongoClient
from pymongo.database import Database
from pymongo.collection import Collection
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Logging Setup ---
# The logger will inherit basicConfig from main.py if this module is imported after basicConfig is set.
# For standalone testing or different logging, configure it here.
logger = logging.getLogger(__name__)

# --- MongoDB Configuration ---
MONGO_URI: Optional[str] = os.getenv("MONGO_URI")
MONGO_DB_NAME: Optional[str] = os.getenv("MONGO_DB_NAME")
MONGO_REPORTS_COLLECTION: Optional[str] = os.getenv("MONGO_REPORTS_COLLECTION")

mongo_client: Optional[MongoClient] = None
db: Optional[Database] = None
reports_collection: Optional[Collection] = None

def connect_to_mongo():
    """
    Establishes a connection to MongoDB and initializes the db and reports_collection.
    """
    global mongo_client, db, reports_collection

    if reports_collection is not None: # Already connected
        logger.debug("MongoDB connection already established.")
        return

    if not MONGO_URI or not MONGO_DB_NAME or not MONGO_REPORTS_COLLECTION:
        logger.critical(
            "MongoDB environment variables (MONGO_URI, MONGO_DB_NAME, MONGO_REPORTS_COLLECTION) "
            "not fully set. MongoDB integration will be disabled."
        )
        return

    try:
        # Obfuscate credentials for logging if present
        log_uri = MONGO_URI
        if "@" in log_uri and "mongodb+srv://" in log_uri:
            parts = log_uri.split("@")
            credentials_part = parts[0].split("://")
            if len(credentials_part) > 1 and ":" in credentials_part[1]:
                user_pass = credentials_part[1].split(":")
                user = user_pass[0]
                log_uri = f"{credentials_part[0]}://{user}:<PASSWORD>@{parts[1]}"
            else: # Just user, no password, or malformed
                 log_uri = f"{credentials_part[0]}://<USER>@{parts[1]}"


        logger.info(f"Attempting to connect to MongoDB: {log_uri} (DB: {MONGO_DB_NAME})")
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000) # Timeout for connection
        mongo_client.admin.command('ping') # Verify connection

        db = mongo_client[MONGO_DB_NAME]
        reports_collection = db[MONGO_REPORTS_COLLECTION]

        logger.info(
            f"Successfully connected to MongoDB. Database: {MONGO_DB_NAME}, "
            f"Collection: {MONGO_REPORTS_COLLECTION}"
        )
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {e}. MongoDB integration will be disabled.")
        mongo_client = None
        db = None
        reports_collection = None

def close_mongo_connection():
    """
    Closes the MongoDB connection if it's open.
    """
    global mongo_client
    if mongo_client:
        mongo_client.close()
        mongo_client = None
        db = None
        reports_collection = None # Ensure this is also reset
        logger.info("MongoDB connection closed.")

# --- Initialize connection when module is loaded ---
# This makes `reports_collection` available for import in main.py.
# For FastAPI applications, it's generally better to manage connections
# using lifespan events (on_event("startup") and on_event("shutdown")).
# However, this approach keeps it simple and similar to your original structure.
# connect_to_mongo()
