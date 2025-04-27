# Dockerfile
# Use an official Python runtime as a parent image
FROM python:3.11-slim as base

# Set environment variables to prevent Python from writing pyc files
ENV PYTHONDONTWRITEBYTECODE 1
# Keep Python output unbuffered
ENV PYTHONUNBUFFERED 1

# Set the working directory in the container
WORKDIR /app

# Install system dependencies if needed (e.g., for certain libraries)
# RUN apt-get update && apt-get install -y --no-install-recommends some-package && rm -rf /var/lib/apt/lists/*

# --- Builder stage (optional, but good practice for smaller final images) ---
FROM base as builder
# Install build dependencies if any (e.g., for compiling C extensions)
# RUN pip install --upgrade pip setuptools wheel

# Copy the requirements file into the container
COPY requirements.txt .

# Install Python dependencies specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# --- Final stage ---
FROM base as final


# Copy installed dependencies from the builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy the rest of the application code into the container
COPY . .

# Create the data directory and set permissions if necessary
# Docker volumes usually handle permissions, but explicit creation can be safer
RUN mkdir -p /data && chown -R nobody:nogroup /data
USER nobody

# Specify the command to run on container startup
CMD ["python", "bot.py"]