# KSB Azure Functions — Setup & Deployment Guide

> **Assumption:** Your IT team has already created a Resource Group for you.
> You will need its name — referred to as `<your-rg>` throughout this guide.

> **Running on a remote machine?** Use the Docker path (Parts 1–3 below).
> Parts 4–6 cover deploying to real Azure once local testing passes.

---

## Two ways to run this locally

| Method | When to use |
|---|---|
| **Docker (recommended)** | Remote machine, IT-managed server, or any machine where Docker is available |
| **func start directly** | Your own laptop with Python + Node.js installed |

This guide covers **Docker first**, then Azure deployment.

---

## Overview of what we are building

```
Mobile App uploads a .zip file
        │
        ▼
  Azure Blob Storage
  └── uploads/my_session.zip   ◄── trigger fires here
        │
        ▼
  Azure Function (process_zip)
  │  1. Extracts the ZIP in memory
  │  2. Uploads files to Blob Storage:
  │       sessions/my_session/results.json
  │       sessions/my_session/annotated/photo_annotated.jpg
  │       sessions/my_session/crops/photo_crop_0.jpg  ...
  │  3. Parses results.json
  └── 4. Writes document to Cosmos DB (with blob HTTPS URLs)
```

---

## Part 1 — Local testing with Docker (recommended for remote machines)

### 1.1 — Install Docker

If Docker is not already installed on your remote machine, ask your IT team to install **Docker Desktop** (Windows/Mac) or **Docker Engine + Docker Compose** (Linux).

Verify both are available:
```bash
docker --version
docker compose version
```

---

### 1.2 — Build and start everything

From the project folder, run:

```bash
docker compose up --build
```

What this does, step by step:
1. **Builds** the function app Docker image from `Dockerfile`
2. **Starts Azurite** (local blob storage) and waits until it is ready
3. **Starts the Cosmos DB emulator** and waits until it is ready (~2 minutes)
4. **Runs `init_storage.py`** to create the `uploads` and `sessions` blob containers
5. **Starts the Azure Function** — it is now listening for ZIP uploads

You should eventually see this line in the logs:
```
ksb-func  | Functions:
ksb-func  |     process_zip: blobTrigger
```

> The Cosmos DB emulator takes about 2 minutes to start on first run.
> Subsequent runs are faster because data is cached in a Docker volume.

---

### 1.3 — Test it by uploading the sample ZIP

Open a **second terminal** on your remote machine (keep `docker compose up` running in the first).

Create a ZIP from the sample folder:

```powershell
# PowerShell
Compress-Archive -Path .\sample_zip_file_extracted\* -DestinationPath .\test_upload.zip
```

```bash
# Or on Linux/Mac
cd sample_zip_file_extracted && zip -r ../test_upload.zip . && cd ..
```

Upload it to the `uploads` container in Azurite:

```bash
# Copy the AZURITE_CONNECTION_STRING value from your .env file, then run:
docker run --rm \
  --network ksb-azure-functions_default \
  mcr.microsoft.com/azure-cli \
  az storage blob upload \
    --container-name uploads \
    --file /dev/stdin \
    --name test_upload.zip \
    --connection-string "<paste AZURITE_CONNECTION_STRING value from your .env>"
```

> **Simpler alternative** — if you have the Azure CLI installed on the same machine:
> ```bash
> # Copy AZURITE_CONNECTION_STRING from .env, change the BlobEndpoint host to localhost
> az storage blob upload \
>   --container-name uploads \
>   --file test_upload.zip \
>   --name test_upload.zip \
>   --connection-string "<paste AZURITE_CONNECTION_STRING from .env, replacing 'azurite' with 'localhost'>"
> ```

Watch the first terminal. You should see:
```
ksb-func  | [Information] Blob trigger fired — blob: 'uploads/test_upload.zip'
ksb-func  | [Information] Session ID: 'test_upload'
ksb-func  | [Information] ZIP read into memory (... bytes)
ksb-func  | [Information] Uploaded 'results.json' → http://localhost:10000/...
ksb-func  | [Information] Uploaded 'crops/photo_crop_0.jpg' → http://localhost:10000/...
ksb-func  | [Information] Cosmos DB upsert succeeded for id='318_springburn_road_...'
ksb-func  | [Information] Done. Cosmos DB document id='...' upserted for session='test_upload'.
```

If you see all of the above — the function is working correctly.

---

### 1.4 — Useful Docker commands

```bash
# View logs from just the function (not all services)
docker compose logs -f func

# Stop all containers (keeps stored data)
docker compose down

# Stop all containers AND delete stored data (fresh start)
docker compose down -v

# Rebuild after code changes
docker compose up --build
```

---

## Part 2 — Install tools for Azure deployment (one-time)

### 2.1 — Azure CLI

Used to create Azure resources and deploy your function.

Download from: **https://aka.ms/installazurecliwindows**

Verify:
```bash
az --version
```

---

### 2.2 — Azure Functions Core Tools

Used for the `func azure functionapp publish` deploy command.

```bash
npm install -g azure-functions-core-tools@4 --unsafe-perm true
```

Verify:
```bash
func --version
# Expected output: 4.x.x
```

> Node.js is required for this. Download from **https://nodejs.org** (LTS version).

---

## Part 3 — Create Azure resources

Log in first:

```bash
az login
# A browser window will open — sign in with your university/work account
```

If you have multiple subscriptions, set the right one:

