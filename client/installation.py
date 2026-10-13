"""Installieren, aktualisieren und deinstallieren — je Benutzer, ohne Adminrechte.

- Installieren: Steno.exe von irgendwo starten (Download aus dem Browser,
  Netzlaufwerk). Sie fragt, kopiert sich nach %LOCALAPPDATA%\\Programs\\Steno,
  legt Autostart (HKCU\\…\\Run), Startmenü-Eintrag und den Eintrag unter
  Einstellungen → Apps an und startet die installierte Kopie.
- Aktualisieren: beim Start fragt der installierte Knopf den Server (/steno,
  Feld "knopf"). Ist dort eine neuere Version, laedt er sie nach Rueckfrage
  nach %LOCALAPPDATA%\\Steno\\update, prueft die SHA-256 und startet sie mit
  --aktualisieren; dann beendet er sich. Die neue wartet, bis der alte Knopf
  weg ist, kopiert sich an seine Stelle und startet die installierte Kopie.
  Bewusst nicht: die laufende exe umbenennen und ersetzen. Das laesst Windows
  zu, aber PyInstaller liest Module aus der exe nach — der alte Prozess stirbt
  dann beim naechsten import ("appears to have been moved or deleted").
- Deinstallieren: Einstellungen → Apps → Steno-Knopf (Steno.exe --deinstallieren).
  Entfernt Programm und Eintraege; Einstellungen und Protokoll nur auf Wunsch,
  noch nicht uebergebene Aufnahmen nie.
"""
import ctypes
import hashlib
import logging
import os
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

log = logging.getLogger("steno")

INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Programs" / "Steno"
INSTALLED_EXE = INSTALL_DIR / "Steno.exe"
PROGRAMS = Path(os.environ.get("APPDATA") or Path.home()) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
SHORTCUT = PROGRAMS / "Steno.lnk"
STARTUP = PROGRAMS / "Startup"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Steno"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Steno"
LINK_KEY = r"Software\Classes\steno"
MUTEX_NAME = "Local\\Steno-Knopf"
NO_WINDOW = 0x08000000                    # CREATE_NO_WINDOW: kein Konsolenfenster fuer PowerShell
ERROR_ALREADY_EXISTS = 183
WAIT_FOR_OLD = 60                         # s, die eine neue Version auf das Ende der alten wartet


# ============================================================
# NUR EIN KNOPF
# ============================================================
def acquire_mutex(wait: float = 0):
    """Autostart und Doppelklick sollen nicht zwei Knoepfe ergeben. Liefert das
    Handle, oder None, wenn schon einer laeuft. wait: so lange auf dessen Ende
    warten — nach einem Update oder einer Installation beendet er sich gerade."""
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    end = time.monotonic() + wait
    while True:
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if handle and ctypes.get_last_error() != ERROR_ALREADY_EXISTS:
            return handle
        if handle:
            kernel32.CloseHandle(handle)
        if time.monotonic() >= end:
            return None
        time.sleep(0.25)


def release_mutex(handle):
    if sys.platform == "win32" and handle and handle is not True:
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle(handle)


# ============================================================
# WO LAEUFT DIESE EXE
# ============================================================
def running_exe() -> Path | None:
    """Die exe dieses Prozesses; None beim Start als Python-Skript."""
    return Path(sys.executable).resolve() if getattr(sys, "frozen", False) else None


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def is_installed() -> bool:
    exe = running_exe()
    return exe is not None and _same(exe, INSTALLED_EXE)


def parse_version(text) -> tuple[int, ...] | None:
    try:
        parts = tuple(int(p) for p in str(text).strip().split("."))
    except ValueError:
        return None
    return parts if len(parts) >= 2 else None


def newer(available, current) -> bool:
    a, c = parse_version(available), parse_version(current)
    return bool(a and c and a > c)


def launch(exe: Path, *args: str):
    """Eine andere Steno.exe starten. PYINSTALLER_RESET_ENVIRONMENT: sonst
    hielte sie sich fuer einen Teil dieses Prozesses und benutzte dessen
    entpackten Ordner, den der beim Beenden loescht."""
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    subprocess.Popen([str(exe), *args], env=env, cwd=str(exe.parent), close_fds=True)


