import os
import mysql.connector
from mysql.connector import Error
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'localhost'),
    'user': os.environ.get('DB_USER', ''),
    'password': os.environ.get('DB_PASSWORD', ''),
    'database': os.environ.get('DB_NAME', ''),
    'port': int(os.environ.get('DB_PORT', 3306)),
}

def get_connection():
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except Error as e:
        logger.error(f"Database connection error: {e}")
        raise

# ============================================
# RESIDENT FUNCTIONS
# ============================================

def get_resident_by_alexa_id(alexa_user_id: str):
    """Get resident by Alexa user ID."""
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM residents WHERE alexa_user_id = %s AND opted_in = TRUE",
            (alexa_user_id,)
        )
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result
    except Error as e:
        logger.error(f"get_resident_by_alexa_id error: {e}")
        return None

def get_resident_by_account_id(account_id: str):
    """Get resident by account_id."""
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM residents WHERE account_id = %s AND opted_in = TRUE",
            (account_id,)
        )
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result
    except Error as e:
        logger.error(f"get_resident_by_account_id error: {e}")
        return None

def link_account_id_to_alexa(account_id: str, alexa_user_id: str, region: str = "NA") -> bool:
    """Link account_id to alexa_user_id."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT * FROM residents WHERE account_id = %s",
            (account_id,)
        )
        existing = cursor.fetchone()
        
        if existing:
            cursor.execute(
                """
                UPDATE residents 
                SET alexa_user_id = %s, alexa_region = %s, opted_in = TRUE, linked_at = NOW()
                WHERE account_id = %s
                """,
                (alexa_user_id, region, account_id)
            )
        else:
            cursor.execute(
                """
                INSERT INTO residents (account_id, alexa_user_id, alexa_region, opted_in, linked_at)
                VALUES (%s, %s, %s, TRUE, NOW())
                """,
                (account_id, alexa_user_id, region)
            )
        
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Error as e:
        logger.error(f"link_account_id_to_alexa error: {e}")
        return False

# ============================================
# PACKAGE FUNCTIONS
# ============================================

def save_or_update_package(resident_id: int, tracking_number: str, carrier: str,
                           package_id: str, compartment: str, delivered_at: str):
    """
    Insert or update package based on tracking_number (unique).
    Returns (package_row, is_new).
    """
    logger.info(f"📝 save_or_update_package: tracking={tracking_number}, carrier={carrier}")
    
    try:
        conn = get_connection()
        
        # Check if package exists by tracking_number
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM packages WHERE tracking_number = %s AND resident_id = %s",
            (tracking_number, resident_id)
        )
        existing = cursor.fetchone()
        cursor.close()
        
        if existing:
            # Update existing package
            update_cursor = conn.cursor()
            update_cursor.execute(
                """
                UPDATE packages 
                SET carrier = %s, package_id = %s, compartment = %s, delivered_at = %s,
                    updated_at = NOW()
                WHERE tracking_number = %s AND resident_id = %s
                """,
                (carrier, package_id, compartment, delivered_at, tracking_number, resident_id)
            )
            conn.commit()
            update_cursor.close()
            
            # Fetch updated record
            select_cursor = conn.cursor(dictionary=True)
            select_cursor.execute(
                "SELECT * FROM packages WHERE tracking_number = %s AND resident_id = %s",
                (tracking_number, resident_id)
            )
            updated = select_cursor.fetchone()
            select_cursor.close()
            conn.close()
            logger.info(f"✅ Package updated: {updated}")
            return updated, False
        
        # Insert new package
        insert_cursor = conn.cursor()
        insert_cursor.execute(
            """
            INSERT INTO packages 
                (resident_id, package_id, tracking_number, carrier, compartment, delivered_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (resident_id, package_id, tracking_number, carrier, compartment, delivered_at)
        )
        conn.commit()
        new_id = insert_cursor.lastrowid
        insert_cursor.close()
        
        # Fetch new record
        select_cursor = conn.cursor(dictionary=True)
        select_cursor.execute(
            "SELECT * FROM packages WHERE id = %s",
            (new_id,)
        )
        new_row = select_cursor.fetchone()
        select_cursor.close()
        conn.close()
        logger.info(f"✅ New package inserted: {new_row}")
        return new_row, True
        
    except Error as e:
        logger.error(f"❌ save_or_update_package error: {e}")
        return None, False

def get_packages_for_resident(resident_id: int):
    """Get all packages for a resident."""
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT * FROM packages 
            WHERE resident_id = %s 
            ORDER BY delivered_at DESC
            """,
            (resident_id,)
        )
        results = cursor.fetchall()
        cursor.close()
        conn.close()
        return results
    except Error as e:
        logger.error(f"get_packages_for_resident error: {e}")
        return []

def mark_package_notified(package_id: int):
    """Mark package as notified."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE packages SET notification_sent = TRUE, last_notified_at = NOW() WHERE id = %s",
            (package_id,)
        )
        conn.commit()
        cursor.close()
        conn.close()
    except Error as e:
        logger.error(f"mark_package_notified error: {e}")

def increment_reminder(package_id: int):
    """Increment reminder count."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE packages SET reminder_count = reminder_count + 1 WHERE id = %s",
            (package_id,)
        )
        conn.commit()
        cursor.close()
        conn.close()
    except Error as e:
        logger.error(f"increment_reminder error: {e}")

def get_package_by_tracking(tracking_number: str, resident_id: int):
    """Get package by tracking number."""
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM packages WHERE tracking_number = %s AND resident_id = %s",
            (tracking_number, resident_id)
        )
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result
    except Error as e:
        logger.error(f"get_package_by_tracking error: {e}")
        return None

# ============================================
# NOTIFICATION LOG
# ============================================

def log_notification(package_id: int, status: str, status_reason: str = None):
    """Log notification attempt."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO notification_log (package_id, status, status_reason) VALUES (%s, %s, %s)",
            (package_id, status, status_reason)
        )
        conn.commit()
        cursor.close()
        conn.close()
    except Error as e:
        logger.error(f"log_notification error: {e}")