import os
import mysql.connector
from mysql.connector import Error
import logging
from datetime import datetime
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


def get_resident_by_alexa_id(alexa_user_id: str):
    """Used by LaunchRequestHandler / PackageStatusIntentHandler to recognize the caller."""
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM residents WHERE alexa_user_id = %s", (alexa_user_id,))
        result = cursor.fetchone()
        cursor.close(); conn.close()
        return result
    except Error as e:
        logger.error(f"get_resident_by_alexa_id error: {e}")
        return None


def get_resident_by_address(address: str):
    """Used by the webhook handler to match Notifii's 'unit' (address) field to a resident."""
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM residents WHERE unit = %s", (address,))
        result = cursor.fetchone()
        cursor.close(); conn.close()
        return result
    except Error as e:
        logger.error(f"get_resident_by_address error: {e}")
        return None


def save_or_update_package(resident_id, package_id, carrier, tracking_number, compartment, delivered_at):
    """Insert or update — same package_id + resident never creates a duplicate row."""
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM packages WHERE package_id = %s AND resident_id = %s",
            (package_id, resident_id)
        )
        existing = cursor.fetchone()
        cursor.close(); conn.close()

        if existing:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE packages SET carrier=%s, tracking_number=%s, compartment=%s, delivered_at=%s WHERE id=%s",
                (carrier, tracking_number, compartment, delivered_at, existing['id'])
            )
            conn.commit(); cursor.close(); conn.close()

            conn = get_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM packages WHERE id = %s", (existing['id'],))
            updated = cursor.fetchone()
            cursor.close(); conn.close()
            return updated, False

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO packages (resident_id, package_id, carrier, tracking_number, compartment, delivered_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (resident_id, package_id, carrier, tracking_number, compartment, delivered_at)
        )
        conn.commit()
        new_id = cursor.lastrowid
        cursor.close(); conn.close()

        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM packages WHERE id = %s", (new_id,))
        new_row = cursor.fetchone()
        cursor.close(); conn.close()
        return new_row, True
    except Error as e:
        logger.error(f"save_or_update_package error: {e}")
        return None, False


def mark_package_notified(package_row_id: int):
    try:
        conn = get_connection(); cursor = conn.cursor()
        cursor.execute("UPDATE packages SET notification_status='notified', last_notified_at=NOW() WHERE id=%s", (package_row_id,))
        conn.commit(); cursor.close(); conn.close()
    except Error as e:
        logger.error(f"mark_package_notified error: {e}")


def increment_reminder(package_row_id: int):
    try:
        conn = get_connection(); cursor = conn.cursor()
        cursor.execute("UPDATE packages SET reminder_count = reminder_count + 1, last_notified_at = NOW() WHERE id=%s", (package_row_id,))
        conn.commit(); cursor.close(); conn.close()
    except Error as e:
        logger.error(f"increment_reminder error: {e}")


def get_all_packages_for_resident(resident_id: int):
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM packages WHERE resident_id = %s ORDER BY delivered_at DESC", (resident_id,))
        results = cursor.fetchall()
        cursor.close(); conn.close()
        return results
    except Error as e:
        logger.error(f"get_all_packages_for_resident error: {e}")
        return []


def get_unheard_packages_for_resident(resident_id: int):
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM packages WHERE resident_id = %s AND heard_at IS NULL ORDER BY delivered_at DESC",
            (resident_id,)
        )
        results = cursor.fetchall()
        cursor.close(); conn.close()
        return results
    except Error as e:
        logger.error(f"get_unheard_packages_for_resident error: {e}")
        return []


def mark_packages_heard(package_row_ids):
    if not package_row_ids:
        return
    try:
        conn = get_connection()
        cursor = conn.cursor()
        format_ids = ','.join(['%s'] * len(package_row_ids))
        cursor.execute(f"UPDATE packages SET heard_at = NOW() WHERE id IN ({format_ids})", tuple(package_row_ids))
        conn.commit(); cursor.close(); conn.close()
    except Error as e:
        logger.error(f"mark_packages_heard error: {e}")


def update_last_session(resident_id: int):
    try:
        conn = get_connection(); cursor = conn.cursor()
        cursor.execute("UPDATE residents SET last_session_at = NOW() WHERE id = %s", (resident_id,))
        conn.commit(); cursor.close(); conn.close()
    except Error as e:
        logger.error(f"update_last_session error: {e}")


def log_notification(package_row_id: int, amazon_request_id, status: str, status_reason: str = None):
    try:
        conn = get_connection(); cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO notification_log (package_id, amazon_request_id, status, status_reason) VALUES (%s, %s, %s, %s)",
            (package_row_id, amazon_request_id, status, status_reason)
        )
        conn.commit(); cursor.close(); conn.close()
    except Error as e:
        logger.error(f"log_notification error: {e}")