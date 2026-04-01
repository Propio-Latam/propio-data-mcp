#!/usr/bin/env bash
# =============================================================================
# Load Excel data into PostgreSQL on the VM
# Usage: scp your Excel files to the VM, then run this script
# =============================================================================
set -euo pipefail

DATA_DIR="${1:-.}"
DB_NAME="${2:-creditu}"
DB_USER="mcpbridge"

echo ">>> Creating database '$DB_NAME' if not exists"
sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;" 2>/dev/null || echo "Database already exists"

echo ">>> Loading Excel files from $DATA_DIR"
cd /opt/mcp-data-bridge
.venv/bin/pip install --quiet openpyxl pandas

.venv/bin/python3 << PYEOF
import pandas as pd
import os
from sqlalchemy import create_engine, text

base_dir = "$DATA_DIR"
db_name = "$DB_NAME"
engine = create_engine(f"postgresql://$DB_USER:$DB_USER@localhost:5432/{db_name}")

all_dfs = []
files = sorted([f for f in os.listdir(base_dir) if f.endswith(('.xlsx', '.xls'))])

if not files:
    print(f"No Excel files found in {base_dir}")
    exit(1)

for fname in files:
    df = pd.read_excel(os.path.join(base_dir, fname))
    df['source_file'] = fname
    all_dfs.append(df)
    print(f"  Read {fname}: {len(df)} rows")

combined = pd.concat(all_dfs, ignore_index=True)

# Normalize columns to snake_case
import re, unicodedata
def normalize_col(name):
    name = unicodedata.normalize('NFKD', str(name)).encode('ascii', 'ignore').decode()
    name = re.sub(r'[^a-zA-Z0-9\s]', '', name)
    return re.sub(r'\s+', '_', name.strip()).lower()

combined.columns = [normalize_col(c) for c in combined.columns]
print(f"Columns: {list(combined.columns)}")
print(f"Total rows: {len(combined)}")

# Deduplicate
before = len(combined)
combined.drop_duplicates(keep='first', inplace=True)
print(f"After dedup: {len(combined)} (removed {before - len(combined)})")

table_name = input("Table name (default: data): ").strip() or "data"
combined.to_sql(table_name, engine, if_exists='replace', index=False, method='multi')

with engine.connect() as conn:
    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN id SERIAL PRIMARY KEY;"))
    conn.commit()

print(f"Loaded {len(combined)} rows into {db_name}.{table_name}")
PYEOF

echo ">>> Done! Now register the database via the API:"
echo "  curl -X POST http://localhost:8000/api/databases \\"
echo "    -H 'Content-Type: application/json' -H 'X-API-Key: YOUR_KEY' \\"
echo "    -d '{\"name\":\"$DB_NAME\", \"host\":\"localhost\", \"port\":5432, \"dbname\":\"$DB_NAME\", \"username\":\"$DB_USER\", \"password\":\"$DB_USER\"}'"