def place_exe(source: Path, target: Path, wait: float = 30):
    """source nach target kopieren. Solange die alte target noch laeuft (oder
    ihr PyInstaller-Elternprozess aufraeumt), sperrt Windows sie: erneut versuchen."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".neu")
    with open(source, "rb") as src, open(tmp, "wb") as dst:
        while chunk := src.read(1 << 20):
            dst.write(chunk)
    end = time.monotonic() + wait
    while True:
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            if time.monotonic() >= end:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.5)


def cleanup(data_dir: Path):
    """Reste eines Updates (der heruntergeladene Installer laeuft womoeglich
    noch aus — dann eben beim naechsten Start)."""
    for leftover in [*(data_dir / "update").glob("*"), INSTALL_DIR / "Steno.exe.neu"]:
        try:
            leftover.unlink()
        except OSError:
            pass


# ============================================================
# EINTRAEGE IN WINDOWS
# ============================================================
def register(exe: Path, version: str, autostart: bool):
    """Eintrag unter Einstellungen → Apps (HKCU genuegt, ohne Adminrechte),
    Startmenue und — nur bei der Erstinstallation — Autostart. Ein Update
    laesst den Autostart, wie er ist: wer ihn abgeschaltet hat, will das so."""
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        for name, value in {
            "DisplayName": "Steno-Knopf", "DisplayVersion": version, "Publisher": "Steno",
            "DisplayIcon": str(exe), "InstallLocation": str(exe.parent),
            "UninstallString": f'"{exe}" --deinstallieren',
            "InstallDate": date.today().strftime("%Y%m%d"),
        }.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name in ("NoModify", "NoRepair"):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, exe.stat().st_size // 1024)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        try:
            winreg.QueryValueEx(key, RUN_VALUE)
            present = True
        except FileNotFoundError:
            present = False
        if autostart or present:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, f'"{exe}"')
    try:
        make_shortcut(SHORTCUT, exe)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning(f"Startmenü-Eintrag nicht angelegt: {e}")


def _ps_quote(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def make_shortcut(link: Path, exe: Path):
    """.lnk ueber WScript.Shell: Python kann das ohne pywin32 nicht selbst."""
    link.parent.mkdir(parents=True, exist_ok=True)
    script = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(link)}); "
              f"$s.TargetPath = {_ps_quote(exe)}; $s.WorkingDirectory = {_ps_quote(exe.parent)}; "
              "$s.Description = 'Steno-Knopf'; $s.Save()")
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                   creationflags=NO_WINDOW, capture_output=True, timeout=30, check=True)


def old_autostart() -> list[Path]:
    """Verknuepfungen im Autostart-Ordner aus der Zeit vor der Installation
    (README bis 0.40: "Verknuepfung in shell:startup") — sonst startet der
    Knopf zweimal, und die alte exe fragt bei jeder Anmeldung nach Installation."""
    return sorted(STARTUP.glob("*Steno*.lnk"))


def _delete_tree(path: str):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    sub = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_tree(f"{path}\\{sub}")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except FileNotFoundError:
        pass


def unregister():
    import winreg
    _delete_tree(UNINSTALL_KEY)
    _delete_tree(LINK_KEY)                    # steno:// (register_link_scheme in steno.py)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_VALUE)
    except FileNotFoundError:
        pass
    for link in [SHORTCUT, *old_autostart()]:
        link.unlink(missing_ok=True)


def remove_later(paths: list[Path]):
    """Ordner loeschen, sobald dieser Prozess weg ist: die laufende exe (und
    das Protokoll, das sie offen haelt) laesst sich vorher nicht loeschen."""
    if not paths:
        return
    script = (f"$paths = @({', '.join(_ps_quote(p) for p in paths)}); "
              "for ($i = 0; $i -lt 60; $i++) { Start-Sleep -Milliseconds 500; "
              "foreach ($p in $paths) { Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue }; "
              "if (-not ($paths | Where-Object { Test-Path -LiteralPath $_ })) { break } }")
    subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                     creationflags=NO_WINDOW, cwd=tempfile.gettempdir(), close_fds=True)


# ============================================================
# HERUNTERLADEN
# ============================================================
def download(session, url: str, release: dict, target: Path, progress=None) -> Path:
    """Neue Version holen und gegen die SHA-256 aus /steno pruefen. Ueber
    requests, nicht den Browser: so gibt es kein "aus dem Internet"-Merkmal
    und keine SmartScreen-Warnung."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    digest, done = hashlib.sha256(), 0
    with session.get(url, stream=True, timeout=(5, 60)) as r:
        r.raise_for_status()
        with open(part, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, int(release.get("size") or 0))
    if digest.hexdigest() != str(release.get("sha256", "")).lower():
        part.unlink(missing_ok=True)
        raise ValueError("Die Prüfsumme der neuen Version stimmt nicht "
                         "(wird sie gerade neu bereitgelegt?). Beim nächsten Start erneut.")
    os.replace(part, target)
    return target


# ============================================================
# ABLAEUFE MIT RUECKFRAGE
# ============================================================
def _message(kind: str, text: str):
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    try:
        getattr(messagebox, kind)("Steno", text, parent=root)
    finally:
        root.destroy()


