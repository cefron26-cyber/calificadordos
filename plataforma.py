"""
plataforma.py — Todo lo que cambia entre el PC y el celular Android.

  • Elegir archivos (fotos, planillas, copias): selector del sistema en
    Android (galería / Archivos, con selección múltiple); explorador en PC.
  • Copiar lo elegido: en Android llega como "content://…" y se lee con el
    ContentResolver; en PC es una ruta normal.
  • Guardar en Descargas, abrir y compartir archivos (Excel, PDF, copias).
  • Copia de seguridad: comprimir y restaurar la carpeta de datos.

Las partes de Android usan pyjnius y solo se importan cuando la app corre
en el celular; en PC nunca se tocan.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from kivy.clock import Clock
from kivy.utils import platform

ES_ANDROID = platform == "android"

MIME = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".zip": "application/zip",
    ".jpg": "image/jpeg",
    ".png": "image/png",
}
_COD_ELEGIR = 0x5E1E  # código para reconocer la respuesta del selector de Android


def mime_de(nombre: str) -> str:
    return MIME.get(Path(nombre).suffix.lower(), "application/octet-stream")


# ─────────────────────────────────────────────────────────────────────────────
# Android: utilidades de pyjnius
# ─────────────────────────────────────────────────────────────────────────────

def _actividad():
    from jnius import autoclass  # type: ignore
    return autoclass("org.kivy.android.PythonActivity").mActivity


def _permitir_rutas_file():
    """Permite pasar 'file://' a otras apps (solo se usa en Android 9 o
    anterior, cuando no hay MediaStore). Sin esto Android cierra la app."""
    try:
        from jnius import autoclass  # type: ignore
        autoclass("android.os.StrictMode").disableDeathOnFileUriExposure()
    except Exception:
        pass


def _uri(ref: str):
    from jnius import autoclass  # type: ignore
    Uri = autoclass("android.net.Uri")
    if ref.startswith("content://"):
        return Uri.parse(ref)
    _permitir_rutas_file()
    return Uri.fromFile(autoclass("java.io.File")(ref))


def _nombre_android(uri) -> str:
    """Nombre visible del archivo elegido (p. ej. 'IMG_2031.jpg')."""
    from jnius import autoclass  # type: ignore
    OpenableColumns = autoclass("android.provider.OpenableColumns")
    resolver = _actividad().getContentResolver()
    nombre = ""
    cursor = resolver.query(uri, None, None, None, None)
    if cursor is not None:
        try:
            if cursor.moveToFirst():
                i = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if i >= 0:
                    nombre = cursor.getString(i) or ""
        finally:
            cursor.close()
    if not Path(nombre).suffix:
        # Sin extensión: se deduce del tipo (importa para planillas y copias)
        tipo = resolver.getType(uri) or ""
        ext = next((e for e, m in MIME.items() if m == tipo), "")
        if not ext and "csv" in tipo:
            ext = ".csv"
        nombre = (nombre or "archivo") + ext
    return nombre


# ─────────────────────────────────────────────────────────────────────────────
# Elegir archivos
# ─────────────────────────────────────────────────────────────────────────────

def elegir_archivos(al_elegir: Callable[[list[str]], None], tipo: str = "imagenes",
                    multiple: bool = True, titulo: str = "Elige los archivos") -> None:
    """
    Abre el selector y llama a al_elegir(lista) en el hilo de la interfaz.
    La lista trae referencias para copiar(): rutas en PC, "content://…" en
    Android. Si el usuario cancela, no se llama.

    tipo: "imagenes", "planillas" o "zip".
    """
    if ES_ANDROID:
        _elegir_android(al_elegir, tipo, multiple)
        return

    filtros = {
        "imagenes": [("Imágenes", "*.png *.jpg *.jpeg *.bmp *.webp"), ("Todos", "*.*")],
        "planillas": [("Excel/CSV", "*.xlsx *.xlsm *.csv"), ("Todos", "*.*")],
        "zip": [("Copia de seguridad", "*.zip")],
    }[tipo]
    import tkinter as tk
    from tkinter import filedialog
    raiz = tk.Tk()
    raiz.withdraw()
    raiz.attributes("-topmost", True)
    try:
        if multiple:
            rutas = list(filedialog.askopenfilenames(title=titulo, filetypes=filtros))
        else:
            r = filedialog.askopenfilename(title=titulo, filetypes=filtros)
            rutas = [r] if r else []
    finally:
        raiz.destroy()
    if rutas:
        al_elegir(rutas)


def _elegir_android(al_elegir, tipo, multiple):
    from jnius import autoclass, cast  # type: ignore
    from android import activity  # type: ignore
    Intent = autoclass("android.content.Intent")
    String = autoclass("java.lang.String")

    intent = Intent(Intent.ACTION_GET_CONTENT)
    intent.addCategory(Intent.CATEGORY_OPENABLE)
    # Planillas y copias: se muestran todos los archivos y la extensión se
    # valida después (los celulares no se ponen de acuerdo en el tipo de un
    # .csv o un .zip, y filtrar de más esconde archivos válidos).
    intent.setType("image/*" if tipo == "imagenes" else "*/*")
    intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, bool(multiple))

    def al_resultado(codigo, resultado, datos):
        if codigo != _COD_ELEGIR:
            return
        activity.unbind(on_activity_result=al_resultado)
        if resultado != -1 or datos is None:      # -1 = RESULT_OK
            return
        refs = []
        clip = datos.getClipData()
        if clip is not None:
            for i in range(clip.getItemCount()):
                refs.append(clip.getItemAt(i).getUri().toString())
        elif datos.getData() is not None:
            refs.append(datos.getData().toString())
        if refs:
            # Esto llega en el hilo de Android: la interfaz se toca desde Kivy
            Clock.schedule_once(lambda dt: al_elegir(refs), 0)

    activity.bind(on_activity_result=al_resultado)
    _actividad().startActivityForResult(
        cast("android.content.Intent", Intent.createChooser(intent, String("Elegir"))),
        _COD_ELEGIR)


def nombre_de(ref: str) -> str:
    """Nombre de archivo de una referencia (ruta o content://)."""
    if ES_ANDROID and ref.startswith("content://"):
        try:
            return _nombre_android(_uri(ref))
        except Exception:
            return "archivo"
    return Path(ref).name


def copiar(ref: str, destino: Path, mover: bool = False) -> None:
    """Copia (o mueve) lo elegido a 'destino'. Acepta rutas o content://."""
    destino = Path(destino)
    if ES_ANDROID and ref.startswith("content://"):
        try:
            from jnius import autoclass  # type: ignore
            resolver = _actividad().getContentResolver()
            entrada = resolver.openInputStream(_uri(ref))
            try:
                try:
                    # Java copia directo al archivo (rápido, sin pasar bytes por Python)
                    Files = autoclass("java.nio.file.Files")
                    Opcion = autoclass("java.nio.file.StandardCopyOption")
                    destino_java = autoclass("java.io.File")(str(destino)).toPath()
                    Files.copy(entrada, destino_java, [Opcion.REPLACE_EXISTING])
                except Exception:
                    # Respaldo (Android 7 o anterior): copiar por bloques
                    with open(destino, "wb") as salida:
                        buf = bytearray(256 * 1024)
                        while True:
                            n = entrada.read(buf)
                            if n is None or n <= 0:
                                break
                            salida.write(bytes(buf[:n]))
            finally:
                entrada.close()
        finally:
            # Los hilos de trabajo deben soltarse de Java al terminar
            if threading.current_thread() is not threading.main_thread():
                try:
                    from jnius import detach  # type: ignore
                    detach()
                except Exception:
                    pass
        return
    (shutil.move if mover else shutil.copy)(str(ref), str(destino))


def copiar_a_temporal(ref: str) -> Path:
    """Copia lo elegido a la carpeta temporal conservando su nombre."""
    carpeta = Path(tempfile.mkdtemp(prefix="calificador_"))
    destino = carpeta / nombre_de(ref)
    copiar(ref, destino)
    return destino


# ─────────────────────────────────────────────────────────────────────────────
# Descargas, abrir y compartir
# ─────────────────────────────────────────────────────────────────────────────

def _carpeta_descargas_pc() -> Path:
    base = None
    if os.name == "nt":
        # Pregunta a Windows cuál es la carpeta "Descargas" real (respeta OneDrive)
        try:
            import winreg
            sub = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
            guid = "{374DE290-123F-4565-9164-39C4925E467B}"  # carpeta conocida: Descargas
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub) as k:
                val, _ = winreg.QueryValueEx(k, guid)
            base = Path(os.path.expandvars(val))
        except Exception:
            base = None
    if base is None or not base.exists():
        base = Path.home() / "Downloads"
        if not base.exists() and (Path.home() / "Descargas").exists():
            base = Path.home() / "Descargas"
    base.mkdir(parents=True, exist_ok=True)
    return base


