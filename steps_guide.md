# KSB Azure Functions — Setup & Deployment Guide

> **Assumption:** Your IT team has already created a Resource Group for you.
> You will need its name — referred to as `<your-rg>` throughout this guide.

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

## Part 1 — Install tools on your machine (one-time)

### 1.1 — Node.js

Required to install Azure Functions Core Tools.

Download and install from: **https://nodejs.org** (choose the LTS version)

Verify:
```bash
node --version
```

---

### 1.2 — Azure Functions Core Tools

This gives you the `func` command to run functions locally.

```bash
npm install -g azure-functions-core-tools@4 --unsafe-perm true
```

Verify:
```bash
func --version
# Expected output: 4.x.x
```

---

### 1.3 — Azurite (local Blob Storage emulator)

Azurite pretends to be Azure Blob Storage on your laptop so you can test without touching the real cloud.

```bash
npm install -g azurite
```

---

### 1.4 — Azure CLI

Used to create Azure resources and deploy your function.

Download from: **https://aka.ms/installazurecliwindows**

Verify:
```bash
az --version
```

---

### 1.5 — Python 3.11

Azure Functions works best with Python 3.11.

Download from: **https://python.org** if not already installed.

Verify:
```bash
python --version
# Expected output: Python 3.11.x
```

---

## Part 2 — Set up the project locally

### 2.1 — Create a Python virtual environment

Open a terminal, navigate to the project folder, and run:

```bash
cd C:\Users\bb41\Documents\v2\ksb-azure-functions

# Create a virtual environment called .venv
python -m venv .venv

# Activate it (Windows)
.venv\Scripts\activate

# Your terminal prompt should now start with (.venv)
```

---

### 2.2 — Install Python dependencies

```bash
pip install -r requirements.txt
```

---

### 2.3 — Fill in your local settings

Open `local.settings.json` and fill in your Cosmos DB details.
The storage values stay as-is for local testing (they point to Azurite).

```json
{
  "IsEncrypted": false,
  "Values": {
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "STORAGE_ACCOUNT_NAME": "devstoreaccount1",
    "COSMOS_DB_ENDPOINT": "https://<YOUR-COSMOS-ACCOUNT>.documents.azure.com:443/",
    "COSMOS_DB_KEY": "<YOUR-COSMOS-KEY>",
    "COSMOS_DB_DATABASE": "ksb-db",
    "COSMOS_DB_CONTAINER": "results"
  }
}
```

> You will create the Cosmos DB account in Part 3 and come back to fill this in.
> If you want to test Cosmos DB locally too, install the Cosmos DB Emulator:
> https://aka.ms/cosmosdb-emulator

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
    STORAGE_ACCOUNT_NAME=ksbstorage \
    COSMOS_DB_ENDPOINT=https://ksb-cosmos.documents.azure.com:443/ \
    COSMOS_DB_KEY=$COSMOS_KEY \
    COSMOS_DB_DATABASE=ksb-db \
    COSMOS_DB_CONTAINER=results
```

---

## Part 4 — Test locally before deploying

### 4.1 — Start Azurite (Terminal 1)

Open a new terminal window and run:

```bash
mkdir .azurite
azurite --location .azurite
```

Leave this running. You should see:
```
Azurite Blob service is starting ...
Azurite Blob service is successfully listening at http://127.0.0.1:10000
```

---

### 4.2 — Create local blob containers (Terminal 2)

In a second terminal:

```bash
az storage container create --name uploads --connection-string "UseDevelopmentStorage=true"
az storage container create --name sessions --connection-string "UseDevelopmentStorage=true"
```

---

### 4.3 — Start the function (Terminal 2)

Make sure your virtual environment is active:

```bash
cd C:\Users\bb41\Documents\v2\ksb-azure-functions
.venv\Scripts\activate
func start
```

You should see:
```
Functions:
    process_zip: blobTrigger
```

---

### 4.4 — Upload the sample ZIP to trigger the function

In a third terminal, create a ZIP from the sample folder and upload it:

```powershell
# Create the test ZIP (PowerShell)
Compress-Archive -Path .\sample_zip_file_extracted\* -DestinationPath .\test_upload.zip

# Upload it to local Azurite storage
az storage blob upload `
  --container-name uploads `
  --file test_upload.zip `
  --name test_upload.zip `
  --connection-string "UseDevelopmentStorage=true"
```

Watch Terminal 2. You should see logs like:

```
[Information] Blob trigger fired — blob: 'uploads/test_upload.zip', size: ... bytes
[Information] Session ID: 'test_upload'
[Information] Uploaded 'results.json' → https://devstoreaccount1...
[Information] Uploaded 'crops/photo_crop_0.jpg' → https://devstoreaccount1...
[Information] Cosmos DB upsert succeeded for id='318_springburn_road_...'
[Information] Done. Cosmos DB document id='...' upserted for session='test_upload'.
```

If you see all of the above, the function is working correctly.

---

## Part 5 — Deploy to Azure

Once local testing passes, deploy with one command:

```bash
func azure functionapp publish ksb-func-app
```

You will see output ending with:
```
Deployment successful.
Remote build succeeded!
```

---

### 5.1 — Test in the cloud

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

**`func` command not found**
> Close and reopen your terminal after installing Core Tools.

**Trigger does not fire locally**
> Make sure Azurite is running (Terminal 1) before you start `func start`.

**`CosmosHttpResponseError` in logs**
> Double-check `COSMOS_DB_ENDPOINT` and `COSMOS_DB_KEY` in `local.settings.json`.

**`BlobServiceError` when uploading extracted files**
> Make sure both `uploads` and `sessions` containers exist (Step 4.2).

**Function deploys but does not trigger in Azure**
> Confirm the `uploads` container exists in `ksbstorage` (Step 3.1).
> In the Azure Portal, go to your Function App → Configuration and verify all App Settings are present.
