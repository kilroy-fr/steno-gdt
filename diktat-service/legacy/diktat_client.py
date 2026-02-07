#!/usr/bin/env python3
"""
Diktat Client - Sendet Audio zum Server und empfängt Text
"""
import sys
import requests
from pathlib import Path


def send_audio_to_server(audio_file: str, server_url: str) -> str:
    """
    Sendet Audio-Datei zum Server und gibt transkribierten Text zurück.
    
    Args:
        audio_file: Pfad zur Audio-Datei
        server_url: Server-URL (z.B. http://192.168.1.100:8000)
    
    Returns:
        Transkribierter und gesäuberter Text
    """
    audio_path = Path(audio_file)
    
    # Prüfe ob Datei existiert
    if not audio_path.exists():
        print(f"ERROR: Audio-Datei nicht gefunden: {audio_file}", file=sys.stderr)
        return ""
    
    # Prüfe Dateigröße (min 1KB)
    if audio_path.stat().st_size < 1000:
        print(f"ERROR: Audio-Datei zu klein (möglicherweise leer)", file=sys.stderr)
        return ""
    
    try:
        # Öffne Audio-Datei
        with open(audio_path, 'rb') as f:
            files = {'audio': (audio_path.name, f, 'audio/wav')}
            
            # Sende POST-Request zum Server
            response = requests.post(
                f"{server_url}/transcribe",
                files=files,
                timeout=120  # 2 Minuten Timeout
            )
        
        # Prüfe Response
        if response.status_code == 200:
            result = response.json()
            cleaned_text = result.get('cleaned_text', '')
            
            # Debug-Info (optional)
            # print(f"Raw: {result.get('raw_text', '')}", file=sys.stderr)
            # print(f"Cleaned: {cleaned_text}", file=sys.stderr)
            
            return cleaned_text
        else:
            print(f"ERROR: Server-Fehler {response.status_code}: {response.text}", file=sys.stderr)
            return ""
            
    except requests.exceptions.ConnectionError:
        print(f"ERROR: Verbindung zu Server {server_url} fehlgeschlagen", file=sys.stderr)
        return ""
    except requests.exceptions.Timeout:
        print(f"ERROR: Server-Timeout (>2min)", file=sys.stderr)
        return ""
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return ""


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: diktat_client.py <audio_file> <server_url>", file=sys.stderr)
        sys.exit(1)
    
    audio_file = sys.argv[1]
    server_url = sys.argv[2]
    
    # Sende zum Server
    text = send_audio_to_server(audio_file, server_url)
    
    # Gebe Text aus (wird von AHK gelesen)
    if text:
        print(text, end='')  # Kein Newline am Ende
    else:
        sys.exit(1)
