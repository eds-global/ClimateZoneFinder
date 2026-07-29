# Pinned to Debian bullseye (glibc 2.31) rather than bookworm/trixie (glibc
# 2.36+). Newer glibc creates threads via the clone3() syscall by default,
# falling back to the older clone() only on ENOSYS. Older Docker Engine
# versions' seccomp profiles predate clone3 and block it with a different
# errno instead, which glibc doesn't fall back from — every thread creation
# in the container then fails with "RuntimeError: can't start new thread",
# including in anyio's threadpool (used for every sync FastAPI endpoint) and
# pip's own progress-bar thread. bullseye's older glibc never attempts
# clone3, sidestepping the whole mismatch without weakening the container's
# seccomp sandbox.
FROM python:3.11-slim-bullseye

# Prevent Python from writing pyc files and enable unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Prevent debconf from trying to prompt interactively during automated builds
# (there's no TTY inside `docker build`, which otherwise aborts dpkg entirely)
ENV DEBIAN_FRONTEND=noninteractive

# Set working directory
WORKDIR /app

# NOTE: previously this stage ran `apt-get install libgl1 libglib2.0-0 libsm6
# libxrender1 libxext6` here ("System libs required by matplotlib/Pillow on
# slim images"). Neither is actually true for this codebase: there is no
# opencv import and no explicit GUI backend selection anywhere, so matplotlib
# uses its bundled headless Agg backend (no libGL/X11 needed) and Pillow's
# wheels statically bundle their own image codecs. These packages appear to
# have been leftover from an earlier dependency that's no longer in
# requirements_api.txt. Dropping the apt-get step entirely — restore it (and
# see the git history for the exact package list) only if a real
# "libGL.so.1: cannot open shared object file" style error shows up at
# runtime, which pip install below would surface immediately if it mattered.
#
# To enable PDF export via the output_format=pdf parameter, LibreOffice would
# need to be installed here instead — that still requires an apt-get step.

# Copy requirements first for Docker layer caching
COPY requirements_api.txt .

# --progress-bar off: this host's kernel/container combo refuses new thread
# creation in some cases ("RuntimeError: can't start new thread"), which pip's
# rich-based progress bar renderer hits by spawning a background refresh
# thread. The progress bar is purely cosmetic — disabling it removes that
# thread entirely without affecting the actual install.
RUN pip install --no-cache-dir --progress-bar off -r requirements_api.txt

# Copy SolarGIS data file explicitly (required by Solar PV module)
COPY solargis_country_pv_data.xlsx .

# Copy project files
COPY . .

# Cloud Run uses port 8080
ENV PORT=8080
EXPOSE 8080

# Start FastAPI app
CMD ["sh", "-c", "cd /app && uvicorn report_api:app --host 0.0.0.0 --port ${PORT}"]
