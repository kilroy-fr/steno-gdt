#!/usr/bin/env python3
"""
Diktat GUI - Medizinisches Diktiersystem (Diktat-Only Fork)
Basierend auf Asklaion, adaptiert für ki-transcribe Server
Nur Diktat-Modus, ohne Ambient-Funktion
"""
import tkinter as tk
from tkinter import messagebox, scrolledtext
import sounddevice as sd
import numpy as np
import threading
import requests
import scipy.io.wavfile as wav
import io
import time
import pyperclip
from datetime import datetime
import os
import json
from pathlib import Path

# Versuche python-certifi-win32 zu importieren (verwendet Windows Certificate Store)
try:
    import certifi_win32
except ImportError:
    pass  # Falls nicht installiert, verwendet requests den Standard-certifi

# --- KONFIGURATION ---
SAMPLE_RATE = 16000
CHUNK_DURATION = 30  # Sekunden pro Chunk

# Config-Pfad (im AppData-Ordner des Users)
CONFIG_DIR = Path(os.getenv('APPDATA')) / "KI-Transcribe-Pro"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Default-Werte
DEFAULT_CONFIG = {
    "server_url": "http://transcribe.domain.local/transcribe",
    "sample_rate": 16000,
    "chunk_duration": 30
}

# --- CONFIG-MANAGEMENT ---
def load_config():
    """Lädt Konfiguration aus JSON-Datei"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Fehler beim Laden der Config: {e}")
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()


def save_config(config):
    """Speichert Konfiguration in JSON-Datei"""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        print(f"Fehler beim Speichern der Config: {e}")
        return False


def show_config_dialog(parent=None):
    """Zeigt Config-Dialog beim ersten Start"""
    dialog = tk.Toplevel(parent) if parent else tk.Tk()
    dialog.title("Erstkonfiguration - KI-Transcribe Pro")
    dialog.geometry("500x300")
    dialog.configure(bg="#f5f7f9")
    dialog.resizable(False, False)

    # Zentriere Fenster
    dialog.update_idletasks()
    x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
    y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
    dialog.geometry(f"+{x}+{y}")

    # Wenn parent existiert, mache modal
    if parent:
        dialog.transient(parent)
        dialog.grab_set()

    # Header
    header = tk.Frame(dialog, bg="#2c3e50", height=60)
    header.pack(fill="x")
    header.pack_propagate(False)

    tk.Label(
        header, text="🎙️ Willkommen bei KI-Transcribe Pro",
        font=("Segoe UI", 14, "bold"), bg="#2c3e50", fg="white"
    ).pack(pady=15)

    # Content
    content = tk.Frame(dialog, bg="#f5f7f9")
    content.pack(fill="both", expand=True, padx=30, pady=20)

    tk.Label(
        content, text="Bitte gib die Server-URL ein:",
        font=("Segoe UI", 10), bg="#f5f7f9", fg="#2c3e50"
    ).pack(anchor="w", pady=(0, 5))

    tk.Label(
        content, text="(Die URL des ki-transcribe Servers)",
        font=("Segoe UI", 8), bg="#f5f7f9", fg="#7f8c8d"
    ).pack(anchor="w", pady=(0, 10))

    # URL-Eingabe
    url_var = tk.StringVar(value=DEFAULT_CONFIG["server_url"])
    url_entry = tk.Entry(
        content, textvariable=url_var, font=("Segoe UI", 11),
        bd=1, relief="solid", width=45
    )
    url_entry.pack(fill="x", pady=5)
    url_entry.focus()

    # Beispiele
    examples = tk.Frame(content, bg="#f5f7f9")
    examples.pack(fill="x", pady=10)

    tk.Label(
        examples, text="Beispiele:",
        font=("Segoe UI", 8, "bold"), bg="#f5f7f9", fg="#7f8c8d"
    ).pack(anchor="w")

    for example in ["http://transcribe.domain.local/transcribe", "http://localhost:8000/transcribe", "http://192.168.1.100:8000/transcribe"]:
        ex_label = tk.Label(
            examples, text=f"• {example}",
            font=("Segoe UI", 8), bg="#f5f7f9", fg="#3498db",
            cursor="hand2"
        )
        ex_label.pack(anchor="w", padx=10)
        ex_label.bind("<Button-1>", lambda e, url=example: url_var.set(url))

    # Result-Variable
    result = {"url": None, "cancelled": True}

    def on_save():
        url = url_var.get().strip()
        if not url:
            messagebox.showerror("Fehler", "Bitte gib eine Server-URL ein!", parent=dialog)
            return

        # Validiere URL-Format
        if not (url.startswith("http://") or url.startswith("https://")):
            messagebox.showerror("Fehler", "URL muss mit http:// oder https:// beginnen!", parent=dialog)
            return

        result["url"] = url
        result["cancelled"] = False
        dialog.destroy()

    def on_cancel():
        result["cancelled"] = True
        dialog.destroy()

    # Buttons
    btn_frame = tk.Frame(content, bg="#f5f7f9")
    btn_frame.pack(side="bottom", fill="x", pady=(20, 0))

    tk.Button(
        btn_frame, text="Speichern", command=on_save,
        bg="#2ecc71", fg="white", font=("Segoe UI", 10, "bold"),
        relief="flat", pady=8, width=15
    ).pack(side="right", padx=5)

    tk.Button(
        btn_frame, text="Abbrechen", command=on_cancel,
        bg="#95a5a6", fg="white", font=("Segoe UI", 10, "bold"),
        relief="flat", pady=8, width=15
    ).pack(side="right")

    # Enter-Taste bindet auf Speichern
    url_entry.bind("<Return>", lambda e: on_save())

    # Warte auf Schließen
    dialog.wait_window()

    return result


# Standard-Befunde wurden entfernt (nur im Ambient-Modus verwendet)


class ModernRecorder:
    def __init__(self, root, config):
        self.root = root
        self.root.title("KI-Transcribe Pro - Diktat")
        self.root.geometry("500x700")
        self.root.configure(bg="#f5f7f9")

        # Config
        self.config = config
        self.server_url = config["server_url"]

        self.is_recording = False
        self.is_paused = False
        self.audio_buffer = []
        self.full_transcription = []

        self._setup_ui()
        self._add_menu()

    def _setup_ui(self):
        # Header mit Status
        header = tk.Frame(self.root, bg="#f5f7f9")
        header.pack(fill="x", padx=20, pady=(10, 0))

        self.status_dot = tk.Label(header, text="●", fg="#bdc3c7", bg="#f5f7f9", font=("Arial", 14))
        self.status_dot.pack(side="left")

        self.status_label = tk.Label(header, text="Bereit", font=("Segoe UI", 9), bg="#f5f7f9", fg="#7f8c8d")
        self.status_label.pack(side="left", padx=5)

        # Diktat-UI direkt im Hauptfenster (kein Notebook mehr)
        self._build_diktat_ui()

    def _build_diktat_ui(self):
        """Diktat-UI: Direktes Diktieren mit Live-Transkription"""
        # Control-Frame
        ctrl = tk.Frame(self.root, bg="white", highlightthickness=1, highlightbackground="#e0e6ed")
        ctrl.pack(fill="x", padx=10, pady=10)
        btn_f = tk.Frame(ctrl, bg="white")
        btn_f.pack(pady=10)

        self.start_btn = tk.Button(
            btn_f, text="Start", bg="#2ecc71", fg="white",
            font=("Segoe UI", 9, "bold"), relief="flat", width=10, pady=6,
            command=self.start_recording
        )
        self.pause_btn = tk.Button(
            btn_f, text="Pause", bg="#f39c12", fg="white",
            font=("Segoe UI", 9, "bold"), relief="flat", width=10, pady=6,
            state="disabled", command=self.toggle_pause
        )
        self.stop_btn = tk.Button(
            btn_f, text="Analyse", bg="#e74c3c", fg="white",
            font=("Segoe UI", 9, "bold"), relief="flat", width=10, pady=6,
            state="disabled", command=self.stop_recording
        )

        for b in [self.start_btn, self.pause_btn, self.stop_btn]:
            b.pack(side="left", padx=3)

        # Live-Transkript
        tk.Label(
            self.root, text="LIVE-TRANSKRIPT",
            font=("Segoe UI", 7, "bold"), bg="#f5f7f9", fg="#95a5a6"
        ).pack(anchor="w", padx=10, pady=(5,0))

        self.live_text = scrolledtext.ScrolledText(
            self.root, height=22, font=("Segoe UI", 11),
            bg="white", bd=1, relief="solid", padx=10, pady=10
        )
        self.live_text.pack(fill="both", expand=True, padx=10, pady=5)

        # Log-Bereich
        self.log_text = scrolledtext.ScrolledText(
            self.root, height=5, bg="#1e272e", fg="#ecf0f1",
            font=("Consolas", 8), borderwidth=0
        )
        self.log_text.pack(fill="x", padx=10, pady=10)

    def _add_menu(self):
        """Fügt Menüleiste hinzu"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # Einstellungen-Menü
        settings_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Einstellungen", menu=settings_menu)
        settings_menu.add_command(label="Server-URL ändern...", command=self.change_server_url)
        settings_menu.add_separator()
        settings_menu.add_command(label="Beenden", command=self.root.quit)

        # Info-Menü
        info_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Info", menu=info_menu)
        info_menu.add_command(label="Über KI-Transcribe Pro", command=self.show_about)

    def change_server_url(self):
        """Öffnet Dialog zum Ändern der Server-URL"""
        result = show_config_dialog(self.root)

        if not result["cancelled"]:
            self.config["server_url"] = result["url"]
            self.server_url = result["url"]

            if save_config(self.config):
                messagebox.showinfo(
                    "Erfolg",
                    f"Server-URL wurde aktualisiert:\n{self.server_url}",
                    parent=self.root
                )
                self.log(f"Server-URL geändert: {self.server_url}")
            else:
                messagebox.showerror(
                    "Fehler",
                    "Konfiguration konnte nicht gespeichert werden!",
                    parent=self.root
                )

    def show_about(self):
        """Zeigt About-Dialog"""
        messagebox.showinfo(
            "Über KI-Transcribe Pro",
            "KI-Transcribe Pro v2.0\n\n"
            "Medizinisches Transkriptions- und Diktiersystem\n"
            "mit automatischer KI-gestützter Textsäuberung.\n\n"
            "Basierend auf Asklaion\n"
            "Powered by faster-whisper & ollama\n\n"
            f"Aktueller Server:\n{self.server_url}",
            parent=self.root
        )

    def log(self, message):
        """Schreibt Log-Nachricht"""
        ts = datetime.now().strftime('%H:%M:%S')
        self.log_text.insert(tk.END, f"[{ts}] {message}\n")
        self.log_text.see(tk.END)

    def toggle_pause(self):
        """Pausiert/Fortsetzt Aufnahme"""
        self.is_paused = not self.is_paused
        status = "PAUSIERT" if self.is_paused else "AKTIV"
        color = "#f39c12" if self.is_paused else "#e74c3c"

        self.status_label.config(text=status, fg=color)
        self.status_dot.config(fg=color)

        btn_text = "Weiter" if self.is_paused else "Pause"
        self.pause_btn.config(text=btn_text)

        self.log(f"Aufnahme {status.lower()}.")

    def audio_callback(self, indata, frames, time, status):
        """Callback für Audio-Stream"""
        if self.is_recording and not self.is_paused:
            self.audio_buffer.append(indata.copy())

    def process_audio_thread(self):
        """Verarbeitet Audio-Chunks kontinuierlich"""
        last_chunk_time = time.time()

        while self.is_recording:
            time.sleep(1)

            if not self.is_paused and time.time() - last_chunk_time >= CHUNK_DURATION:
                self.send_chunk()
                last_chunk_time = time.time()

    def send_chunk(self, is_final=False):
        """Sendet Audio-Chunk zum Server"""
        if not self.audio_buffer and not is_final:
            return

        if self.audio_buffer:
            chunk_data = np.concatenate(self.audio_buffer, axis=0)

            if not is_final:
                self.audio_buffer = []

            # Konvertiere zu WAV
            wav_io = io.BytesIO()
            wav.write(wav_io, SAMPLE_RATE, chunk_data)
            wav_io.seek(0)

            # Sende in Thread
            threading.Thread(
                target=self._upload_task,
                args=(wav_io, is_final),
                daemon=True
            ).start()
        elif is_final:
            self.finalize_transcription()

    def _upload_task(self, wav_file, is_final):
        """Lädt Audio-Chunk zum Server hoch"""
        try:
            files = {'audio': ('chunk.wav', wav_file, 'audio/wav')}
            response = requests.post(self.server_url, files=files, timeout=120)

            if response.status_code == 200:
                result = response.json()
                text = result.get("cleaned_text", "")

                if text:
                    self.full_transcription.append(text + " ")

                    # Live-Anzeige im Diktat-Text
                    self.root.after(0, lambda t=text: self.live_text.insert(tk.END, t + " "))
                    self.root.after(0, lambda: self.live_text.see(tk.END))
            else:
                self.log(f"Server-Fehler: {response.status_code}")

        except requests.exceptions.ConnectionError:
            self.log(f"Verbindung zum Server fehlgeschlagen!")
        except Exception as e:
            self.log(f"Fehler: {e}")

        if is_final:
            self.root.after(0, self.finalize_transcription)

    def start_recording(self):
        """Startet Aufnahme"""
        self.is_recording = True
        self.is_paused = False
        self.audio_buffer = []
        self.full_transcription = []
        self.live_text.delete("1.0", tk.END)

        # UI-Updates
        self.start_btn.config(state="disabled", bg="#bdc3c7")
        self.pause_btn.config(state="normal")
        self.stop_btn.config(state="normal")

        self.status_dot.config(fg="#e74c3c")
        self.status_label.config(text="AKTIV", fg="#e74c3c")

        # Starte Audio-Stream
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            callback=self.audio_callback
        )
        self.stream.start()

        # Starte Chunk-Verarbeitung
        threading.Thread(target=self.process_audio_thread, daemon=True).start()

        self.log("Aufnahme gestartet.")

    def stop_recording(self):
        """Stoppt Aufnahme und startet Analyse"""
        # Stoppe Aufnahme
        self.is_recording = False
        self.stream.stop()
        self.stream.close()

        # UI-Updates
        self.start_btn.config(state="normal", bg="#2ecc71")
        self.pause_btn.config(state="disabled")
        self.stop_btn.config(state="disabled")

        self.status_label.config(text="VERARBEITE...", fg="#3498db")

        # Sende finalen Chunk
        self.send_chunk(is_final=True)

    def finalize_transcription(self):
        """Finalisiert Transkription und zeigt Ergebnis"""
        raw_text = "".join(self.full_transcription)

        # Speichere Roh-Transkript
        output_dir = "Transkripte_Raw"
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        with open(os.path.join(output_dir, f"raw_{timestamp}.txt"), "w", encoding="utf-8") as f:
            f.write(raw_text)

        self.log(f"Transkript gespeichert: raw_{timestamp}.txt")

        # Zeige editierbares Ergebnis
        self.root.after(0, lambda: self.show_editable_result(raw_text))

    def show_editable_result(self, text):
        """Zeigt Ergebnis in editierbarem Fenster"""
        self.status_label.config(text="BEREIT", fg="#7f8c8d")
        self.status_dot.config(fg="#bdc3c7")

        # Kopiere in Zwischenablage
        pyperclip.copy(text)

        # Erstelle Ergebnis-Fenster
        res_win = tk.Toplevel(self.root)
        res_win.title("Ergebnis (Editierbar)")
        res_win.geometry("500x650")
        res_win.configure(bg="#f5f7f9")
        res_win.attributes("-topmost", True)

        tk.Label(
            res_win, text="Transkribierter Text (Korrektur möglich):",
            font=("Segoe UI", 9, "bold"), bg="#f5f7f9", fg="#2c3e50"
        ).pack(pady=10)

        txt_area = scrolledtext.ScrolledText(
            res_win, wrap=tk.WORD, font=("Segoe UI", 11),
            padx=10, pady=10, bg="white", bd=0
        )
        txt_area.pack(fill="both", expand=True, padx=15, pady=5)
        txt_area.insert(tk.END, text)

        def finalize_and_copy():
            final_text = txt_area.get("1.0", tk.END).strip()
            pyperclip.copy(final_text)
            res_win.destroy()

        btn_close = tk.Button(
            res_win, text="In Zwischenablage & Schließen",
            command=finalize_and_copy,
            bg="#34495e", fg="white", font=("Segoe UI", 10, "bold"),
            relief="flat", pady=10
        )
        btn_close.pack(side="bottom", fill="x", padx=15, pady=15)


def main():
    """Hauptfunktion"""
    # Prüfe, ob Config existiert
    config = load_config()
    first_run = not CONFIG_FILE.exists()

    if first_run:
        # Zeige Config-Dialog beim ersten Start
        result = show_config_dialog()

        if result["cancelled"]:
            # User hat abgebrochen
            return

        config["server_url"] = result["url"]
        save_config(config)

    # Erstelle Hauptfenster
    root = tk.Tk()

    # App starten
    app = ModernRecorder(root, config)

    # Zentriere Fenster
    root.update_idletasks()
    x = (root.winfo_screenwidth() // 2) - (root.winfo_width() // 2)
    y = (root.winfo_screenheight() // 2) - (root.winfo_height() // 2)
    root.geometry(f"+{x}+{y}")

    root.mainloop()


if __name__ == "__main__":
    main()
