"""
function_app.py
---------------
Azure Functions v2 (Python) — Blob Trigger (ZIP) → Extract → Cosmos DB

High-level flow
===============
1.  A ZIP file is uploaded to the "uploads" container in Blob Storage.
2.  The blob trigger fires and passes the raw ZIP bytes into this function.
3.  The ZIP is extracted in memory (no temp files on disk).
4.  Every file inside the ZIP is uploaded to a dedicated session folder
    inside the "sessions" container:
        sessions/<session_id>/results.json
        sessions/<session_id>/annotated/photo_annotated.jpg
        sessions/<session_id>/crops/photo_crop_0.jpg
        sessions/<session_id>/crops/photo_crop_1.jpg
        ...
5.  results.json is parsed and a structured document is upserted into
    Cosmos DB, with HTTPS blob URLs for every crop and annotated image.
    The actual image files remain in Blob Storage — only their URLs are
    stored in Cosmos DB.

Environment variables (set in local.settings.json for local dev,
or as App Settings in Azure):
    AzureWebJobsStorage        — storage account connection string (trigger + SDK)
    STORAGE_BLOB_BASE_URL      — base URL used to build blob HTTPS URLs stored in Cosmos DB
                                  Local Docker : http://localhost:10000/devstoreaccount1
                                  Azure        : https://<account>.blob.core.windows.net
    COSMOS_DB_ENDPOINT         — https://<account>.documents.azure.com:443/
    COSMOS_DB_KEY              — Cosmos DB primary or secondary key
    COSMOS_DB_DATABASE         — Cosmos DB database name
    COSMOS_DB_CONTAINER        — Cosmos DB container (collection) name
    COSMOS_DB_DISABLE_SSL_VERIFY — set to "true" when using the local Cosmos DB emulator
"""

import io
import json
import logging
import os
import uuid
import zipfile
from datetime import datetime, timezone

import azure.functions as func
from azure.cosmos import CosmosClient, PartitionKey, exceptions
from azure.storage.blob import BlobServiceClient

# ---------------------------------------------------------------------------
# Create the Function App (Python v2 programming model)
# ---------------------------------------------------------------------------
app = func.FunctionApp()

# Name of the blob container where extracted files will be stored.
# The trigger container ("uploads") stays separate so we don't re-trigger
# on the files we upload during extraction.
SESSIONS_CONTAINER = "sessions"


