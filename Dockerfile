# Base image: Python slim (yar, degdeg ah)
FROM python:3.10-slim

# Install system dependencies (ffmpeg, curl, etc.)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set work directory
WORKDIR /app

# Copy requirements.txt and install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all project files
COPY . .

# Expose the port (Render uses 10000/8080 usually)
EXPOSE 8080

# Command to start your Flask app
CMD ["python", "main.py"]
