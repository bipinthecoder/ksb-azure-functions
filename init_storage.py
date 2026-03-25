"""
init_storage.py
---------------
One-shot setup script run by the "init-storage" Docker Compose service.

Creates the two blob containers the function depends on inside Azurite:
    litter-detection-sbx
    sessions

Uses the Azure Blob Storage SDK and the standard Azurite local
connection string. Safe to run multiple times.
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

CONTAINERS = ["litter-detection-sbx", "sessions"]

# Correct Azurite default dev key for devstoreaccount1
AZURITE_DEFAULT_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey="
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://azurite:10000/devstoreaccount1;"
)


def build_connection_string() -> str:
    """
    Prefer AZURITE_CONNECTION_STRING from the environment.
    Fall back to the correct Azurite default connection string.
    """
    conn = (os.environ.get("AZURITE_CONNECTION_STRING") or "").strip()
    if conn:
        return conn
    return AZURITE_DEFAULT_CONNECTION_STRING


def create_container(blob_service: BlobServiceClient, container_name: str) -> None:
    client = blob_service.get_container_client(container_name)
    try:
        client.create_container()
        logging.info("Created container '%s'", container_name)
    except ResourceExistsError:
        logging.info("Container already exists (skipping): '%s'", container_name)


def main() -> None:
    conn = build_connection_string()
    logging.info("Using Azurite connection string: %s", conn)

    blob_service = BlobServiceClient.from_connection_string(conn)

    for name in CONTAINERS:
        create_container(blob_service, name)

    logging.info("Storage initialisation complete.")


if __name__ == "__main__":
    main()