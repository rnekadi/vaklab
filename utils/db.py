import os
import logging
import psycopg2

# Defaults provided for safety, but should be set in environment.
DB_HOST = os.environ.get("DB_HOST", "localhost")
# Using the updated DB name if user changed it? Assuming same env var just likely different value.
DB_NAME = os.environ.get("DB_NAME", "debt_collection_db")
DB_USER = os.environ.get("DB_USER", "user")
DB_PASS = os.environ.get("DB_PASSWORD", "password")

def get_db_connection():
    """Establishes a connection to the PostgreSQL database."""
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASS
        )
        return conn
    except Exception as e:
        logging.error(f"Failed to connect to DB: {e}")
        return None
