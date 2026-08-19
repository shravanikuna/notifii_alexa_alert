import os
import mysql.connector
from mysql.connector import Error
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)

DB_CONFIG = {
    'host': os.environ.get('DB_HOST', '').strip(),
    'user': os.environ.get('DB_USER', '').strip(),
    'password': os.environ.get('DB_PASSWORD', '').strip(),
    'database': os.environ.get('DB_NAME', '').strip(),
    'port': int(os.environ.get('DB_PORT', 3306)),
}


@contextmanager
def get_connection():
    """Opens a connection, always closes it, even on error."""
    conn = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        yield conn
    except Error as e:
        logger.error(f"Database connection error: {e}")
        raise
    finally:
        if conn and conn.is_connected():
            conn.close()


def link_resident(unit: str, alexa_user_id: str, region: str = "NA") -> bool:
    """INSERT or UPDATE a resident's Alexa account link."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO residents (unit, alexa_user_id, alexa_region, opted_in, linked_at)
                VALUES (%s, %s, %s, TRUE, NOW())
                ON DUPLICATE KEY UPDATE
                    alexa_user_id = VALUES(alexa_user_id),
                    alexa_region = VALUES(alexa_region),
                    opted_in = TRUE,
                    linked_at = NOW()
                """,
                (unit, alexa_user_id, region)
            )
            conn.commit()
            cursor.close()
            return True
    except Error as e:
        logger.error(f"link_resident error: {e}")
        return False


def get_resident_by_alexa_id(alexa_user_id: str):
    """SELECT — used to find which unit an Alexa account belongs to."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT * FROM residents WHERE alexa_user_id = %s AND opted_in = TRUE",
                (alexa_user_id,)
            )
            result = cursor.fetchone()
            cursor.close()
            return result
    except Error as e:
        logger.error(f"get_resident_by_alexa_id error: {e}")
        return None


def get_resident_by_unit(unit: str):
    """SELECT — used by the webhook to find a resident's Alexa account."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                "SELECT * FROM residents WHERE unit = %s AND opted_in = TRUE",
                (unit,)
            )
            result = cursor.fetchone()
            cursor.close()
            return result
    except Error as e:
        logger.error(f"get_resident_by_unit error: {e}")
        return None


def save_package(resident_id: int, package_id: str, carrier: str,
                  tracking_number: str, compartment: str, delivered_at: str) -> int:
    """INSERT a new package row, returns the new package's id."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO packages
                    (resident_id, package_id, carrier, tracking_number, compartment, delivered_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (resident_id, package_id, carrier, tracking_number, compartment, delivered_at)
            )
            conn.commit()
            new_id = cursor.lastrowid
            cursor.close()
            return new_id
    except Error as e:
        logger.error(f"save_package error: {e}")
        return None


def get_packages_for_unit(unit: str):
    """SELECT — all packages for a unit, joined through residents."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT p.* FROM packages p
                JOIN residents r ON p.resident_id = r.id
                WHERE r.unit = %s
                ORDER BY p.delivered_at DESC
                """,
                (unit,)
            )
            results = cursor.fetchall()
            cursor.close()
            return results
    except Error as e:
        logger.error(f"get_packages_for_unit error: {e}")
        return []


def log_notification(package_row_id: int, amazon_request_id: str, status: str):
    """INSERT into notification_log — tracks every send attempt."""
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO notification_log (package_id, amazon_request_id, status)
                VALUES (%s, %s, %s)
                """,
                (package_row_id, amazon_request_id, status)
            )
            conn.commit()
            cursor.close()
    except Error as e:
        logger.error(f"log_notification error: {e}")