def guardar_en_descargas(origen: Path, nombre: str) -> tuple[str, str]:
    """
    Copia 'origen' a Descargas. Devuelve (texto_para_mostrar, referencia),
    donde la referencia sirve para abrir() y compartir().
      • Android 10+: MediaStore (sin permisos especiales).
      • Android 9 o menos: /storage/emulated/0/Download.
      • PC: la carpeta Descargas del usuario.
    """
    origen = Path(origen)
    if ES_ANDROID:
        try:
            from jnius import autoclass  # type: ignore
            ContentValues = autoclass("android.content.ContentValues")
            MediaColumns = autoclass("android.provider.MediaStore$MediaColumns")
            Downloads = autoclass("android.provider.MediaStore$Downloads")
            resolver = _actividad().getContentResolver()
            valores = ContentValues()
            valores.put(MediaColumns.DISPLAY_NAME, nombre)
            valores.put(MediaColumns.MIME_TYPE, mime_de(nombre))
            valores.put(MediaColumns.RELATIVE_PATH, "Download")
            uri = resolver.insert(Downloads.EXTERNAL_CONTENT_URI, valores)
            salida = resolver.openOutputStream(uri)
            try:
                salida.write(origen.read_bytes())
                salida.flush()
            finally:
                salida.close()
            return "Descargas/" + nombre, uri.toString()
        except Exception:
            destino = Path("/storage/emulated/0/Download") / nombre
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(str(origen), str(destino))
            return str(destino), str(destino)

    destino = _carpeta_descargas_pc() / nombre
    shutil.copy(str(origen), str(destino))
    return str(destino), str(destino)


