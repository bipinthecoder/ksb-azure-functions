"""
init_storage.py
---------------
One-shot setup script run by the "init-storage" Docker Compose service.

Creates the two blob containers the function depends on inside Azurite
(the local blob storage emulator):

    uploads  — where ZIP files are uploaded to trigger the function
    sessions — where extracted files (crops, annotated images) are stored

Uses Python's built-in stdlib only (hmac, hashlib, urllib) to sign and
send Azure Storage REST API requests directly.  This avoids any azure-sdk
import-path instability across package versions.

This script is safe to run multiple times — it silently skips containers
that already exist (HTTP 409 Conflict).
"""

import base64
import hashlib
import hmac
import logging
import os
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen

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


def _shared_key_auth(account_name: str, account_key: str,
                     method: str, url: str, headers: dict) -> str:
    """
    Compute the SharedKey Authorization header value for one Azure Storage
    REST API request.

    Implements the canonical string format documented at:
    https://learn.microsoft.com/en-us/rest/api/storageservices/authorize-with-shared-key
    """
    parsed = urlparse(url)

    # --- Canonicalized headers (sorted x-ms-* headers, one per line) ---
    x_ms = {k.lower(): v.strip()
             for k, v in headers.items()
             if k.lower().startswith("x-ms-")}
    canonical_headers = "".join(f"{k}:{x_ms[k]}\n" for k in sorted(x_ms))

    # --- Canonicalized resource (path + sorted query params) ---
    canonical_resource = parsed.path   # e.g. /devstoreaccount1/uploads
    if parsed.query:
        params = parse_qs(parsed.query, keep_blank_values=True)
        for k in sorted(params):
            canonical_resource += f"\n{k}:{','.join(sorted(params[k]))}"

    # --- String to sign (12 header slots + canonicalized sections) ---
    # Microsoft spec: Content-Length must be an empty string when the value
    # is zero — do NOT write "0".
    string_to_sign = (
        f"{method}\n"         # VERB
        f"\n"                 # Content-Encoding
        f"\n"                 # Content-Language
        f"\n"                 # Content-Length  (empty = 0-byte body)
        f"\n"                 # Content-MD5
        f"\n"                 # Content-Type
        f"\n"                 # Date  (empty — using x-ms-date instead)
        f"\n"                 # If-Modified-Since
        f"\n"                 # If-Match
        f"\n"                 # If-None-Match
        f"\n"                 # If-Unmodified-Since
        f"\n"                 # Range
        f"{canonical_headers}"       # already ends with \n per header
        f"{canonical_resource}"      # no trailing \n
    )

    key_bytes = base64.b64decode(account_key)
    signature = base64.b64encode(
        hmac.new(key_bytes, string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    ).decode()

    return f"SharedKey {account_name}:{signature}"


def create_container(account_name: str, account_key: str,
                     host: str, port: str, container: str) -> None:
    """Send a PUT request to create one blob container."""
    url = f"http://{host}:{port}/{account_name}/{container}?restype=container"
    date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")

    headers = {
        "x-ms-date": date,
        "x-ms-version": "2020-02-10",
    }
    headers["Authorization"] = _shared_key_auth(
        account_name, account_key, "PUT", url, headers
    )

    req = Request(url, method="PUT", headers=headers, data=b"")
    try:
        with urlopen(req) as resp:
            logging.info("Created container '%s' (HTTP %d)", container, resp.status)
    except HTTPError as exc:
        if exc.code == 409:   # ContainerAlreadyExists
            logging.info("Container already exists (skipping): '%s'", container)
        else:
            body = exc.read().decode(errors="replace")
            logging.error(
                "Failed to create '%s': HTTP %d\n%s", container, exc.code, body
            )
            raise


def main() -> None:
    # .strip() removes \r from Windows line endings that corrupt the HMAC key.
    key = (os.environ.get("AZURITE_ACCOUNT_KEY") or AZURITE_DEFAULT_KEY).strip()
    host = os.environ.get("AZURITE_HOST", "azurite")
    port = os.environ.get("AZURITE_PORT", "10000")
    account_name = "devstoreaccount1"

    logging.info("Key length: %d, last 6 chars: ...%s", len(key), key[-6:])
    logging.info("Connecting to http://%s:%s/%s", host, port, account_name)

    for name in CONTAINERS:
        create_container(account_name, key, host, port, name)

    logging.info("Storage initialisation complete.")


if __name__ == "__main__":
    main()
