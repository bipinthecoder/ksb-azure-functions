# init.Dockerfile
# ---------------
# Minimal image for the init-storage container.
# Uses plain Python (not the Azure Functions base image) to avoid any
# environment variables baked into the Functions image that could
# interfere with Azure Storage SDK authentication against Azurite.

FROM python:3.11-slim

# Install only what init_storage.py needs
RUN pip install --no-cache-dir azure-storage-blob==12.19.0

COPY init_storage.py /init_storage.py

CMD ["python", "/init_storage.py"]
