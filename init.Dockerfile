# init.Dockerfile
# ---------------
# Minimal image for the init-storage container.
# Uses plain Python (not the Azure Functions base image) to avoid any
# environment variables baked into the Functions image that could
# interfere with Azure Storage SDK authentication against Azurite.

FROM python:3.11-slim

# init_storage.py uses only Python stdlib (hmac, hashlib, urllib) —
# no third-party packages needed.

COPY init_storage.py /init_storage.py

CMD ["python", "/init_storage.py"]
