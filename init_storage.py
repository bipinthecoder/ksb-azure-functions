"""
init_storage.py
---------------
One-shot setup script run by the "init-storage" Docker Compose service.

Creates the two blob containers the function depends on inside Azurite
(the local blob storage emulator):

    uploads  — where ZIP files are uploaded to trigger the function
    sessions — where extracted files (crops, annotated images) are stored

This script is safe to run multiple times — it silently skips containers
that already exist (ContainerAlreadyExists error is caught and ignored).
"""

import logging
import os
import sys

from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceExistsError

# Send logs to stdout so they appear in "docker compose logs init-storage"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [init-storage] %(levelname)s %(message)s",
    stream=sys.stdout,
)

# Names of the containers to create
CONTAINERS = ["uploads", "sessions"]

def main() -> None:
    # Read the connection string injected by docker-compose.yml
    connection_string = os.environ["AZURE_STORAGE_CONNECTION_STRING"]

    # Connect to Azurite (or Azure Storage if this is run against a real account)
    client = BlobServiceClient.from_connection_string(connection_string)
    logging.info("Connected to Blob Storage.")

    for name in CONTAINERS:
        try:
            client.create_container(name)
            logging.info("Created container: '%s'", name)
        except ResourceExistsError:
            # Container already exists — nothing to do
            logging.info("Container already exists (skipping): '%s'", name)

    logging.info("Storage initialisation complete.")


if __name__ == "__main__":
    main()
