"""
gestor_salones.py — Carpetas de la app: profesores/<Profesor>/<Grado>/<Curso>/
"""

from pathlib import Path

# Carpeta raíz que almacena toda la información jerárquica
BASE_DIR = Path("profesores")


def inicializar_sistema():
    """Garantiza la existencia de la carpeta raíz de profesores."""
    BASE_DIR.mkdir(parents=True, exist_ok=True)


def crear_profesor(nombre_completo: str) -> Path:
    """Crea la carpeta para un nuevo profesor."""
    nombre_carpeta = nombre_completo.strip().replace(" ", "_")
    ruta_profesor = BASE_DIR / nombre_carpeta
    ruta_profesor.mkdir(parents=True, exist_ok=True)
    return ruta_profesor


def crear_grado(ruta_profesor: Path, grado: str) -> Path:
    """Crea la carpeta de un grado (ej. '11') dentro de un profesor."""
    ruta_grado = ruta_profesor / grado.strip()
    ruta_grado.mkdir(parents=True, exist_ok=True)
    return ruta_grado


def crear_curso(ruta_grado: Path, curso: str) -> Path:
    """Crea la carpeta de un curso (ej. 'A') dentro de un grado, con sus subcarpetas."""
    ruta_curso = ruta_grado / curso.strip().upper()
    ruta_curso.mkdir(parents=True, exist_ok=True)

    (ruta_curso / "estudiantes").mkdir(exist_ok=True)       # lista_estudiantes.xlsx
    (ruta_curso / "examenes_claves").mkdir(exist_ok=True)   # claves JSON
    (ruta_curso / "examenes_crudos").mkdir(exist_ok=True)   # fotos recién cargadas
    (ruta_curso / "resultados").mkdir(exist_ok=True)        # imágenes marcadas + notas
    (ruta_curso / "resultados_excel").mkdir(exist_ok=True)  # registro de notas

    return ruta_curso
