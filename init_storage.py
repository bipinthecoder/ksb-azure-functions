"""
init_storage.py
---------------
One-shot setup script run by the "init-storage" Docker Compose service.

Creates the two blob containers the function depends on inside Azurite
(the local blob storage emulator):

    uploads  — where ZIP files are uploaded to trigger the function
    sessions — where extracted files (crops, annotated images) are stored

The connection string is built here from individual environment variables
rather than passed as a single pre-built string — this avoids any YAML or
shell substitution issues with embedded special characters (=, ;, :).

This script is safe to run multiple times — it silently skips containers
that already exist.
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


def build_connection_string() -> str:
    """
    Build the Azurite connection string from individual environment variables.

    Using individual variables avoids issues where a pre-built connection
    string containing '=', ';', or ':' characters gets corrupted during
    Docker Compose YAML variable substitution.

    Env vars:
        AZURITE_ACCOUNT_KEY  — the storage account key (falls back to the
                               well-known public Azurite development key)
        AZURITE_HOST         — hostname of the Azurite container (default: azurite)
        AZURITE_PORT         — blob service port (default: 10000)
    """
    # Azurite's fixed, publicly documented development key.
    # It is the same on every machine and only works with the local emulator,
    # not with real Azure Storage — safe to use as a hardcoded default.
    AZURITE_DEFAULT_KEY = (
        "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tiq"
        "nDkHl02wH0bM68pPQJQiKsQ=="
    )

    key  = os.environ.get("AZURITE_ACCOUNT_KEY") or AZURITE_DEFAULT_KEY
    host = os.environ.get("AZURITE_HOST", "azurite")
    port = os.environ.get("AZURITE_PORT", "10000")

    logging.info("Using account key (last 6 chars): ...%s", key[-6:])

    return (
        f"DefaultEndpointsProtocol=http;"
        f"AccountName=devstoreaccount1;"
        f"AccountKey={key};"
        f"BlobEndpoint=http://{host}:{port}/devstoreaccount1;"
    )


def main() -> None:
    conn_str = build_connection_string()
    logging.info("Connecting to Blob Storage at %s:%s ...",
                 os.environ.get("AZURITE_HOST", "azurite"),
                 os.environ.get("AZURITE_PORT", "10000"))

    # Connect to Azurite (or Azure Storage if run against a real account)
    client = BlobServiceClient.from_connection_string(conn_str)
    logging.info("Connected to Blob Storage.")

    for name in CONTAINERS:
        try:
            client.create_container(name)
            logging.info("Created container: '%s'", name)
        except ResourceExistsError:
            # Container already exists from a previous run — nothing to do
            logging.info("Container already exists (skipping): '%s'", name)

    logging.info("Storage initialisation complete.")


if __name__ == "__main__":
    main()
