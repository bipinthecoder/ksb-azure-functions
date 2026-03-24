# init.Dockerfile
# ---------------
# Minimal image for the init-storage container.

FROM python:3.11-slim

WORKDIR /

COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

COPY init_storage.py /init_storage.py

CMD ["python", "/init_storage.py"]