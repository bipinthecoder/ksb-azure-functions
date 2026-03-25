"""
function_app.py
---------------
Azure Functions v2 (Python) — Blob Trigger (ZIP) → Extract → Cosmos DB

High-level flow
===============
1.  A ZIP file is uploaded to the "litter-detection-sbx" container in Blob Storage.
2.  The blob trigger fires and passes the raw ZIP bytes into this function.
3.  The ZIP is extracted in memory (no temp files on disk).
4.  Every file inside the ZIP is uploaded to a dedicated session folder
    inside the "sessions" container.
5.  results.json is parsed and a structured document is upserted into
    Cosmos DB, with blob URLs for every crop and annotated image.
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

app = func.FunctionApp()

SESSIONS_CONTAINER = "sessions"


@app.blob_trigger(
    arg_name="myblob",
    path="litter-detection-sbx/{name}",
    connection="AzureWebJobsStorage",
)
def process_zip(myblob: func.InputStream) -> None:
    logging.info(
        "Blob trigger fired — blob: '%s', size: %d bytes",
        myblob.name,
        myblob.length,
    )

    if not myblob.name.lower().endswith(".zip"):
        logging.info("Skipping non-ZIP blob: '%s'", myblob.name)
        return

    blob_filename = myblob.name.split("/")[-1]
    session_id = os.path.splitext(blob_filename)[0]
    logging.info("Session ID: '%s'", session_id)

    zip_bytes = myblob.read()
    logging.info("ZIP read into memory (%d bytes)", len(zip_bytes))

    try:
        zip_buffer = io.BytesIO(zip_bytes)
        with zipfile.ZipFile(zip_buffer, "r") as zf:
            zip_names = zf.namelist()
            logging.info("ZIP contents: %s", zip_names)

            results_candidates = [
                name
                for name in zip_names
                if name == "results.json" or name.endswith("/results.json")
            ]

            if not results_candidates:
                raise ValueError(
                    f"results.json not found in ZIP '{myblob.name}'. "
                    f"Contents: {zip_names}"
                )

            if len(results_candidates) > 1:
                raise ValueError(
                    f"Multiple results.json files found in ZIP '{myblob.name}': "
                    f"{results_candidates}"
                )

            results_json_path = results_candidates[0]
            logging.info("Using results.json at '%s'", results_json_path)

            results: dict = json.loads(
                zf.read(results_json_path).decode("utf-8")
            )

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

    cosmos_document = _build_cosmos_document(
        results=results,
        session_id=session_id,
        blob_url_map=blob_url_map,
        source_zip_blob=myblob.name,
    )

    _upsert_to_cosmos(cosmos_document)

    logging.info(
        "Done. Cosmos DB document id='%s' upserted for session='%s'.",
        cosmos_document["id"],
        session_id,
    )


def _upload_zip_contents(
    zf: zipfile.ZipFile,
    zip_names: list[str],
    session_id: str,
) -> dict[str, str]:
    connection_string = os.environ["AzureWebJobsStorage"]
    blob_service = BlobServiceClient.from_connection_string(connection_string)

    blob_base_url = os.environ["STORAGE_BLOB_BASE_URL"].rstrip("/")

    blob_url_map: dict[str, str] = {}

    for zip_path in zip_names:
        if zip_path.endswith("/"):
            continue

        blob_dest_path = f"{session_id}/{zip_path}"
        file_bytes = zf.read(zip_path)

        blob_client = blob_service.get_blob_client(
            container=SESSIONS_CONTAINER,
            blob=blob_dest_path,
        )
        blob_client.upload_blob(
            data=file_bytes,
            overwrite=True,
        )

        blob_url = f"{blob_base_url}/{SESSIONS_CONTAINER}/{blob_dest_path}"
        blob_url_map[zip_path] = blob_url

        logging.info("Uploaded '%s' → '%s'", zip_path, blob_url)

    return blob_url_map


def _guess_content_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "json": "application/json",
        "mp4": "video/mp4",
    }.get(ext, "application/octet-stream")


def _build_cosmos_document(
    results: dict,
    session_id: str,
    blob_url_map: dict[str, str],
    source_zip_blob: str,
) -> dict:
    raw_id = results.get("videoName") or str(uuid.uuid4())
    document_id = (
        raw_id.replace("/", "_")
              .replace("\\", "_")
              .replace("?", "_")
              .replace("#", "_")
              .replace(" ", "_")
    )

    frames_mapped = [
        _map_frame(frame, blob_url_map, session_id)
        for frame in results.get("frames", [])
    ]

    unique_detections_mapped = _map_detections(
        results.get("uniqueDetections", []), blob_url_map, session_id
    )

    return {
        "id": document_id,
        "orgId": results.get("orgId"),
        "sessionId": session_id,
        "sourceZip": source_zip_blob,
        "ingestedAt": datetime.now(timezone.utc).isoformat(),
        "videoName": results.get("videoName"),
        "videoUri": results.get("videoUri"),
        "isPhoto": results.get("isPhoto"),
        "type": results.get("type"),
        "processedAt": results.get("processedAt"),
        "videoDurationMs": results.get("videoDurationMs"),
        "date": results.get("date"),
        "streetName": results.get("streetName"),
        "location": results.get("location"),
        "surveyedBy": results.get("surveyedBy"),
        "pipelineConfig": results.get("pipelineConfig"),
        "summary": results.get("summary"),
        "frames": frames_mapped,
        "uniqueDetections": unique_detections_mapped,
    }


def _map_frame(frame: dict, blob_url_map: dict[str, str], session_id: str) -> dict:
    annotated_zip_path = frame.get("annotatedPath")

    frame_uri = frame.get("framePath", "")
    frame_filename = frame_uri.split("/")[-1] if frame_uri else None
    frame_zip_path = f"frames/{frame_filename}" if frame_filename else None

    return {
        "frameNumber": frame.get("frameNumber"),
        "timeMs": frame.get("timeMs"),
        "frameIndex": frame.get("frameIndex"),
        "frameBlobUrl": blob_url_map.get(frame_zip_path) if frame_zip_path else None,
        "frameBlobPath": f"{session_id}/{frame_zip_path}" if frame_zip_path else None,
        "annotatedBlobUrl": blob_url_map.get(annotated_zip_path) if annotated_zip_path else None,
        "annotatedBlobPath": f"{session_id}/{annotated_zip_path}" if annotated_zip_path else None,
        "width": frame.get("width"),
        "height": frame.get("height"),
        "blurScore": frame.get("blurScore"),
        "privacy": frame.get("privacy"),
        "detectionCount": frame.get("detectionCount"),
        "detections": _map_detections(
            frame.get("detections", []), blob_url_map, session_id
        ),
    }


def _map_detections(
    detections: list, blob_url_map: dict[str, str], session_id: str
) -> list:
    mapped = []
    for det in detections:
        crop_zip_path = det.get("cropPath", "")
        crop_filename = det.get("cropFileName", "")

        mapped.append({
            "detectionId": det.get("detectionId"),
            "bbox": det.get("bbox"),
            "bboxPadded": det.get("bboxPadded"),
            "cropFileName": crop_filename,
            "cropBlobUrl": blob_url_map.get(crop_zip_path) if crop_zip_path else None,
            "cropBlobPath": f"{session_id}/{crop_zip_path}" if crop_zip_path else None,
            "detector": det.get("detector"),
            "classification": det.get("classification"),
        })

    return mapped


def _upsert_to_cosmos(document: dict) -> None:
    endpoint = os.environ["COSMOS_DB_ENDPOINT"]
    key = os.environ["COSMOS_DB_KEY"]
    db_name = os.environ["COSMOS_DB_DATABASE"]
    container_name = os.environ["COSMOS_DB_CONTAINER"]

    disable_ssl = os.environ.get("COSMOS_DB_DISABLE_SSL_VERIFY", "false").lower() == "true"

    client = CosmosClient(
        url=endpoint,
        credential=key,
        connection_verify=not disable_ssl,
    )

    database = client.create_database_if_not_exists(id=db_name)
    container = database.create_container_if_not_exists(
        id=container_name,
        partition_key=PartitionKey(path="/orgId"),
    )

    try:
        container.upsert_item(document)
        logging.info("Cosmos DB upsert succeeded for id='%s'", document["id"])
    except exceptions.CosmosHttpResponseError as exc:
        logging.error(
            "Cosmos DB upsert FAILED for id='%s': status=%s message=%s",
            document.get("id"),
            exc.status_code,
            exc.message,
        )
        raise