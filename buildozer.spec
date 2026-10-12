[app]

# Nombre que se ve en el teléfono
title = CalificadorApp

# Identificadores internos (sin espacios ni mayúsculas raras)
package.name = calificador
package.domain = org.liceosahagun

# Carpeta del código (el . = la misma carpeta donde está este archivo)
source.dir = .

# Tipos de archivo que se empaquetan dentro del APK
source.include_exts = py,png,jpg,jpeg,kv,atlas,ttf,json

# Carpetas que NO van en el APK (muestras de prueba y datos locales del PC)
source.exclude_dirs = muestras,profesores,__pycache__
# logo.png es el original (1.7 MB); el PDF usa assets/logo_pdf.png
source.exclude_patterns = logo.png

# Versión de la app
version = 0.1

# Librerías que necesita la app
requirements = python3,kivy,opencv,numpy,pillow,openpyxl,et_xmlfile,plyer,pyjnius

# La app se ve en vertical
orientation = portrait
fullscreen = 0

# Permisos de Android. No hace falta CAMERA (la foto la toma la app de cámara
# del sistema) ni leer el almacenamiento (fotos y planillas se eligen con el
# selector del sistema). WRITE solo lo usa Android 9 o anterior para Descargas.
android.permissions = VIBRATE,WRITE_EXTERNAL_STORAGE

# Arquitectura (cubre los celulares modernos)
android.archs = arm64-v8a

# Nivel de API de Android para compilar
android.api = 33

android.allow_backup = 1

# Acepta solo las licencias del SDK de Android (necesario al compilar en GitHub,
# donde nadie puede escribir "y" para aceptarlas)
android.accept_sdk_license = True

# --- Opcionales: descomenta si quieres usar el logo como ícono/splash ---
# icon.filename = %(source.dir)s/logo.png
# presplash.filename = %(source.dir)s/logo.png


[buildozer]

# Nivel de detalle de los mensajes (2 = bastante detalle)
log_level = 2
warn_on_root = 1
