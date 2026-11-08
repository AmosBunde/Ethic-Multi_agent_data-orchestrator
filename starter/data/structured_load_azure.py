"""Load the demographic CSVs in data/structured/ into Azure Database for PostgreSQL.

Connection details are read from Azure Key Vault (device-code login), never hardcoded.
Run from anywhere:  python data/structured_load_azure.py
"""
import csv
import re
from pathlib import Path

import psycopg
from azure.identity import DeviceCodeCredential
from azure.keyvault.secrets import SecretClient

# ---------- CONFIG ----------

keyVaultName = "structuredazureendpoint"
KVUri = f"https://{keyVaultName}.vault.azure.net/"

DATA_FOLDER = Path(__file__).resolve().parent / "structured"  # CSV datasets


def load_db_config() -> dict:
    client = SecretClient(vault_url=KVUri, credential=DeviceCodeCredential())
    return {
        "host": client.get_secret("postgres-host").value,
        "dbname": client.get_secret("postgres-database").value,
        "user": client.get_secret("postgres-user").value,
        "password": client.get_secret("postgres-password").value,
        "port": 5432,
        "sslmode": "require",
    }


# ---------- HELPERS ----------

def clean_identifier(name: str) -> str:
    """Convert column/table names to safe SQL identifiers."""
    name = name.lower()
    name = re.sub(r"\W+", "_", name)
    return name.strip("_")


def infer_type(value: str):
    """Simple type inference."""
    if value == "":
        return "TEXT"

    try:
        int(value)
        return "INTEGER"
    except ValueError:
        pass

    try:
        float(value)
        return "FLOAT"
    except ValueError:
        pass

    if value.lower() in {"true", "false", "yes", "no"}:
        return "BOOLEAN"

    return "TEXT"


def infer_schema(csv_path: Path):
    """Infer schema from first non-empty row."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        first_row = next(reader)

    schema = {}

    for col, value in first_row.items():
        schema[clean_identifier(col)] = infer_type(value)

    return schema


# ---------- DATABASE FUNCTIONS ----------

def create_table(conn, table_name, schema):
    columns_sql = ",\n".join(
        f"{col} {dtype}" for col, dtype in schema.items()
    )

    create_sql = f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id SERIAL PRIMARY KEY,
            {columns_sql}
        );
    """

    with conn.cursor() as cur:
        cur.execute(create_sql)

    conn.commit()
    print(f"✔ Table created: {table_name}")


def ingest_csv(conn, csv_path, table_name, schema):
    columns = list(schema.keys())

    insert_sql = f"""
        INSERT INTO {table_name} ({",".join(columns)})
        VALUES ({",".join(["%s"] * len(columns))})
    """

    rows = []

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            rows.append(tuple(None if v == "" else v for v in row.values()))

    with conn.cursor() as cur:
        cur.executemany(insert_sql, rows)

    conn.commit()

    print(f"✔ Loaded {len(rows)} rows into {table_name}")


# ---------- PIPELINE ----------

def process_csv(conn, csv_path):
    table_name = clean_identifier(csv_path.stem)

    print(f"\nProcessing: {csv_path.name}")

    schema = infer_schema(csv_path)

    create_table(conn, table_name, schema)

    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        existing = cur.fetchone()[0]
    if existing:
        print(f"↷ Skipping {table_name}: already holds {existing} rows (drop the table to reload)")
        return

    ingest_csv(conn, csv_path, table_name, schema)


def main():
    csv_files = sorted(DATA_FOLDER.glob("*.csv"))
    if not csv_files:
        print(f"No CSV files found in {DATA_FOLDER}")
        return

    try:
        with psycopg.connect(**load_db_config()) as conn:
            for csv_file in csv_files:
                process_csv(conn, csv_file)

        print("\n✅ All datasets ingested successfully!")

    except Exception as e:
        print("❌ Error:", e)


if __name__ == "__main__":
    main()
