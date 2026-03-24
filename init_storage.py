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

from azure.storage.blob import BlobServiceClient, StorageSharedKeyCredential
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
    # Azurite's fixed, publicly documented development key.
    # It is the same on every machine and only works with the local emulator,
    # not with real Azure Storage — safe to use as a hardcoded default.
    AZURITE_DEFAULT_KEY = (
        "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tiq"
        "nDkHl02wH0bM68pPQJQiKsQ=="
    )

    account_name = "devstoreaccount1"
    # .strip() removes any invisible characters (e.g. \r from Windows line
    # endings in .env files) that would silently corrupt the HMAC signature.
    key  = (os.environ.get("AZURITE_ACCOUNT_KEY") or AZURITE_DEFAULT_KEY).strip()
    host = os.environ.get("AZURITE_HOST", "azurite")
    port = os.environ.get("AZURITE_PORT", "10000")

    logging.info("Key length: %d, last 6 chars: ...%s", len(key), key[-6:])

    # Use StorageSharedKeyCredential + explicit account_url instead of
    # from_connection_string().  When from_connection_string() parses a
    # path-style emulator URL (http://azurite:10000/devstoreaccount1) it can
    # misidentify the account name, which corrupts the HMAC canonical resource
    # string and causes a 403 AuthorizationFailure even with the correct key.
    credential = StorageSharedKeyCredential(account_name, key)
    account_url = f"http://{host}:{port}/{account_name}"

    logging.info("Connecting to Blob Storage at %s ...", account_url)

    client = BlobServiceClient(account_url=account_url, credential=credential)
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