```bash
az account list --output table
az account set --subscription "<your-subscription-id>"
```

> **From here on, replace `<your-rg>` with the resource group name your IT team gave you.**

---

### 3.1 — Create a Storage Account

The name must be **globally unique**, lowercase, and 3–24 characters (letters and numbers only).

```bash
az storage account create \
  --name ksbstorage \
  --resource-group <your-rg> \
  --location uksouth \
  --sku Standard_LRS
```

> Change `uksouth` to your preferred region if needed.

Create the two blob containers the function uses:

```bash
# "uploads" — where ZIP files land (the trigger watches this)
az storage container create --name uploads --account-name ksbstorage

# "sessions" — where extracted files are stored after processing
az storage container create --name sessions --account-name ksbstorage
```

---

### 3.2 — Create a Cosmos DB account

The account name must also be globally unique.

```bash
az cosmosdb create \
  --name ksb-cosmos \
  --resource-group <your-rg> \
  --locations regionName=uksouth
```

This takes a few minutes. Once done, create the database and container inside it:

```bash
# Create the database
az cosmosdb sql database create \
  --account-name ksb-cosmos \
  --resource-group <your-rg> \
  --name ksb-db

# Create the container (partition key groups documents efficiently)
az cosmosdb sql container create \
  --account-name ksb-cosmos \
  --resource-group <your-rg> \
  --database-name ksb-db \
  --name results \
  --partition-key-path "/sessionId"
```

---

### 3.3 — Create the Function App

```bash
az functionapp create \
  --resource-group <your-rg> \
  --consumption-plan-location uksouth \
  --runtime python \
  --runtime-version 3.11 \
  --functions-version 4 \
  --name ksb-func-app \
  --storage-account ksbstorage \
  --os-type linux
```

> `--name ksb-func-app` must also be globally unique. Change it if needed.

---

### 3.4 — Push secrets to the Function App

The function reads its configuration from environment variables. In Azure these are called "App Settings".

```bash
# Fetch the Cosmos DB primary key automatically
COSMOS_KEY=$(az cosmosdb keys list \
  --name ksb-cosmos \
  --resource-group <your-rg> \
  --query primaryMasterKey \
  --output tsv)

# Push all settings to the Function App in one command
az functionapp config appsettings set \
  --name ksb-func-app \
  --resource-group <your-rg> \
  --settings \
    STORAGE_BLOB_BASE_URL=https://ksbstorage.blob.core.windows.net \
    COSMOS_DB_ENDPOINT=https://ksb-cosmos.documents.azure.com:443/ \
    COSMOS_DB_KEY=$COSMOS_KEY \
    COSMOS_DB_DATABASE=ksb-db \
    COSMOS_DB_CONTAINER=results \
    COSMOS_DB_DISABLE_SSL_VERIFY=false
```

---

## Part 4 — Deploy to Azure

Once local Docker testing passes, deploy with one command:

```bash
func azure functionapp publish ksb-func-app
```

You will see output ending with:
```
Deployment successful.
Remote build succeeded!
```

---

### 4.1 — Test in the cloud

Upload a ZIP to the real `uploads` container:

```bash
az storage blob upload \
  --account-name ksbstorage \
  --container-name uploads \
  --file test_upload.zip \
  --name test_upload.zip
```

Then check the logs in the Azure Portal:
1. Go to **portal.azure.com**
2. Find your Function App `ksb-func-app`
3. Click **Functions** → `process_zip` → **Monitor**
4. You will see each invocation and its logs

---

## Quick reference — resource names used in this guide

| Resource | Name used | Notes |
|---|---|---|
| Resource Group | `<your-rg>` | Provided by your IT team |
| Storage Account | `ksbstorage` | Change if name is taken |
| Blob container (trigger) | `uploads` | ZIP files land here |
| Blob container (output) | `sessions` | Extracted files go here |
| Cosmos DB account | `ksb-cosmos` | Change if name is taken |
| Cosmos DB database | `ksb-db` | |
| Cosmos DB container | `results` | Partition key: `/sessionId` |
| Function App | `ksb-func-app` | Change if name is taken |

---

## Troubleshooting

**Cosmos DB emulator takes too long / never becomes healthy**
> It needs ~2 minutes on first start. Run `docker compose logs cosmosdb` to watch it.
> If your machine has less than 3 GB of free RAM, reduce `AZURE_COSMOS_EMULATOR_PARTITION_COUNT` to `3` in `docker-compose.yml`.

**Trigger does not fire after uploading a ZIP**
> Check `docker compose logs func` for errors.
> Make sure the file name ends in `.zip` — other extensions are silently skipped.

**`BlobServiceError` when uploading extracted files**
> The `init-storage` container may have failed. Run `docker compose logs init-storage`.
> Try `docker compose down -v && docker compose up --build` for a clean restart.

**`CosmosHttpResponseError` in logs**
> Check that `COSMOS_DB_DISABLE_SSL_VERIFY=true` is set in `docker-compose.yml` for local runs.
> The emulator uses a self-signed certificate — SSL verification must be disabled locally.

**Function deploys to Azure but does not trigger**
> Confirm the `uploads` container exists in `ksbstorage` (Part 3.1).
> In the Azure Portal → your Function App → Configuration, verify all App Settings are present.
> Make sure `COSMOS_DB_DISABLE_SSL_VERIFY` is set to `false` (or removed) in Azure.
