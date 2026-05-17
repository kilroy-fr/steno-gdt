#Requires AutoHotkey v2.0
#SingleInstance Force

; ============================================
; KONFIGURATION - ANPASSEN!
; ============================================
SERVER_URL := "http://192.168.1.100:8000"  ; Server IP anpassen!
SOX_PATH := "C:\Program Files\sox\sox.exe"  ; SoX Pfad anpassen!

; Automatisch erkannte Pfade
SCRIPT_DIR := A_ScriptDir
PYTHON_EXE := "python"
PYTHON_SCRIPT := SCRIPT_DIR . "\diktat_client.py"
TEMP_AUDIO := SCRIPT_DIR . "\temp_recording.wav"

; ============================================
; GLOBALE VARIABLEN
; ============================================
isRecording := false
soxPID := 0

; ============================================
; HOTKEY: Ctrl+Shift+D - Start/Stop
; ============================================
^+d::
{
    global isRecording, soxPID, TEMP_AUDIO, SOX_PATH
    
    if (!isRecording)
    {
        ; === START AUFNAHME ===
        isRecording := true
        TrayTip("🎤 Aufnahme läuft...", "Diktat", 1)
        
        ; Lösche alte Datei
        if FileExist(TEMP_AUDIO)
            FileDelete(TEMP_AUDIO)
        
        ; Starte SoX Aufnahme
        ; -d = Default Mikrofon
        ; -r 16000 = 16kHz Sample Rate (ausreichend für Sprache)
        ; -c 1 = Mono
        Run('"' . SOX_PATH . '" -d -r 16000 -c 1 "' . TEMP_AUDIO . '"', , "Hide", &soxPID)
    }
    else
    {
        ; === STOP AUFNAHME ===
        isRecording := false
        TrayTip("⏹️ Verarbeite...", "Diktat", 1)
        
        ; Beende SoX
        if (soxPID != 0)
        {
            ProcessClose(soxPID)
            soxPID := 0
        }
        
        Sleep(300)  ; Kurz warten
        
        ; Prüfe Datei
        if (!FileExist(TEMP_AUDIO))
        {
            TrayTip("❌ Keine Aufnahme!", "Diktat", 1)
            Sleep(2000)
            TrayTip()
            return
        }
        
        ; Sende zum Server
        SendToServer()
    }
}

; ============================================
; HOTKEY: Ctrl+Shift+S - Abbrechen
; ============================================
^+s::
{
    global isRecording, soxPID
    
    if (isRecording)
    {
        isRecording := false
        if (soxPID != 0)
        {
            ProcessClose(soxPID)
            soxPID := 0
        }
        TrayTip("🛑 Abgebrochen", "Diktat", 1)
        Sleep(1500)
        TrayTip()
    }
}

; ============================================
; Sende zum Server und füge Text ein
; ============================================
SendToServer()
{
    global PYTHON_EXE, PYTHON_SCRIPT, TEMP_AUDIO, SERVER_URL
    
    ; Baue Kommando
    cmd := Format('"{}" "{}" "{}" "{}"', PYTHON_EXE, PYTHON_SCRIPT, TEMP_AUDIO, SERVER_URL)
    
    ; Führe aus
    shell := ComObject("WScript.Shell")
    exec := shell.Exec(A_ComSpec . " /C " . cmd)
    
    ; Warte auf Ergebnis
    output := exec.StdOut.ReadAll()
    TrayTip()
    
    if (output != "")
    {
        ; Text in Zwischenablage
        A_Clipboard := output
        Sleep(50)
        
        ; Füge ein
        Send("^v")
        
        ; Bestätigung
        SoundBeep(800, 100)
        TrayTip("✅ Eingefügt", "Diktat", 1)
        Sleep(1000)
        TrayTip()
    }
    else
    {
        TrayTip("❌ Fehler!", "Diktat", 1)
        Sleep(2000)
        TrayTip()
    }
}

; ============================================
; Startup
; ============================================
TrayTip("✅ Bereit!", "Ctrl+Shift+D: Aufnahme`nCtrl+Shift+S: Abbrechen", 2)
Sleep(2000)
TrayTip()
