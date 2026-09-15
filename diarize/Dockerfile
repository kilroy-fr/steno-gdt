FROM python:3.12-slim

# HF_HOME muss zur Build- und Laufzeit identisch sein, sonst findet die
# Pipeline die vorgeladenen Modelle nicht.
ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/models \
    TORCH_HOME=/models/torch \
    OMP_NUM_THREADS=4

# ffmpeg konvertiert das webm/opus des Browsers nach 16 kHz mono WAV.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Zwei Schritte, Reihenfolge ist relevant: erst CPU-torch aus dem PyTorch-Index,
# danach pyannote — sonst zieht pip die CUDA-Wheels von PyPI nach.
COPY requirements-torch.txt requirements.txt ./
RUN pip install --no-cache-dir -r requirements-torch.txt \
    && pip install --no-cache-dir -r requirements.txt

# Modelle in den Cache backen. Token per BuildKit-Secret, damit er nicht in der
# Image-History landet:  docker compose build diarize  (liest ./.hf_token)
COPY torch_compat.py download_model.py ./
RUN --mount=type=secret,id=hf_token \
    HF_TOKEN="$(cat /run/secrets/hf_token 2>/dev/null)" python download_model.py

# Ab hier kein Netzzugang mehr noetig — verhindert auch, dass die Pipeline zur
# Laufzeit still auf Hugging Face nachladen will.
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

COPY app.py .

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