# ---------------------------------------------------------------------------
# Blob Trigger — fires when any blob lands in the "uploads" container
# ---------------------------------------------------------------------------
@app.blob_trigger(
    arg_name="myblob",
    # "uploads/{name}" matches every blob in the "uploads" container.
    # {name} captures the blob's name (e.g. "abc123.zip").
    # We filter for .zip inside the function so non-zip uploads are skipped
    # gracefully rather than causing a binding error.
    path="uploads/{name}",
    connection="AzureWebJobsStorage",
)
def process_zip(myblob: func.InputStream) -> None:
    """
    Entry point called by the Azure Functions runtime when a blob appears
    (or is updated) in the "uploads" container.

    Args:
        myblob: Readable stream of the triggering blob's bytes.
    """
    logging.info(
        "Blob trigger fired — blob: '%s', size: %d bytes",
        myblob.name,
        myblob.length,
    )

    # ------------------------------------------------------------------
    # Guard: only process ZIP files; silently skip everything else
    # (e.g. log files, metadata blobs the SDK may write automatically)
    # ------------------------------------------------------------------
    if not myblob.name.lower().endswith(".zip"):
        logging.info("Skipping non-ZIP blob: '%s'", myblob.name)
        return

    # ------------------------------------------------------------------
    # Derive a session ID from the ZIP filename (strip the .zip extension).
    # e.g.  uploads/318_springburn_road_abc123.zip  →  session_id = "318_springburn_road_abc123"
    # This becomes the folder name inside the "sessions" container.
    # ------------------------------------------------------------------
    blob_filename = myblob.name.split("/")[-1]          # "318_springburn_road_abc123.zip"
    session_id = os.path.splitext(blob_filename)[0]     # "318_springburn_road_abc123"
    logging.info("Session ID: '%s'", session_id)

    # ------------------------------------------------------------------
    # Read the entire ZIP into memory so we can work with it without
    # writing anything to the function's (ephemeral) local disk.
    # ------------------------------------------------------------------
    zip_bytes = myblob.read()
    logging.info("ZIP read into memory (%d bytes)", len(zip_bytes))

    # ------------------------------------------------------------------
    # Open the in-memory ZIP and extract its contents
    # ------------------------------------------------------------------
    try:
        zip_buffer = io.BytesIO(zip_bytes)     # wrap bytes in a file-like object
        with zipfile.ZipFile(zip_buffer, "r") as zf:
            # Validate that results.json is present before doing any uploads
            zip_names = zf.namelist()          # list of all paths inside the ZIP
            logging.info("ZIP contents: %s", zip_names)

            if "results.json" not in zip_names:
                raise ValueError(
                    f"results.json not found in ZIP '{myblob.name}'. "
                    f"Contents: {zip_names}"
                )

            # Read results.json into a Python dict
            results: dict = json.loads(zf.read("results.json").decode("utf-8"))

            # Upload every file from the ZIP to Blob Storage and collect
            # a mapping of zip-internal path → HTTPS URL for later use
            blob_url_map = _upload_zip_contents(
                zf=zf,
                zip_names=zip_names,
                session_id=session_id,
            )

    except zipfile.BadZipFile as exc:
        logging.error("'%s' is not a valid ZIP file: %s", myblob.name, exc)
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logging.error("Failed to parse results.json inside '%s': %s", myblob.name, exc)
        raise

    # ------------------------------------------------------------------
    # Build the Cosmos DB document from parsed results + blob URLs
    # ------------------------------------------------------------------
    cosmos_document = _build_cosmos_document(
        results=results,
        session_id=session_id,
        blob_url_map=blob_url_map,
        source_zip_blob=myblob.name,
    )

    # ------------------------------------------------------------------
    # Upsert the document into Cosmos DB
    # Upsert = insert if new, replace if already exists (idempotent)
    # ------------------------------------------------------------------
    _upsert_to_cosmos(cosmos_document)

    logging.info(
        "Done. Cosmos DB document id='%s' upserted for session='%s'.",
        cosmos_document["id"],
        session_id,
    )


# ---------------------------------------------------------------------------
# Step A: Upload all files from the ZIP to Blob Storage
# ---------------------------------------------------------------------------
def _upload_zip_contents(
    zf: zipfile.ZipFile,
    zip_names: list[str],
    session_id: str,
) -> dict[str, str]:
    """
    Upload every file inside the ZIP to the "sessions" container in Blob Storage,
    preserving the original directory structure under a session-scoped prefix.

    Destination path pattern:
        sessions/<session_id>/<zip_internal_path>
    e.g.
        sessions/abc123/results.json
        sessions/abc123/annotated/photo_annotated.jpg
        sessions/abc123/crops/photo_crop_0.jpg

    Args:
        zf:         Open ZipFile object to read from.
        zip_names:  List of all paths inside the ZIP (from zf.namelist()).
        session_id: Folder name to scope all uploads for this session.

    Returns:
        A dict mapping each zip-internal path to its HTTPS Blob Storage URL.
        e.g. {"crops/photo_crop_0.jpg": "https://..."}
    """
    # Build a BlobServiceClient from the same connection string used by the trigger
    connection_string = os.environ["AzureWebJobsStorage"]
    blob_service = BlobServiceClient.from_connection_string(connection_string)

    # STORAGE_BLOB_BASE_URL controls the URL prefix stored in Cosmos DB.
    # Use the full Azure URL in production; use the localhost Azurite URL locally.
    # e.g. locally:  http://localhost:10000/devstoreaccount1
    #      in Azure: https://ksbstorage.blob.core.windows.net
    blob_base_url = os.environ["STORAGE_BLOB_BASE_URL"].rstrip("/")

    blob_url_map: dict[str, str] = {}

    for zip_path in zip_names:
        # Skip directory entries (they have no file content)
        if zip_path.endswith("/"):
            continue

        # Destination blob path inside the "sessions" container
        blob_dest_path = f"{session_id}/{zip_path}"     # e.g. "abc123/crops/photo_crop_0.jpg"

        # Read the file bytes from the ZIP (in memory — no disk I/O)
        file_bytes = zf.read(zip_path)

        # Upload to Blob Storage (overwrite=True makes this idempotent on re-upload)
        blob_client = blob_service.get_blob_client(
            container=SESSIONS_CONTAINER,
            blob=blob_dest_path,
        )
        blob_client.upload_blob(
            data=file_bytes,
            overwrite=True,     # safe to re-run — replaces existing blob instead of failing
        )

        # Build the public URL for this blob using the configured base URL.
        # Format: <STORAGE_BLOB_BASE_URL>/<container>/<session_id>/<zip_internal_path>
        blob_url = f"{blob_base_url}/{SESSIONS_CONTAINER}/{blob_dest_path}"
        blob_url_map[zip_path] = blob_url

        logging.info("Uploaded '%s' → '%s'", zip_path, blob_url)

    return blob_url_map


