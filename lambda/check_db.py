# lambda/check_db.py
import mysql.connector
import os
from dotenv import load_dotenv
load_dotenv()

conn = mysql.connector.connect(
    host=os.environ.get('DB_HOST'),
    user=os.environ.get('DB_USER'),
    password=os.environ.get('DB_PASSWORD'),
    database=os.environ.get('DB_NAME'),
    port=int(os.environ.get('DB_PORT', 3306))
)
cursor = conn.cursor()
cursor.execute("SELECT @@hostname, @@port, DATABASE(), VERSION();")
print("Python sees:", cursor.fetchone())
cursor.execute("SELECT COUNT(*) FROM packages;")
print("Package count via Python:", cursor.fetchone())
conn.close()