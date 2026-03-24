"""
init_storage.py
---------------
One-shot setup script run by the "init-storage" Docker Compose service.

Creates the two blob containers the function depends on inside Azurite
(the local blob storage emulator):

    uploads  — where ZIP files are uploaded to trigger the function
    sessions — where extracted files (crops, annotated images) are stored

Uses the Azure Blob Storage SDK instead of manually signing REST requests.
That avoids SharedKey signature mismatches against Azurite.

This script is safe to run multiple times — it silently skips containers
that already exist.
"""

import logging
import os
import sys

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [init-storage] %(levelname)s %(message)s",
    stream=sys.stdout,
)

CONTAINERS = ["uploads", "sessions"]

# Azurite's fixed, publicly documented development key.
# The same on every machine — only works with the local emulator.
AZURITE_DEFAULT_KEY = (
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tiq"
    "nDkHl02wH0bM68pPQJQiKsQ=="
)


def build_connection_string() -> str:
    """
    Build an Azurite connection string from environment variables.

    Preference order:
      1. AZURITE_CONNECTION_STRING (full connection string)
      2. Construct one from AZURITE_ACCOUNT_KEY / host / port
    """
    conn = (os.environ.get("AZURITE_CONNECTION_STRING") or "").strip()
    if conn:
        return conn

    key = (os.environ.get("AZURITE_ACCOUNT_KEY") or AZURITE_DEFAULT_KEY).strip()
    host = os.environ.get("AZURITE_HOST", "azurite").strip()
    port = os.environ.get("AZURITE_PORT", "10000").strip()
    account_name = "devstoreaccount1"

    return (
        "DefaultEndpointsProtocol=http;"
        f"AccountName={account_name};"
        f"AccountKey={key};"
        f"BlobEndpoint=http://{host}:{port}/{account_name};"
    )


def create_container(blob_service: BlobServiceClient, container: str) -> None:
    """Create one blob container if it does not already exist."""
    client = blob_service.get_container_client(container)
    try:
        client.create_container()
        logging.info("Created container '%s'", container)
    except ResourceExistsError:
        logging.info("Container already exists (skipping): '%s'", container)


def main() -> None:
    connection_string = build_connection_string()
    logging.info("Connecting to Azurite using configured connection string")

    blob_service = BlobServiceClient.from_connection_string(connection_string)

    for name in CONTAINERS:
        create_container(blob_service, name)

    logging.info("Storage initialisation complete.")


if __name__ == "__main__":
    main()