def abrir(ref: str, nombre: str = "") -> Optional[str]:
    """Abre el archivo con la app que corresponda. Devuelve un error o None."""
    try:
        if ES_ANDROID:
            from jnius import autoclass, cast  # type: ignore
            Intent = autoclass("android.content.Intent")
            String = autoclass("java.lang.String")
            intent = Intent(Intent.ACTION_VIEW)
            intent.setDataAndType(_uri(ref), mime_de(nombre or ref))
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            _actividad().startActivity(
                cast("android.content.Intent", Intent.createChooser(intent, String("Abrir con"))))
        elif os.name == "nt":
            os.startfile(ref)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", ref])
        return None
    except Exception as e:
        return str(e)


def compartir(ref: str, nombre: str = "") -> Optional[str]:
    """Android: menú Compartir (WhatsApp, Drive, correo…).
    PC: abre la carpeta con el archivo seleccionado. Devuelve un error o None."""
    try:
        if ES_ANDROID:
            from jnius import autoclass, cast  # type: ignore
            Intent = autoclass("android.content.Intent")
            String = autoclass("java.lang.String")
            intent = Intent(Intent.ACTION_SEND)
            intent.setType(mime_de(nombre or ref))
            intent.putExtra(Intent.EXTRA_STREAM, cast("android.os.Parcelable", _uri(ref)))
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            _actividad().startActivity(
                cast("android.content.Intent", Intent.createChooser(intent, String("Compartir"))))
        elif os.name == "nt":
            subprocess.Popen(["explorer", "/select,", str(Path(ref))])
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open",
                              str(Path(ref).parent)])
        return None
    except Exception as e:
        return str(e)


# ─────────────────────────────────────────────────────────────────────────────
# Copia de seguridad
# ─────────────────────────────────────────────────────────────────────────────

def crear_respaldo(carpeta_datos: Path) -> Path:
    """Comprime la carpeta de datos (profesores/) en un .zip temporal."""
    carpeta_datos = Path(carpeta_datos)
    marca = datetime.now().strftime("%Y-%m-%d_%H%M")
    zip_path = Path(tempfile.gettempdir()) / f"respaldo_calificador_{marca}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in carpeta_datos.rglob("*"):
            if f.is_file():
                z.write(f, Path(carpeta_datos.name) / f.relative_to(carpeta_datos))
    return zip_path


def restaurar_respaldo(zip_path: Path, carpeta_datos: Path) -> Path:
    """
    Reemplaza la carpeta de datos por la del .zip. Lo que había antes NO se
    borra: se renombra a '<carpeta>_antes_<fecha>' (junto a la original).
    Devuelve esa carpeta. Lanza ValueError si el .zip no es una copia válida.
    """
    zip_path, carpeta_datos = Path(zip_path), Path(carpeta_datos)
    raiz = carpeta_datos.name + "/"
    with zipfile.ZipFile(zip_path) as z:
        nombres = z.namelist()
        if not nombres or not all(n.replace("\\", "/").startswith(raiz) for n in nombres):
            raise ValueError("Ese archivo no es una copia de seguridad de esta app.")
        for n in nombres:   # nada puede escribirse fuera de la carpeta destino
            partes = Path(n.replace("\\", "/")).parts
            if ".." in partes or Path(n).is_absolute():
                raise ValueError("La copia de seguridad está dañada.")
        temporal = Path(tempfile.mkdtemp(prefix="restaurar_", dir=carpeta_datos.parent))
        z.extractall(temporal)

    anterior = carpeta_datos.with_name(
        f"{carpeta_datos.name}_antes_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}")
    if carpeta_datos.exists():
        carpeta_datos.rename(anterior)
    (temporal / carpeta_datos.name).rename(carpeta_datos)
    shutil.rmtree(temporal, ignore_errors=True)
    return anterior