def _guess_content_type(filename: str) -> str:
    """
    Return a basic MIME type based on the file extension.
    Used when uploading blobs so they are served with the correct Content-Type.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "json": "application/json",
        "mp4": "video/mp4",
    }.get(ext, "application/octet-stream")


# ---------------------------------------------------------------------------
# Step B: Build the Cosmos DB document
# ---------------------------------------------------------------------------
def _build_cosmos_document(
    results: dict,
    session_id: str,
    blob_url_map: dict[str, str],
    source_zip_blob: str,
) -> dict:
    """
    Assemble the Cosmos DB document that will be upserted.

    All local device file:// paths in results.json are replaced with the
    HTTPS Blob Storage URLs from blob_url_map.  The actual image files
    stay in Blob Storage; Cosmos DB only stores the URLs.

    Args:
        results:         Parsed results.json dict.
        session_id:      Session folder name (derived from the ZIP filename).
        blob_url_map:    zip-internal path → HTTPS URL mapping from Step A.
        source_zip_blob: Blob path of the original ZIP (for provenance).

    Returns:
        A dict ready to be written to Cosmos DB.
    """
    # Use videoName as the Cosmos DB document ID for natural idempotency.
    # Sanitise characters that Cosmos DB forbids in IDs: / \ ? #
    raw_id = results.get("videoName") or str(uuid.uuid4())
    document_id = (
        raw_id.replace("/", "_")
              .replace("\\", "_")
              .replace("?", "_")
              .replace("#", "_")
              .replace(" ", "_")
    )

    # Map per-frame data, replacing device file:// paths with blob URLs
    frames_mapped = [
        _map_frame(frame, blob_url_map, session_id)
        for frame in results.get("frames", [])
    ]

    # Map uniqueDetections list, replacing cropPath with blob URLs
    unique_detections_mapped = _map_detections(
        results.get("uniqueDetections", []), blob_url_map, session_id
    )

    return {
        # Cosmos DB requires a string "id" field on every document
        "id": document_id,

        # ---- Partition key — every document must have this field ----
        # The Cosmos DB container is partitioned by /orgId, so this field
        # must be present and non-null for efficient storage and querying.
        "orgId": results.get("orgId"),

        # ---- Session / ingestion provenance ----
        "sessionId": session_id,
        "sourceZip": source_zip_blob,           # which ZIP file produced this document
        "ingestedAt": datetime.now(timezone.utc).isoformat(),  # when this function ran

        # ---- Top-level submission fields (copied directly from results.json) ----
        "videoName": results.get("videoName"),
        "videoUri": results.get("videoUri"),        # original device URI (kept for reference)
        "isPhoto": results.get("isPhoto"),
        "type": results.get("type"),                # "photo" or "video"
        "processedAt": results.get("processedAt"),  # timestamp from the mobile ML pipeline
        "videoDurationMs": results.get("videoDurationMs"),
        "date": results.get("date"),                # survey date/time from the surveyor
        "streetName": results.get("streetName"),

        # ---- Geolocation (start/end GPS coordinates + street name) ----
        "location": results.get("location"),

        # ---- Surveyor identity ----
        "surveyedBy": results.get("surveyedBy"),    # {uid, email, displayName}

        # ---- ML pipeline settings used when the ZIP was produced ----
        "pipelineConfig": results.get("pipelineConfig"),

        # ---- High-level detection counts and class breakdown ----
        "summary": results.get("summary"),

        # ---- Per-frame data with blob URLs resolved ----
        "frames": frames_mapped,

        # ---- Deduplicated detections across all frames ----
        "uniqueDetections": unique_detections_mapped,
    }


def _map_frame(frame: dict, blob_url_map: dict[str, str], session_id: str) -> dict:
    """
    Convert one frame object from results.json into a Cosmos DB-ready dict.

    Replaces:
      - framePath  (device file:// URI)  → frameBlobUrl  (HTTPS)
      - annotatedPath (relative path)    → annotatedBlobUrl (HTTPS)

    The per-frame detections list is also mapped to include crop blob URLs.

    Args:
        frame:        Raw frame dict from results.json.
        blob_url_map: zip-internal path → HTTPS URL.
        session_id:   Session folder name (for the container-relative path).

    Returns:
        Dict with blob URLs added.
    """
    # annotatedPath inside the ZIP is e.g. "annotated/photo_annotated.jpg"
    annotated_zip_path = frame.get("annotatedPath")     # "annotated/photo_annotated.jpg"

    # framePath is a device URI — extract just the filename to build a blob path.
    # The original image file sits at frames/<filename> inside the ZIP if it exists;
    # for photos the annotated version in annotated/ is the deliverable.
    frame_uri = frame.get("framePath", "")
    frame_filename = frame_uri.split("/")[-1] if frame_uri else None  # "abc.jpg"
    frame_zip_path = f"frames/{frame_filename}" if frame_filename else None

    return {
        "frameNumber": frame.get("frameNumber"),
        "timeMs": frame.get("timeMs"),
        "frameIndex": frame.get("frameIndex"),

        # HTTPS URL for the original frame image in Blob Storage (if uploaded)
        "frameBlobUrl": blob_url_map.get(frame_zip_path) if frame_zip_path else None,
        # Container-relative path, useful for direct SDK access
        "frameBlobPath": f"{session_id}/{frame_zip_path}" if frame_zip_path else None,

        # HTTPS URL for the annotated (bounding-box overlay) image
        "annotatedBlobUrl": blob_url_map.get(annotated_zip_path) if annotated_zip_path else None,
        "annotatedBlobPath": f"{session_id}/{annotated_zip_path}" if annotated_zip_path else None,

        # Frame dimensions in pixels
        "width": frame.get("width"),
        "height": frame.get("height"),
        "blurScore": frame.get("blurScore"),        # motion blur quality score

        # Privacy masking summary (faces and licence plates blurred)
        "privacy": frame.get("privacy"),

        # Total number of litter detections in this frame
        "detectionCount": frame.get("detectionCount"),

        # All detections for this frame with crop URLs resolved
        "detections": _map_detections(
            frame.get("detections", []), blob_url_map, session_id
        ),
    }


def _map_detections(
    detections: list, blob_url_map: dict[str, str], session_id: str
) -> list:
    """
    Convert a list of detection dicts, replacing each local cropPath with
    the corresponding HTTPS Blob Storage URL.

    Crop images are stored under crops/ inside the ZIP, which is then
    uploaded to sessions/<session_id>/crops/ in Blob Storage.

    Args:
        detections:   List of raw detection dicts from results.json.
        blob_url_map: zip-internal path → HTTPS URL.
        session_id:   Session folder name (for the container-relative path).

    Returns:
        List of dicts with cropBlobUrl and cropBlobPath added.
    """
    mapped = []
    for det in detections:
        # cropPath is the zip-internal relative path, e.g. "crops/photo_crop_0.jpg"
        crop_zip_path = det.get("cropPath", "")         # "crops/photo_crop_0.jpg"
        crop_filename = det.get("cropFileName", "")     # "photo_crop_0.jpg"

        mapped.append({
            "detectionId": det.get("detectionId"),

            # Raw bounding box coordinates (float pixels, unpadded)
            "bbox": det.get("bbox"),

            # Padded bounding box used to generate the crop image
            "bboxPadded": det.get("bboxPadded"),

            "cropFileName": crop_filename,

            # Full HTTPS URL to the crop image in Blob Storage
            "cropBlobUrl": blob_url_map.get(crop_zip_path) if crop_zip_path else None,
            # Container-relative path for direct SDK/SAS access
            "cropBlobPath": f"{session_id}/{crop_zip_path}" if crop_zip_path else None,

            # Detector model raw confidence score
            "detector": det.get("detector"),

            # Three classification dimensions: semantic label, material, coarse category
            "classification": det.get("classification"),
        })

    return mapped


# ---------------------------------------------------------------------------
# Step C: Upsert document into Cosmos DB
# ---------------------------------------------------------------------------
def _upsert_to_cosmos(document: dict) -> None:
    """
    Write (or overwrite) a document in the configured Cosmos DB container.

    Uses upsert_item so that re-processing the same ZIP (e.g. after a fix)
    updates the existing record rather than creating a duplicate.

    Connection config is read from environment variables:
        COSMOS_DB_ENDPOINT   — e.g. https://<account>.documents.azure.com:443/
        COSMOS_DB_KEY        — primary or secondary key
        COSMOS_DB_DATABASE   — database name
        COSMOS_DB_CONTAINER  — container (collection) name

    Args:
        document: Cosmos DB document dict; must contain a string "id" field.

    Raises:
        CosmosHttpResponseError if the write fails (e.g. wrong key, quota exceeded).
    """
    endpoint = os.environ["COSMOS_DB_ENDPOINT"]
    key = os.environ["COSMOS_DB_KEY"]
    db_name = os.environ["COSMOS_DB_DATABASE"]
    container_name = os.environ["COSMOS_DB_CONTAINER"]

    # COSMOS_DB_DISABLE_SSL_VERIFY should be "true" only when using the local
    # Cosmos DB emulator (which uses a self-signed certificate).
    # Never set this to true in production.
    disable_ssl = os.environ.get("COSMOS_DB_DISABLE_SSL_VERIFY", "false").lower() == "true"

    # CosmosClient is lightweight — no persistent connection is held
    client = CosmosClient(url=endpoint, credential=key, connection_verify=not disable_ssl)

    # create_database_if_not_exists / create_container_if_not_exists mean the
    # function is self-bootstrapping: first run creates the DB and container
    # automatically, both locally and in Azure.
    database = client.create_database_if_not_exists(id=db_name)
    container = database.create_container_if_not_exists(
        id=container_name,
        # Partition key matches the container configured in Azure Cosmos DB.
        # All documents must include an "orgId" field.
        partition_key=PartitionKey(path="/orgId"),
    )

    try:
        container.upsert_item(document)
        logging.info("Cosmos DB upsert succeeded for id='%s'", document["id"])
    except exceptions.CosmosHttpResponseError as exc:
        # Log full error details so we can diagnose (wrong endpoint, bad key, etc.)
        logging.error(
            "Cosmos DB upsert FAILED for id='%s': status=%s message=%s",
            document.get("id"),
            exc.status_code,
            exc.message,
        )
        raise  # re-raise so the Functions runtime marks this invocation as failed
