FROM python:3.12-slim

# Create a non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install runtime dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Change ownership of the app directory to the non-root user
RUN chown -R appuser:appuser /app

# Switch to the non-root user
USER appuser

EXPOSE 8000

CMD ["python", "main.py"]
