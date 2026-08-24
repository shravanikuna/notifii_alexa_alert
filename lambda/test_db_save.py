#!/usr/bin/env python3
import os
from dotenv import load_dotenv
import mysql.connector

load_dotenv()

print(f"DB_HOST: {os.environ.get('DB_HOST')}")
print(f"DB_USER: {os.environ.get('DB_USER')}")
print(f"DB_NAME: {os.environ.get('DB_NAME')}")

try:
    conn = mysql.connector.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        user=os.environ.get('DB_USER', ''),
        password=os.environ.get('DB_PASSWORD', ''),
        database=os.environ.get('DB_NAME', ''),
        port=int(os.environ.get('DB_PORT', 3306))
    )
    print("✅ Connection SUCCESSFUL!")
    conn.close()
except Exception as e:
    print(f"❌ Connection FAILED: {e}")