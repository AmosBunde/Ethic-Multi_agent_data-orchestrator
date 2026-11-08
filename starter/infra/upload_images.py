"""Upload the house images in data/multimodal/ to the Blob Storage container the
multimodal agent reads (default "houses"). Idempotent: existing blobs are skipped.

    python infra/upload_images.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from azure_login import get_credential
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient, ContentSettings

VAULT = "structuredazureendpoint"
CONTAINER = "houses"
IMAGE_DIR = Path(__file__).resolve().parents[1] / "data" / "multimodal"


def ensure_images(credential=None, container_name=CONTAINER, image_dir=IMAGE_DIR):
    secrets = SecretClient(f"https://{VAULT}.vault.azure.net/", credential or get_credential())
    conn = secrets.get_secret("blob-connection-string").value
    service = BlobServiceClient.from_connection_string(conn)
    container = service.get_container_client(container_name)
    if not container.exists():
        container.create_container()
        print(f"Created container '{container_name}'")

    existing = {b.name for b in container.list_blobs()}
    files = sorted(p for p in image_dir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    uploaded = 0
    for p in files:
        if p.name in existing:
            continue
        with open(p, "rb") as f:
            container.upload_blob(p.name, f, overwrite=False,
                                  content_settings=ContentSettings(content_type="image/jpeg"))
        uploaded += 1
        print(f"  uploaded {p.name}")
    print(f"Container '{container_name}': {len(existing)} already present, {uploaded} uploaded, "
          f"{len(existing) + uploaded} total")
    return len(existing) + uploaded


if __name__ == "__main__":
    sys.exit(0 if ensure_images() else 1)
