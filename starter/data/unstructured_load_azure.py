"""Load the permit PDFs in data/unstructured/ into Azure Cosmos DB for MongoDB.

Connection string is read from Azure Key Vault (device-code login), never hardcoded.
Run from anywhere:  python data/unstructured_load_azure.py
"""
from pathlib import Path

import PyPDF2
from azure.identity import DeviceCodeCredential
from azure.keyvault.secrets import SecretClient
from pymongo import MongoClient

keyVaultName = "structuredazureendpoint"
KVUri = f"https://{keyVaultName}.vault.azure.net/"

COLLECTION_NAME = "permits"  # chat.py relies on this name
PDF_FOLDER = Path(__file__).resolve().parent / "unstructured"


def load_pdfs_to_mongo(folder_path: Path = PDF_FOLDER):
    secrets = SecretClient(vault_url=KVUri, credential=DeviceCodeCredential())
    mongo_uri = secrets.get_secret("mongodb-connection-string").value
    db_name = secrets.get_secret("mongodb-database").value

    client = MongoClient(mongo_uri)
    db = client[db_name]

    # Explicitly create collection if it doesn't exist
    if COLLECTION_NAME not in db.list_collection_names():
        db.create_collection(COLLECTION_NAME)

    collection = db[COLLECTION_NAME]

    if not folder_path.is_dir():
        raise FileNotFoundError(f"Folder not found: {folder_path}")

    loaded = 0
    for pdf_path in sorted(folder_path.glob("*.pdf")):
        print(f"Processing: {pdf_path.name}")

        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            text = "".join((page.extract_text() or "") for page in reader.pages)

        # Idempotent: re-running updates the document instead of duplicating it.
        collection.update_one(
            {"filename": pdf_path.name},
            {"$set": {"filename": pdf_path.name, "content": text}},
            upsert=True,
        )
        loaded += 1

    print(f"All {loaded} PDFs loaded into MongoDB ({db_name}.{COLLECTION_NAME}).")


if __name__ == "__main__":
    load_pdfs_to_mongo()
