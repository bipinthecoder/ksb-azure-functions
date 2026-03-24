# -----------------------------------------------------------------------
# Dockerfile — KSB Azure Functions (Python v2)
# -----------------------------------------------------------------------
# Uses Microsoft's official Azure Functions base image for Python 3.11.
# The base image already includes the Functions host runtime, so we only
# need to install our Python dependencies and copy our code on top.
# -----------------------------------------------------------------------

FROM mcr.microsoft.com/azure-functions/python:4-python3.11

# AzureWebJobsScriptRoot  — tells the Functions host where to find our code
# AzureFunctionsJobHost__Logging__Console__IsEnabled — streams logs to stdout
#   so they appear in "docker compose logs"
ENV AzureWebJobsScriptRoot=/home/site/wwwroot \
    AzureFunctionsJobHost__Logging__Console__IsEnabled=true

# Copy and install Python dependencies first.
# Doing this before copying source code means Docker can cache this layer —
# a code change won't re-run pip install unless requirements.txt also changes.
COPY requirements.txt /
RUN pip install --no-cache-dir -r /requirements.txt

# Copy all source files into the working directory the Functions host expects
COPY . /home/site/wwwroot
