FROM python:3.12-slim

# HF_HOME muss zur Build- und Laufzeit identisch sein, sonst findet die
# Pipeline die vorgeladenen Modelle nicht.
ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/models \
    TORCH_HOME=/models/torch

# Bewusst KEIN OMP_NUM_THREADS mehr: die Variable deckelte den Container still
# auf 4 der 32 Kerne und liess nebenbei `nproc` 4 melden, was die Fehlersuche
# verschleiert hat. Die Threadzahl setzt app.py ueber TORCH_THREADS — und nur
# noch im CPU-Fall, auf der GPU ist sie bedeutungslos.

# ffmpeg konvertiert das webm/opus des Browsers nach 16 kHz mono WAV.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Welche Karte die Diarisation rechnet, entscheidet allein dieses Argument:
#   CPU        https://download.pytorch.org/whl/cpu   (Default, kleinstes Image)
#   NVIDIA     https://download.pytorch.org/whl/cu128 (Blackwell/RTX 5080 = sm_120)
#   Intel Arc  https://download.pytorch.org/whl/xpu
# Der Default bleibt CPU, damit ein Build ohne docker-compose.yml lauffaehig
# bleibt — das Projekt setzt das Argument dort.
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

# Zwei Schritte, Reihenfolge ist relevant: erst torch aus dem PyTorch-Index,
# danach pyannote — sonst zieht pip die Wheels von PyPI nach und ueberschreibt
# die gerade installierte Variante.
COPY requirements-torch.txt requirements.txt ./
RUN pip install --no-cache-dir --index-url "$TORCH_INDEX_URL" -r requirements-torch.txt \
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
