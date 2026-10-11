FROM python:3.13-slim

WORKDIR /app

COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

COPY server.py .
COPY prompts.py .
COPY medical_vocab.py .
COPY gdt.py .
COPY fhir.py .
COPY templates/ templates/
COPY static/ static/

EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
