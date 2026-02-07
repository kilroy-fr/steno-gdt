#!/usr/bin/env python3
"""
Diktat Server - Orchestriert faster-whisper und ollama
"""
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
import requests
import subprocess
import tempfile
import os
from pathlib import Path
from typing import Optional
import logging

# Logging Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Diktat-Server")

# ============================================
# KONFIGURATION
# ============================================
WHISPER_HOST = os.getenv("WHISPER_HOST", "faster-whisper")
WHISPER_PORT = os.getenv("WHISPER_PORT", "8001")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "ollama")
OLLAMA_PORT = os.getenv("OLLAMA_PORT", "11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

# Whisper-Modell (tiny, base, small, medium, large-v3)
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "de")


# ============================================
# HILFSFUNKTIONEN
# ============================================

def transcribe_audio(audio_path: str) -> str:
    """
    Transkribiert Audio mit faster-whisper Container.
    """
    try:
        # Sende Audio an faster-whisper Container (OpenAI-kompatibles API)
        with open(audio_path, 'rb') as f:
            files = {'file': (Path(audio_path).name, f, 'audio/wav')}
            data = {
                'language': WHISPER_LANGUAGE,
                'response_format': 'text'
            }

            response = requests.post(
                f"http://{WHISPER_HOST}:{WHISPER_PORT}/v1/audio/transcriptions",
                files=files,
                data=data,
                timeout=300
            )
        
        if response.status_code == 200:
            # faster-whisper gibt Text direkt zurück
            return response.text.strip()
        else:
            logger.error(f"Whisper-Fehler {response.status_code}: {response.text}")
            raise HTTPException(status_code=500, detail=f"Whisper-Fehler: {response.status_code}")
            
    except requests.exceptions.ConnectionError:
        logger.error(f"Verbindung zu faster-whisper fehlgeschlagen: {WHISPER_HOST}:{WHISPER_PORT}")
        raise HTTPException(status_code=503, detail="faster-whisper Container nicht erreichbar")
    except Exception as e:
        logger.error(f"Transkriptionsfehler: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def clean_text_with_llm(raw_text: str, model: str = OLLAMA_MODEL) -> str:
    """
    Säubert Text mit ollama (entfernt Füllwörter, behält wörtliche Wiedergabe).
    """
    if not raw_text or raw_text.strip() == "":
        return ""
    
    # Prompt für Textsäuberung
    prompt = f"""Du bist ein Assistent für Transkriptions-Nachbearbeitung.

Aufgabe: Entferne Füllwörter und Sprechpausen, aber behalte die wörtliche Wiedergabe bei.

Regeln:
- Entferne: äh, ähm, hm, also, sozusagen, irgendwie, halt, ne, ja also, etc.
- Behalte: Den genauen Inhalt und die Formulierung bei
- KEINE Umformulierungen oder Zusammenfassungen
- KEINE zusätzlichen Erklärungen
- Korrigiere offensichtliche Transkriptionsfehler
- Gib NUR den gesäuberten Text zurück, keine Meta-Kommentare

Transkribierter Text:
{raw_text}

Gesäuberter Text:"""

    try:
        # Sende an ollama
        response = requests.post(
            f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.3,  # Niedrig für konsistente Ergebnisse
                    "top_p": 0.9,
                    "num_predict": 2048
                }
            },
            timeout=120
        )
        
        if response.status_code == 200:
            result = response.json()
            cleaned = result.get('response', '').strip()
            return cleaned
        else:
            logger.error(f"Ollama-Fehler {response.status_code}: {response.text}")
            # Fallback: Gib rohen Text zurück
            return raw_text
            
    except requests.exceptions.ConnectionError:
        logger.error(f"Verbindung zu ollama fehlgeschlagen: {OLLAMA_HOST}:{OLLAMA_PORT}")
        # Fallback: Gib rohen Text zurück
        return raw_text
    except Exception as e:
        logger.error(f"LLM-Säuberungsfehler: {e}")
        # Fallback: Gib rohen Text zurück
        return raw_text


# ============================================
# API ENDPOINTS
# ============================================

@app.get("/")
async def root():
    """Health Check"""
    return {
        "status": "online",
        "whisper_host": f"{WHISPER_HOST}:{WHISPER_PORT}",
        "ollama_host": f"{OLLAMA_HOST}:{OLLAMA_PORT}",
        "ollama_model": OLLAMA_MODEL
    }


@app.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    """
    Hauptendpoint: Nimmt Audio entgegen, transkribiert und säubert Text.
    """
    logger.info(f"Empfange Audio-Datei: {audio.filename}")
    
    # Speichere temporär
    with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as tmp:
        content = await audio.read()
        tmp.write(content)
        tmp_path = tmp.name
    
    try:
        # 1. Transkribiere mit faster-whisper
        logger.info("Transkribiere mit faster-whisper...")
        raw_text = transcribe_audio(tmp_path)
        logger.info(f"Roher Text: {raw_text[:100]}...")
        
        # 2. Säubere mit LLM
        logger.info("Säubere Text mit LLM...")
        cleaned_text = clean_text_with_llm(raw_text, OLLAMA_MODEL)
        logger.info(f"Gesäuberter Text: {cleaned_text[:100]}...")
        
        return JSONResponse({
            "raw_text": raw_text,
            "cleaned_text": cleaned_text,
            "model": OLLAMA_MODEL
        })
        
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Unerwarteter Fehler: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Lösche temporäre Datei
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@app.get("/health/whisper")
async def health_whisper():
    """Prüfe faster-whisper Container"""
    try:
        response = requests.get(f"http://{WHISPER_HOST}:{WHISPER_PORT}/", timeout=5)
        return {"status": "ok", "code": response.status_code}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/health/ollama")
async def health_ollama():
    """Prüfe ollama Container"""
    try:
        response = requests.get(f"http://{OLLAMA_HOST}:{OLLAMA_PORT}/api/tags", timeout=5)
        models = response.json().get('models', [])
        model_names = [m['name'] for m in models]
        return {
            "status": "ok",
            "models": model_names,
            "configured_model": OLLAMA_MODEL
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
