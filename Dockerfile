# Use Python 3.10 or higher as base image
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install system dependencies required for numpy/pandas
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy only the requirements file first
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Create necessary directories with appropriate permissions
RUN mkdir -p /app/data /app/logs \
    && chmod 777 /app/data /app/logs

# Copy project files (excluding what's in .dockerignore)
COPY . .

# Create a non-root user for security
RUN useradd -m appuser \
    && chown -R appuser:appuser /app
USER appuser

# Set Python to run in unbuffered mode
ENV PYTHONUNBUFFERED=1

# Command to run the application
CMD ["python", "main.py"]