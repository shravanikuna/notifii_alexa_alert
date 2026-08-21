#!/usr/bin/env python3
import os
from dotenv import load_dotenv
load_dotenv()

import db

# Test 1: Get resident
print("=" * 60)
print("🔍 Testing get_resident_by_address('4B')")
resident = db.get_resident_by_address('4B')
print(f"Resident: {resident}")

if resident:
    print(f"✅ Resident found: ID={resident.get('id')}")
    
    # Test 2: Save package
    print("\n" + "=" * 60)
    print("🔍 Testing save_or_update_package")
    result, is_new = db.save_or_update_package(
        resident_id=resident['id'],
        package_id='PKG-TEST-001',
        carrier='DHL',
        tracking_number='999888777',
        compartment='Z9',
        delivered_at='2026-08-20 12:00:00'
    )
    print(f"Result: {result}")
    print(f"Is new: {is_new}")
else:
    print("❌ Resident not found!")