def install(argv: list[str], version: str, update: bool) -> bool:
    """Diese exe liegt nicht im Installationsordner. True = erledigt, dieser
    Prozess endet; False = "nur dieses Mal von hier starten"."""
    if not update:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        try:
            answer = messagebox.askyesnocancel(
                "Steno installieren",
                f"Steno-Knopf {version} für diesen Benutzer installieren?\n\n"
                f"Ordner: {INSTALL_DIR}\n"
                "Startet bei jeder Anmeldung, Eintrag im Startmenü. Entfernen über "
                "Einstellungen → Apps → Steno-Knopf.\n\n"
                "Nein = nur dieses Mal von hier starten.", parent=root)
        finally:
            root.destroy()
        if answer is None:
            return True
        if not answer:
            return False
    mutex = acquire_mutex(WAIT_FOR_OLD if update else 0)
    if mutex is None:
        _message("showinfo", "Steno läuft schon. Bitte den Knopf erst beenden "
                             "(Rechtsklick → Beenden), dann diese Datei erneut starten.")
        return True
    try:
        place_exe(running_exe(), INSTALLED_EXE)
        register(INSTALLED_EXE, version, autostart=not update)
        if not update:
            for link in old_autostart():
                link.unlink(missing_ok=True)
                log.info(f"Alte Autostart-Verknüpfung entfernt: {link.name}")
        log.info(f"Steno {version} {'aktualisiert' if update else 'installiert'}: {INSTALLED_EXE}")
    except Exception as e:
        log.exception("Installation fehlgeschlagen")
        release_mutex(mutex)
        if update and INSTALLED_EXE.exists():
            _message("showerror", f"Aktualisierung fehlgeschlagen:\n{e}\n\nSteno startet in der bisherigen Version.")
            launch(INSTALLED_EXE, "--warten", *argv)
        else:
            _message("showerror", f"Installation fehlgeschlagen:\n{e}")
        return True
    release_mutex(mutex)
    launch(INSTALLED_EXE, "--warten", *argv)
    return True


def offer_update(root, session, base: str, release: dict | None, version: str, data_dir: Path) -> bool:
    """Beim Start des installierten Knopfs: neuere Version auf dem Server?
    True = sie laeuft schon an, dieser Knopf soll sich beenden."""
    if not release or not is_installed() or not newer(release.get("version"), version):
        return False
    import tkinter as tk
    from tkinter import messagebox
    import requests
    new = release["version"]
    if not messagebox.askyesno(
            "Steno aktualisieren",
            f"Neue Version {new} verfügbar (installiert: {version}).\n\n"
            "Jetzt aktualisieren? Steno startet danach von selbst wieder. "
            "Einstellungen und Aufnahmen bleiben erhalten.", parent=root):
        log.info(f"Update auf {new} abgelehnt")
        return False
    window = tk.Toplevel(root)
    window.title("Steno")
    window.attributes("-topmost", True)
    window.resizable(False, False)
    label = tk.Label(window, text=f"Steno {new} wird geladen …", padx=24, pady=16)
    label.pack()
    window.update()

    def progress(done: int, total: int):
        if total:
            label.config(text=f"Steno {new} wird geladen … {100 * done // total} %")
        window.update()

    try:
        exe = download(session, f"{base}/steno/knopf/Steno.exe", release,
                       data_dir / "update" / "Steno.exe", progress)
    except (requests.RequestException, OSError, ValueError) as e:
        log.error(f"Update auf {new} nicht geladen: {e}")
        window.destroy()
        messagebox.showerror("Steno", f"Die neue Version ließ sich nicht laden:\n{e}\n\n"
                                      "Steno startet in der bisherigen Version.", parent=root)
        return False
    label.config(text=f"Steno {new} wird installiert …")
    window.update()
    log.info(f"Update auf {new} geladen, wird installiert")
    launch(exe, "--aktualisieren")
    return True


def uninstall(app_dir: Path, data_dir: Path, spool_dir: Path):
    """Steno.exe --deinstallieren (aus Einstellungen → Apps)."""
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    try:
        mutex = acquire_mutex(0)
        if mutex is None:
            messagebox.showinfo("Steno", "Steno läuft noch. Bitte den Knopf erst beenden "
                                         "(Rechtsklick → Beenden), dann erneut deinstallieren.", parent=root)
            return
        if not messagebox.askyesno("Steno deinstallieren",
                                   "Steno-Knopf für diesen Benutzer entfernen?", parent=root):
            return
        unregister()
        remove = [INSTALL_DIR] if INSTALL_DIR.exists() else []
        pending = sorted(spool_dir.glob("*.json")) if spool_dir.exists() else []
        if messagebox.askyesno(
                "Steno deinstallieren",
                "Auch Einstellungen (Server, Arbeitsplatz, Mikrofon) und Protokoll löschen?\n\n"
                "Nein = beim erneuten Installieren ist alles wieder da.",
                default=messagebox.NO, parent=root):
            remove.append(app_dir)
            if pending:                       # die Aufnahmen bleiben, alles andere geht
                remove.extend(p for p in data_dir.iterdir() if not _same(p, spool_dir))
            elif data_dir.exists():
                remove.append(data_dir)
        if pending:
            messagebox.showwarning(
                "Steno", f"{len(pending)} Aufnahme(n) sind noch nicht an T2med übergeben. "
                         f"Sie bleiben in\n{spool_dir}\nliegen und werden nach einer erneuten "
                         "Installation nachgeholt.", parent=root)
        log.info(f"Deinstalliert, {len(pending)} Aufnahme(n) bleiben liegen")
        remove_later(remove)
        messagebox.showinfo("Steno", "Steno-Knopf wurde entfernt.", parent=root)
    finally:
        root.destroy()
