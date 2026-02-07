FROM python:3.13-slim

WORKDIR /app

# Installiere Dependencies
COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

# Kopiere Server-Code
COPY server.py .

# Exponiere Port
EXPOSE 8000

# Starte Server
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
