# Shekel Budget App — Dockerfile
# Lightweight Python image for production deployment.

FROM python:3.12-slim

# Install PostgreSQL client libraries (needed by psycopg2).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user.
RUN useradd --create-home shekel
WORKDIR /home/shekel/app

# Install Python dependencies.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install gunicorn

# Copy application code.
COPY . .

# Switch to non-root user.
RUN chown -R shekel:shekel /home/shekel
USER shekel

EXPOSE 5000

# Run with Gunicorn in production.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "run:app"]
