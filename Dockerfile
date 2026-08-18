FROM python:3-slim

WORKDIR /app

# Install standard networking libraries
RUN pip install --no-cache-dir flask requests

# Copy our app script into the container
COPY app.py .

# Expose Flask's internal port
EXPOSE 5000

# Start the server
CMD ["python", "app.py"]
