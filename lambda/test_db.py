#!/usr/bin/env python3
import os
from dotenv import load_dotenv
import mysql.connector

# Load .env file
load_dotenv()

def test_connection():
    print("=" * 60)
    print("🔍 TESTING DATABASE CONNECTION")
    print("=" * 60)
    
    print(f"DB_HOST: {os.environ.get('DB_HOST', 'NOT SET')}")
    print(f"DB_USER: {os.environ.get('DB_USER', 'NOT SET')}")
    print(f"DB_NAME: {os.environ.get('DB_NAME', 'NOT SET')}")
    
    try:
        conn = mysql.connector.connect(
            host=os.environ.get('DB_HOST', 'localhost'),
            user=os.environ.get('DB_USER', ''),
            password=os.environ.get('DB_PASSWORD', ''),
            database=os.environ.get('DB_NAME', ''),
            port=int(os.environ.get('DB_PORT', 3306))
        )
        print("✅ Database connection SUCCESSFUL!")
        conn.close()
        return True
    except Exception as e:
        print(f"❌ Database connection FAILED: {e}")
        return False

def test_get_resident():
    print("\n" + "=" * 60)
    print("🔍 TESTING get_resident_by_unit('4B')")
    print("=" * 60)
    
    try:
        import db
        result = db.get_resident_by_unit('4B')
        if result:
            print("✅ Resident FOUND!")
            print(f"   Unit: {result.get('unit')}")
            print(f"   Alexa ID: {result.get('alexa_user_id', 'None')[:30]}...")
            return True
        else:
            print("❌ Resident NOT FOUND!")
            return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    test_connection()
    test_get_resident()