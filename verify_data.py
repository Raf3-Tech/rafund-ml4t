"""Quick verification script to check database contents."""
from data.db import DatabaseConnection

db = DatabaseConnection()
stats = db.get_data_stats()

print("\n" + "=" * 50)
print("DATABASE VERIFICATION")
print("=" * 50)
print(f"Total records: {stats.get('total_price_records', 0)}")
print(f"Total symbols: {stats.get('num_symbols', 0)}")
print(f"Date range: {stats.get('min_date')} to {stats.get('max_date')}")
print("=" * 50 + "\n")

db.close_pool()
