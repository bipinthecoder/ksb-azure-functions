#!/bin/bash
# upload_test.sh
# --------------
# Uploads the sample ZIP into the Azurite "litter-detection-sbx" container from inside
# the running func container — no GUI or Azure CLI required.
#
# Usage:
#   bash upload_test.sh                        # uses sample_zip_file_extracted/
#   bash upload_test.sh /path/to/your.zip      # uploads a specific zip file

set -e

BLOB_NAME="${2:-test_upload.zip}"   # name it will have in the container

if [ -n "$1" ]; then
  # A real zip file was passed — copy it into the container first
  echo "Copying $1 into container..."
  docker cp "$1" ksb-func:/tmp/upload_target.zip
  ZIP_PATH="/tmp/upload_target.zip"
else
  # No file passed — zip up the sample folder inside the container
  echo "Creating zip from sample_zip_file_extracted/ ..."
  docker exec ksb-func bash -c "
    cd /home/site/wwwroot && \
    zip -r /tmp/upload_target.zip sample_zip_file_extracted/
  "
  ZIP_PATH="/tmp/upload_target.zip"
fi

echo "Uploading '$BLOB_NAME' to Azurite litter-detection-sbx container..."
docker exec ksb-func python3 -c "
import os
from azure.storage.blob import BlobServiceClient
client = BlobServiceClient.from_connection_string(os.environ['AzureWebJobsStorage'])
with open('${ZIP_PATH}', 'rb') as f:
    client.get_blob_client('litter-detection-sbx', '${BLOB_NAME}').upload_blob(f, overwrite=True)
print('Done — blob uploaded as: ${BLOB_NAME}')
print('Run: docker compose logs -f func  to watch the trigger.')
"
