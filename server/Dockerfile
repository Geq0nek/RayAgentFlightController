# Use the same base image you had
FROM python:3.11

# Install build dependencies (keep this, it's good for performance/compiling)
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory inside the container
WORKDIR /workspace

# Copy requirements first to leverage Docker cache
COPY requirements.txt ./server/
RUN pip install --no-cache-dir -r ./server/requirements.txt

# Copy all files from the /server directory to the container
COPY . ./server/

# Expose the Flask port and Ray dashboard port
EXPOSE 5000
EXPOSE 8265

# Start the application
CMD ["python", "main.py"]