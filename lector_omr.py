"""
lector_omr.py — Motor de lectura de las hojas OMR (las que dibuja generador_pdf.py).

Estrategia:
  1. Detecta las 4 marcas de esquina (cuadrados negros) en la foto.
  2. Decide la orientación con la marca de orientación (la foto puede estar
     girada o de cabeza) y endereza la hoja con una homografía.
  3. Proyecta sobre la imagen enderezada las posiciones de layout_hoja.py.
  4. Binariza UNA sola vez (Otsu sobre el papel).
  5. Mide el relleno de cada burbuja y decide por contraste relativo.
  6. Lee el código del estudiante y compara con la clave.

La app (main.py) usa calificar_foto(): una foto → registro con la nota.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

import digitalizador as D
import layout_hoja as LH

# ─────────────────────────────────────────────────────────────────────────────
# Geometría de la hoja
# ─────────────────────────────────────────────────────────────────────────────
# Las posiciones de marcas y burbujas NO se definen aquí: salen de
# layout_hoja.py, el mismo archivo que usa generador_pdf.py para dibujar.
# Cada función recibe el formato de la hoja ("carta" o "media_carta");
# si no se indica, se asume "carta" (el formato original).

CM: float = LH.CM

# Escala de renderizado: píxeles por punto PDF (mayor = más preciso, más memoria)
RENDER_SCALE: float = 2.0

OPCIONES: tuple[str, ...] = LH.OPCIONES
LEER_CODIGO_ESTUDIANTE: bool = True
DIGITOS_CODIGO: int = LH.DIGITOS_CODIGO

# Dimensión máxima al cargar una foto (reduce fotos de celular sin perder precisión)
MAX_DIM: int = 2400

# Escala de calificación (lineal 0–10)
NOTA_MIN: float = 0.0
NOTA_MAX: float = 10.0
NOTA_APROBACION: float = 6.0
NOTA_PISO: float = 1.0   # ninguna nota baja de este valor (piso institucional)

# ─────────────────────────────────────────────────────────────────────────────
# Tipos de datos
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Burbuja:
    pregunta: int  # O el índice de columna en el caso del código
    opcion:   str
    x_pt:     float   # coordenada X en puntos PDF (coords imagen, origen arriba)
    y_pt:     float   # coordenada Y en puntos PDF (coords imagen, origen arriba)


@dataclass
class ResultadoLectura:
    """Resultado completo de procesar una hoja OMR."""
    respuestas:  dict[int, Optional[str]]        # pregunta → letra o None
    fills:       dict[int, dict[str, float]]      # pregunta → {opción → fracción relleno}
    codigo_estudiante: str = "???"                     # Identificador leído
    en_blanco:   list[int] = field(default_factory=list)
    doble_marca: list[int] = field(default_factory=list)
    dudosas:     list[int] = field(default_factory=list)  # marca débil, revisar manualmente


@dataclass
class DatosExamen:
    nombre:          str
    total_preguntas: int
    clave:           dict[int, str]   # pregunta (1-based) → letra correcta
    formato:         str = "carta"    # formato de la hoja impresa (layout_hoja)


# ─────────────────────────────────────────────────────────────────────────────
# Geometría de la hoja (sale de layout_hoja.py)
# ─────────────────────────────────────────────────────────────────────────────

def esquinas_pdf(formato=None) -> np.ndarray:
    """
    Devuelve las 4 esquinas (centros de las marcas de referencia) en
    coordenadas de imagen PDF, orden: [arriba-izq, arriba-der, abajo-der, abajo-izq].
    """
    return np.float32(LH.obtener(formato).esquinas())


def calcular_burbujas(num_preguntas: int, formato=None) -> list[Burbuja]:
    """
    Calcula las posiciones de todas las burbujas de respuesta en
    coordenadas de imagen PDF (origen arriba-izquierda).
    """
    return [Burbuja(pregunta=i, opcion=opc, x_pt=x, y_pt=y)
            for i, opc, x, y in LH.obtener(formato).burbujas_respuesta(num_preguntas)]


def calcular_burbujas_codigo(formato=None) -> list[Burbuja]:
    """Calcula posiciones de burbujas del código de estudiante.
    En 'pregunta' va el índice de la columna (0, 1, 2)."""
    return [Burbuja(pregunta=col, opcion=dig, x_pt=x, y_pt=y)
            for col, dig, x, y in LH.obtener(formato).burbujas_codigo()]

# ─────────────────────────────────────────────────────────────────────────────
# Procesamiento de imagen
# ─────────────────────────────────────────────────────────────────────────────

def _redimensionar_si_grande(img: np.ndarray) -> np.ndarray:
    """Reduce la imagen si alguna dimensión supera MAX_DIM, preservando aspecto."""
    h, w = img.shape[:2]
    mayor = max(h, w)
    if mayor <= MAX_DIM:
        return img
    factor = MAX_DIM / mayor
    return cv2.resize(img, (int(w * factor), int(h * factor)),
                      interpolation=cv2.INTER_AREA)


def _detectar_bbox_hoja(img_gray: np.ndarray) -> tuple[int, int, int, int]:
    """
    Detecta el bounding box del papel blanco dentro de la foto.
    Devuelve (x, y, w, h) en píxeles. Si no lo encuentra, devuelve
    el tamaño completo de la imagen.
    """
    h, w = img_gray.shape

    # Umbral: el papel es claro, el fondo (mesa, escritorio) es más oscuro
    _, thresh = cv2.threshold(img_gray, 0, 255,
                              cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)

    contornos, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL,
                                    cv2.CHAIN_APPROX_SIMPLE)
    if not contornos:
        return 0, 0, w, h

    # El papel es el contorno más grande
    cnt = max(contornos, key=cv2.contourArea)
    area = cv2.contourArea(cnt)

    # Solo confiar si ocupa al menos el 40 % de la imagen
    if area < w * h * 0.40:
        return 0, 0, w, h

    bx, by, bw, bh = cv2.boundingRect(cnt)
    return bx, by, bw, bh


def _marcas_candidatos(bin_img: np.ndarray, pw: int) -> list[tuple[float, float]]:
    """Centros de blobs que parecen cuadrados negros sólidos, según un binario."""
    contornos, _ = cv2.findContours(bin_img, cv2.RETR_EXTERNAL,
                                    cv2.CHAIN_APPROX_SIMPLE)
    area_min = (pw * 0.004) ** 2
    area_max = (pw * 0.060) ** 2
    out: list[tuple[float, float]] = []
    for cnt in contornos:
        area = cv2.contourArea(cnt)
        if not (area_min < area < area_max):
            continue
        bx, by, bw, bh = cv2.boundingRect(cnt)
        if bw == 0 or bh == 0:
            continue
        if not (0.45 < bw / bh < 2.2):       # ~cuadrado (tolerante a perspectiva)
            continue
        if area / (bw * bh) < 0.6:           # relleno sólido (descarta letras/aros)
            continue
        out.append((bx + bw / 2.0, by + bh / 2.0))
    return out


def _marcas_por_zona(pts: np.ndarray, px: int, py: int, pw: int, ph: int
                     ) -> Optional[np.ndarray]:
    """De una nube de candidatos, elige el más cercano a cada esquina del papel."""
    ZONA = 0.30
    zonas = [
        (px,      py,      px,               px + pw * ZONA, py,               py + ph * ZONA),
        (px + pw, py,      px + pw * (1 - ZONA), px + pw,     py,               py + ph * ZONA),
        (px + pw, py + ph, px + pw * (1 - ZONA), px + pw,     py + ph * (1 - ZONA), py + ph),
        (px,      py + ph, px,               px + pw * ZONA, py + ph * (1 - ZONA), py + ph),
    ]
    seleccionados: list[np.ndarray] = []
    for ref_x, ref_y, xmin, xmax, ymin, ymax in zonas:
        en_zona = [p for p in pts if xmin <= p[0] <= xmax and ymin <= p[1] <= ymax]
        if not en_zona:
            return None
        ref = np.float32([ref_x, ref_y])
        seleccionados.append(min(en_zona, key=lambda p: float(np.linalg.norm(p - ref))))
    return np.array(seleccionados, dtype=np.float32)


def detectar_marcas(img_gray: np.ndarray) -> Optional[np.ndarray]:
    """
    Detecta los 4 cuadrados negros de las esquinas, robusto a fotos de celular.

    Prueba varias binarizaciones (Otsu, adaptativa para sombras, y umbrales
    fijos) y se queda con la primera que produzca 4 marcas válidas, una por
    esquina del papel. Esto tolera iluminación despareja, sombras y fondos de
    escritorio.

    Returns:
        Array (4, 2) [arriba-izq, arriba-der, abajo-der, abajo-izq] o None.
    """
    h_img, w_img = img_gray.shape

    # Paso 0: bounding box del papel (para ignorar la mesa)
    px, py, pw, ph = _detectar_bbox_hoja(img_gray)

    # Suavizado leve para reducir ruido de cámara/JPEG
    suave = cv2.GaussianBlur(img_gray, (3, 3), 0)

    # Construir varias binarizaciones (marca = blanco sobre negro)
    binarios: list[np.ndarray] = []
    _, b_otsu = cv2.threshold(suave, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    binarios.append(b_otsu)

    # Adaptativa: clave para sombras / luz despareja
    block = max(31, (pw // 8) | 1)           # impar
    b_adap = cv2.adaptiveThreshold(suave, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV, block, 12)
    binarios.append(b_adap)

    # Umbrales fijos de respaldo
    for thv in (60, 90, 120, 150):
        _, bf = cv2.threshold(suave, thv, 255, cv2.THRESH_BINARY_INV)
        binarios.append(bf)

    # Máscara del papel (con margen) para ignorar lo que esté fuera de la hoja
    mask = np.zeros_like(img_gray)
    mx = int(pw * 0.04)
    cv2.rectangle(mask, (max(0, px - mx), max(0, py - mx)),
                  (min(w_img, px + pw + mx), min(h_img, py + ph + mx)), 255, -1)

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    for b in binarios:
        b = cv2.bitwise_and(b, mask)
        closed = cv2.morphologyEx(b, cv2.MORPH_CLOSE, kernel)
        candidatos = _marcas_candidatos(closed, pw)
        if len(candidatos) < 4:
            continue
        pts = np.array(candidatos, dtype=np.float32)
        sel = _marcas_por_zona(pts, px, py, pw, ph)
        if sel is None:
            continue
        xs, ys = sel[:, 0], sel[:, 1]
        # Cordura: las 4 marcas deben abarcar buena parte del papel
        if (xs.max() - xs.min()) < pw * 0.45 or (ys.max() - ys.min()) < ph * 0.45:
            continue
        return sel

    return None


def _relleno_cuadro(gray: np.ndarray, cx: float, cy: float, lado: float,
                    scale: float, umbral: float) -> float:
    """Fracción de píxeles oscuros en el centro de un cuadro (coords en pt)."""
    h = max(2, int(lado * scale * 0.3))
    x, y = int(cx * scale), int(cy * scale)
    roi = gray[max(0, y - h):y + h + 1, max(0, x - h):x + h + 1]
    return float(np.mean(roi < umbral)) if roi.size else 0.0


def _orientar_marcas(img: np.ndarray, marcas: np.ndarray, f: LH.Formato) -> np.ndarray:
    """
    Reordena las 4 marcas detectadas para que la primera sea la esquina
    superior-izquierda REAL de la hoja, aunque la foto esté girada o de cabeza.

    detectar_marcas() las devuelve según su posición en la FOTO. Si la hoja
    está girada 90° (p. ej. media hoja horizontal fotografiada con el celular
    vertical) o 180°, ese orden no corresponde al de la hoja.
      1. La forma del cuadrilátero dice si la hoja está derecha/de cabeza
         o girada de lado.
      2. Entre esas dos opciones, la marca de orientación (cuadrito bajo la
         marca superior-izquierda) decide. Las hojas viejas no la tienen: en
         ese caso se deja el orden tal cual, como siempre.
    """
    p = marcas.astype(np.float32)
    ancho_q = (np.linalg.norm(p[1] - p[0]) + np.linalg.norm(p[2] - p[3])) / 2
    alto_q = (np.linalg.norm(p[3] - p[0]) + np.linalg.norm(p[2] - p[1])) / 2
    esq = f.esquinas()
    prop_f = (esq[1][0] - esq[0][0]) / (esq[3][1] - esq[0][1])
    prop_q = ancho_q / max(alto_q, 1e-6)
    de_lado = abs(np.log(1 / prop_q / prop_f)) < abs(np.log(prop_q / prop_f))
    giros = (1, 3) if de_lado else (0, 2)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    ox, oy, lado = f.orientacion()
    destino = esquinas_pdf(f)                      # escala 1 px/pt: basta para revisar
    mejor, mejor_dif = giros[0], 0.0
    for g in giros:
        cand = np.roll(p, -g, axis=0)
        M = cv2.getPerspectiveTransform(cand, destino)
        w = cv2.warpPerspective(gray, M, (int(f.ancho), int(f.alto)))
        w = D.aplanar_iluminacion(w, int(1.1 * CM))
        umbral = 0.6 * float(np.median(w))
        dentro = _relleno_cuadro(w, ox, oy, lado, 1.0, umbral)
        opuesto = _relleno_cuadro(w, f.ancho - ox, f.alto - oy, lado, 1.0, umbral)
        if dentro >= 0.6 and dentro - opuesto > max(0.4, mejor_dif):
            mejor, mejor_dif = g, dentro - opuesto
    return np.roll(p, -mejor, axis=0)


def corregir_perspectiva(img: np.ndarray,
                         marcas_detectadas: np.ndarray,
                         formato=None) -> tuple[np.ndarray, float]:
    """
    Aplica homografía para proyectar la hoja fotografiada al espacio PDF,
    corrigiendo también si la foto está girada o de cabeza.

    Returns:
        Tupla (imagen_corregida_BGR, escala_px_por_pt).
    """
    f = LH.obtener(formato)
    dst_w = int(f.ancho * RENDER_SCALE)
    dst_h = int(f.alto * RENDER_SCALE)
    marcas_destino = esquinas_pdf(f) * RENDER_SCALE
    marcas_detectadas = _orientar_marcas(img, marcas_detectadas, f)

    M, _ = cv2.findHomography(marcas_detectadas, marcas_destino,
                               cv2.RANSAC, 5.0)
    if M is None:
        raise ValueError("No se pudo calcular la homografía con las marcas detectadas.")

    warped = cv2.warpPerspective(img, M, (dst_w, dst_h))
    return warped, RENDER_SCALE


def binarizar(img_gray: np.ndarray,
              bbox_papel: Optional[tuple[int,int,int,int]] = None) -> np.ndarray:
    """
    Binariza la imagen con umbral Otsu calculado SOLO sobre el papel.

    Si se provee bbox_papel (x, y, w, h), el umbral se calcula exclusivamente
    sobre esa región, eliminando el efecto de fondos oscuros o franjas negras
    que distorsionan el histograma global.

    Debe llamarse UNA sola vez por imagen y reutilizar el resultado.
    """
    h, w = img_gray.shape

    if bbox_papel is not None:
        bx, by, bw, bh = bbox_papel
        # Recortar con un margen interior del 3 % para evitar el borde del papel
        mx, my = max(int(bw * 0.03), 4), max(int(bh * 0.03), 4)
        x0 = max(bx + mx, 0);  y0 = max(by + my, 0)
        x1 = min(bx + bw - mx, w);  y1 = min(by + bh - my, h)
        zona = img_gray[y0:y1, x0:x1]
    else:
        # Fallback: zona interior del 8 %
        mx, my = int(w * 0.08), int(h * 0.08)
        zona = img_gray[my:h - my, mx:w - mx]

    blurred_zona = cv2.GaussianBlur(zona, (3, 3), 0)
    umbral, _ = cv2.threshold(blurred_zona, 0, 255,
                              cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Aplicar umbral calculado a la imagen completa
    blurred = cv2.GaussianBlur(img_gray, (3, 3), 0)
    _, binaria = cv2.threshold(blurred, int(umbral), 255, cv2.THRESH_BINARY_INV)
    return binaria


def medir_fill_burbuja(binaria: np.ndarray,
                        x_pt: float, y_pt: float,
                        scale: float,
                        radio_pt: float = LH.CARTA.radio) -> float:
    """
    Mide la fracción de píxeles oscuros dentro de una burbuja.

    Args:
        binaria: Imagen ya binarizada (INV: oscuro = marcado = 255).
        x_pt, y_pt: Centro en puntos PDF.
        scale: Píxeles por punto PDF.
        radio_pt: Radio de la burbuja impresa, en puntos PDF.

    Returns:
        Fracción [0.0, 1.0] de píxeles marcados dentro del círculo.
    """
    cx = int(x_pt * scale)
    cy = int(y_pt * scale)
    r  = max(2, int(radio_pt * scale * 0.85))

    # Extraer ROI cuadrada para no operar sobre la imagen completa
    x0, y0 = max(cx - r, 0), max(cy - r, 0)
    x1, y1 = min(cx + r + 1, binaria.shape[1]), min(cy + r + 1, binaria.shape[0])
    roi = binaria[y0:y1, x0:x1]

    # Máscara circular local (relativa al ROI)
    mask = np.zeros(roi.shape, dtype=np.uint8)
    cv2.circle(mask, (cx - x0, cy - y0), r, 255, -1)

    total   = cv2.countNonZero(mask)
    relleno = cv2.countNonZero(cv2.bitwise_and(roi, roi, mask=mask))

    return relleno / total if total > 0 else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Lógica de lectura y calificación
# ─────────────────────────────────────────────────────────────────────────────

UMBRAL_RANGO:        float = 0.08   # contraste mínimo para considerar una marca válida
UMBRAL_RANGO_DUDOSO: float = 0.04   # contraste en zona gris: posible X fina o trazo débil


def _clasificar_fila(fills_fila: dict[str, float]) -> tuple[str, Optional[str]]:
    """
    Decide qué opción está marcada en una fila de burbujas.

    Devuelve (estado, opcion) con estado en:
      "ok"     → una sola opción destacada (opcion = esa letra/dígito)
      "doble"  → dos o más opciones claramente rellenas
      "dudosa" → hay algo marcado pero el trazo es débil
      "blanco" → nada marcado

    La doble marca se mide contra la burbuja MÁS VACÍA de la fila: si la segunda
    más rellena también contrasta fuerte con ella (al menos la mitad que la
    primera), son dos marcas. Así un borrón leve no cuenta como doble marca,
    pero dos círculos rellenos sí (antes se leían como "en blanco").
    """
    orden = sorted(fills_fila.items(), key=lambda x: x[1], reverse=True)
    f_max = orden[0][1]
    f_seg = orden[1][1] if len(orden) > 1 else 0.0
    f_min = orden[-1][1]

    contraste_1 = f_max - f_min
    contraste_2 = f_seg - f_min
    if len(orden) > 1 and contraste_2 >= max(UMBRAL_RANGO, 0.5 * contraste_1):
        return "doble", None

    rango = f_max - f_seg
    if rango < UMBRAL_RANGO_DUDOSO:
        return "blanco", None
    if rango < UMBRAL_RANGO:
        return "dudosa", None
    return "ok", orden[0][0]


def leer_respuestas(img_bgr: np.ndarray,
                    num_preguntas: int,
                    scale: float,
                    formato=None) -> ResultadoLectura:
    """
    Lee todas las burbujas de la imagen corregida en un único pase.

    Lógica de detección por rango (max − segundo_mayor):
      Si la diferencia entre el fill más alto y el segundo más alto de una fila
      supera UMBRAL_RANGO, se considera que hay una burbuja marcada (la de mayor fill).
      Si todos los fills son similares → en blanco.
      Si hay dos candidatos que superan el umbral → doble marca.

      Esta estrategia es robusta ante variaciones de iluminación y fondos oscuros
      porque no depende de un umbral absoluto sino de contraste relativo entre opciones.
    """
    f = LH.obtener(formato)
    img_gray  = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    # Luz pareja antes de binarizar: con un solo umbral para toda la hoja, una
    # sombra hacía que las burbujas vacías de la zona oscura parecieran rellenas.
    # El kernel (1.1 cm) es mayor que una burbuja rellena y que las marcas.
    img_gray  = D.aplanar_iluminacion(img_gray, int(1.1 * CM * scale))
    bbox_papel = _detectar_bbox_hoja(img_gray)
    binaria    = binarizar(img_gray, bbox_papel)   # ← Otsu sobre el papel, no el fondo

    # 1. Leer código del estudiante
    codigo_leido = ""
    if LEER_CODIGO_ESTUDIANTE:
        burbujas_cod = calcular_burbujas_codigo(f)
        por_columna: dict[int, list[Burbuja]] = {}
        for b in burbujas_cod:
            por_columna.setdefault(b.pregunta, []).append(b)
        
        for col in range(DIGITOS_CODIGO):
            fills_col = {b.opcion: medir_fill_burbuja(binaria, b.x_pt, b.y_pt, scale, f.radio)
                         for b in por_columna[col]}
            # Mismo criterio estricto que las respuestas: un dígito solo se
            # acepta si destaca con claridad; doble marca o trazo débil → "?"
            estado, digito = _clasificar_fila(fills_col)
            codigo_leido += digito if estado == "ok" else "?"
    else:
        codigo_leido = "N/A"

    # 2. Leer respuestas
    burbujas    = calcular_burbujas(num_preguntas, f)
    por_pregunta: dict[int, list[Burbuja]] = {}
    for b in burbujas:
        por_pregunta.setdefault(b.pregunta, []).append(b)

    respuestas:  dict[int, Optional[str]]       = {}
    fills:       dict[int, dict[str, float]]    = {}
    en_blanco:   list[int]                      = []
    doble_marca: list[int]                      = []
    dudosas:     list[int]                      = []

    for num_p in sorted(por_pregunta):
        fills_fila = {
            b.opcion: medir_fill_burbuja(binaria, b.x_pt, b.y_pt, scale, f.radio)
            for b in por_pregunta[num_p]
        }
        fills[num_p] = fills_fila

        estado, opcion = _clasificar_fila(fills_fila)
        respuestas[num_p] = opcion
        if estado == "blanco":
            en_blanco.append(num_p)
        elif estado == "dudosa":
            # Trazo débil (posible X, visto, borrado): no cuenta, se alerta al docente
            dudosas.append(num_p)
        elif estado == "doble":
            doble_marca.append(num_p)

    return ResultadoLectura(
        respuestas  = respuestas,
        fills       = fills,
        codigo_estudiante=codigo_leido,
        en_blanco   = en_blanco,
        doble_marca = doble_marca,
        dudosas     = dudosas,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Visualización
# ─────────────────────────────────────────────────────────────────────────────

def _calcular_nota(correctas: int, total: int) -> float:
    """
    Nota lineal en escala NOTA_MIN–NOTA_MAX, TRUNCADA a 1 decimal (no redondea),
    con piso en NOTA_PISO: si el resultado es inferior a 1.0, se reemplaza por 1.0.
    Ej.: 0 de 3 → 0.0 → 1.0 ; 1 de 3 → 3.3 ; 2 de 3 → 6.6.
    """
    if total == 0:
        return NOTA_MIN
    valor = NOTA_MIN + (NOTA_MAX - NOTA_MIN) * (correctas / total)
    # Truncar a 1 decimal (el epsilon evita que 9.0 caiga a 8.9 por error de float)
    nota = int(valor * 10 + 1e-9) / 10.0
    # Piso institucional: ninguna nota baja de 1.0
    return max(NOTA_PISO, nota)


def _dibujar_panel_resultado(img: np.ndarray,
                              correctas: int,
                              total: int,
                              en_blanco: list[int],
                              doble_marca: list[int],
                              dudosas: list[int],
                              nombre_examen: str,
                              codigo_estudiante: str) -> np.ndarray:
    """
    Dibuja un panel semitransparente en la parte inferior de la imagen
    con: nombre del examen, aciertos, nota final y alertas.
    """
    h, w = img.shape[:2]
    nota  = _calcular_nota(correctas, total)
    aprueba = nota >= NOTA_APROBACION

    # ── Dimensiones del panel ────────────────────────────────────────
    PANEL_H    = 150 # Aumentado para el código de estudiante
    PADDING    = 18
    y_panel    = h - PANEL_H

    # Fondo semitransparente oscuro
    overlay = img.copy()
    cv2.rectangle(overlay, (0, y_panel), (w, h), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.82, img, 0.18, 0, img)

    # ── Barra de color según resultado (izquierda) ───────────────────
    barra_color = (60, 180, 60) if aprueba else (50, 50, 210)
    cv2.rectangle(img, (0, y_panel), (10, h), barra_color, -1)

    # ── Texto: nombre del examen ─────────────────────────────────────
    y_txt = y_panel + PADDING + 16
    if nombre_examen:
        cv2.putText(img, nombre_examen.upper(), (PADDING + 10, y_txt),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1, cv2.LINE_AA)
        y_txt += 24

    # Dibujar Código de Estudiante
    texto_estudiante = f"Estudiante: {codigo_estudiante}"
    cv2.putText(img, texto_estudiante, (PADDING + 10, y_txt),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (100, 255, 255), 1, cv2.LINE_AA)
    y_txt += 26

    # ── Texto: aciertos / total ──────────────────────────────────────
    texto_aciertos = f"Aciertos: {correctas} / {total}"
    cv2.putText(img, texto_aciertos, (PADDING + 10, y_txt),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (230, 230, 230), 1, cv2.LINE_AA)

    # ── Nota grande (derecha del panel) ─────────────────────────────
    texto_nota  = f"{nota:.1f}"
    texto_label = "NOTA"
    color_nota  = (80, 220, 80) if aprueba else (80, 80, 230)

    # Posición: pegado a la derecha
    (tw, th), _ = cv2.getTextSize(texto_nota, cv2.FONT_HERSHEY_SIMPLEX, 2.2, 3)
    x_nota = w - tw - PADDING - 10
    y_nota = y_panel + PANEL_H - PADDING - 4
    cv2.putText(img, texto_nota, (x_nota, y_nota),
                cv2.FONT_HERSHEY_SIMPLEX, 2.2, color_nota, 3, cv2.LINE_AA)

    # Etiqueta "NOTA" encima del número
    (lw, _), _ = cv2.getTextSize(texto_label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    cv2.putText(img, texto_label, (x_nota + (tw - lw) // 2, y_panel + PADDING + 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1, cv2.LINE_AA)

    # Estado APROBADO / REPROBADO
    estado_txt   = "APROBADO" if aprueba else "REPROBADO"
    estado_color = (80, 220, 80) if aprueba else (80, 80, 230)
    y_estado = y_nota - th - 6
    cv2.putText(img, estado_txt, (x_nota, y_estado),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, estado_color, 1, cv2.LINE_AA)

    # ── Alertas (en blanco / doble marca) ────────────────────────────
    y_alerta = y_txt + 26
    if en_blanco:
        nums = ", ".join(str(n) for n in en_blanco[:10])
        sufijo = "…" if len(en_blanco) > 10 else ""
        cv2.putText(img, f"Sin resp: {nums}{sufijo}", (PADDING + 10, y_alerta),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (80, 200, 255), 1, cv2.LINE_AA)
        y_alerta += 20
    if doble_marca:
        nums = ", ".join(str(n) for n in doble_marca[:10])
        sufijo = "…" if len(doble_marca) > 10 else ""
        cv2.putText(img, f"Doble marca: {nums}{sufijo}", (PADDING + 10, y_alerta),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (60, 160, 255), 1, cv2.LINE_AA)
        y_alerta += 20
    if dudosas:
        nums = ", ".join(str(n) for n in dudosas[:10])
        sufijo = "…" if len(dudosas) > 10 else ""
        cv2.putText(img, f"REVISAR: preg. {nums}{sufijo} — marca debil, verificar",
                    (PADDING + 10, y_alerta),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 165, 255), 1, cv2.LINE_AA)

    return img


def dibujar_anotaciones(img_bgr:       np.ndarray,
                         resultado:     ResultadoLectura,
                         clave:         Optional[dict[int, str]],
                         scale:         float,
                         nombre_examen: str = "",
                         formato=None) -> np.ndarray:
    """
    Dibuja sobre la imagen corregida:
      • Círculos de color en cada burbuja (verde=correcto, rojo=incorrecto, azul=sin clave)
      • Panel inferior con nota (escala 0–10), aciertos y alertas.

    Reutiliza los fills de ResultadoLectura — no reprocesa la imagen.

    Colores de burbuja:
      Verde  → marcada y correcta   (con clave)
      Rojo   → marcada e incorrecta (con clave)
      Azul   → marcada              (sin clave)
      Gris   → sin marcar
    """
    f = LH.obtener(formato)
    debug = img_bgr.copy()
    r_px      = max(3, int(f.radio * scale))

    # Dibujar Cuadrícula de Estudiante
    if LEER_CODIGO_ESTUDIANTE:
        burbujas_cod = calcular_burbujas_codigo(f)
        for b in burbujas_cod:
            cx, cy = int(b.x_pt * scale), int(b.y_pt * scale)
            marcada = False
            
            # Verificamos si esta burbuja es parte del código leído
            if resultado.codigo_estudiante and "?" not in resultado.codigo_estudiante:
                if b.pregunta < len(resultado.codigo_estudiante):
                    if b.opcion == resultado.codigo_estudiante[b.pregunta]:
                        marcada = True
                        
            if marcada:
                cv2.circle(debug, (cx, cy), r_px, (220, 180, 0), 3, cv2.LINE_AA) # Cian suave
            else:
                cv2.circle(debug, (cx, cy), r_px, (200, 200, 200), 1, cv2.LINE_AA) # Gris


    # Dibujar Cuadrícula de Preguntas
    burbujas_preg: list[Burbuja] = calcular_burbujas(len(resultado.respuestas), f)
    por_pregunta: dict[int, list[Burbuja]] = {}
    for b in burbujas_preg:
        por_pregunta.setdefault(b.pregunta, []).append(b)

    correctas = 0

    for num_p, bbs in sorted(por_pregunta.items()):
        resp_alumno = resultado.respuestas.get(num_p)
        resp_clave  = clave.get(num_p) if clave else None

        if resp_alumno == resp_clave and resp_alumno is not None:
            correctas += 1

        for b in bbs:
            cx = int(b.x_pt * scale)
            cy = int(b.y_pt * scale)

            marcada = (b.opcion == resp_alumno)
            es_correcta_clave = (b.opcion == resp_clave)

            # Lógica de colores
            if clave:
                if marcada and es_correcta_clave:
                    color = (0, 200, 0)      # Verde
                    grosor = 3
                elif marcada and not es_correcta_clave:
                    color = (0, 0, 220)      # Rojo
                    grosor = 3
                elif not marcada and es_correcta_clave:
                    color = (0, 150, 0)      # Verde oscuro (la correcta que no marcó)
                    grosor = 2
                else:
                    color = (200, 200, 200)  # Gris (ignorada)
                    grosor = 1
            else:
                # Si no hay clave, solo marcamos en azul las seleccionadas
                if marcada:
                    color = (220, 100, 0)    # Azul (BGR)
                    grosor = 3
                else:
                    color = (200, 200, 200)
                    grosor = 1

            cv2.circle(debug, (cx, cy), r_px, color, grosor, cv2.LINE_AA)

    # Destacar las 4 marcas de esquina con círculos verdes suaves
    esquinas = esquinas_pdf(f) * scale
    for pt in esquinas:
        cv2.circle(debug, (int(pt[0]), int(pt[1])), 15, (100, 255, 100), 2, cv2.LINE_AA)

    # Dibujar panel inferior en una franja AGREGADA debajo de la hoja, para que
    # no tape las últimas filas (en la media hoja quedaban ocultas)
    debug = cv2.copyMakeBorder(debug, 0, 150, 0, 0, cv2.BORDER_CONSTANT,
                               value=(20, 20, 20))
    total_preg = len(clave) if clave else len(resultado.respuestas)
    _dibujar_panel_resultado(
        debug, correctas, total_preg,
        resultado.en_blanco, resultado.doble_marca, resultado.dudosas,
        nombre_examen, resultado.codigo_estudiante
    )

    return debug


# ─────────────────────────────────────────────────────────────────────────────
# Manejo de archivos e I/O
# ─────────────────────────────────────────────────────────────────────────────

MENSAJE_SIN_MARCAS = ("No se vieron las 4 marcas negras de las esquinas. "
                      "Repite la foto con la hoja completa y buena luz.")


def leer_hoja(img_bgr: np.ndarray, num_preguntas: int, formato=None
              ) -> tuple[np.ndarray, float, ResultadoLectura]:
    """
    Pipeline completo sobre una foto ya cargada:
      detectar las 4 marcas → enderezar (también si está girada o de cabeza)
      → leer código y respuestas.

    Returns:
        (imagen_enderezada, escala_px_por_pt, ResultadoLectura)
    Raises:
        ValueError si no se ven las 4 marcas (mensaje listo para el docente).
    """
    img_gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    marcas = detectar_marcas(img_gray)
    if marcas is None:
        raise ValueError(MENSAJE_SIN_MARCAS)
    recta, scale = corregir_perspectiva(img_bgr, marcas, formato)
    return recta, scale, leer_respuestas(recta, num_preguntas, scale, formato)


def leer_foto(img_bgr: np.ndarray, num_preguntas: int, formato=None
              ) -> tuple[np.ndarray, float, ResultadoLectura]:
    """
    Igual que leer_hoja, pero primero "escanea" la foto con el digitalizador
    (recorta el fondo y quita sombras). Si así no se ven las 4 marcas
    (p. ej. el digitalizador eligió mal el contorno), reintenta con la foto
    original. Es la función que usa la app, tanto para fotos cargadas como
    para las de la cámara.
    """
    try:
        escaneada = D.alinear(img_bgr)
    except Exception as e:
        print(f"  ⚠️  Digitalizador: {e}")
        escaneada = None
    if escaneada is not None:
        try:
            return leer_hoja(escaneada, num_preguntas, formato)
        except ValueError:
            pass
    return leer_hoja(img_bgr, num_preguntas, formato)


def leer_imagen(ruta) -> Optional[np.ndarray]:
    """cv2.imread que también funciona con tildes/ñ en la ruta (en Windows
    cv2.imread devuelve None si la ruta tiene, p. ej., 'César')."""
    try:
        datos = np.fromfile(str(ruta), dtype=np.uint8)
    except OSError:
        return None
    return cv2.imdecode(datos, cv2.IMREAD_COLOR) if datos.size else None


def guardar_imagen(ruta, img: np.ndarray) -> bool:
    """cv2.imwrite que también funciona con tildes/ñ en la ruta."""
    ext = Path(ruta).suffix or ".jpg"
    ok, buf = cv2.imencode(ext, img)
    if ok:
        buf.tofile(str(ruta))
    return bool(ok)


def cargar_imagen(ruta: Path) -> np.ndarray:
    """Carga y reduce la imagen si es demasiado grande."""
    if not Path(ruta).exists():
        raise FileNotFoundError(f"Imagen no encontrada: {ruta}")

    img = leer_imagen(ruta)
    if img is None:
        raise ValueError(f"No se pudo abrir la imagen: {Path(ruta).name}")

    return _redimensionar_si_grande(img)


def cargar_datos_examen(ruta_json: Path) -> DatosExamen:
    """Lee la clave JSON de un examen."""
    if not ruta_json.exists():
        raise FileNotFoundError(f"Archivo de clave no encontrado: {ruta_json}")

    with ruta_json.open('r', encoding='utf-8') as f:
        datos = json.load(f)

    try:
        nombre = datos["examen"]
        total  = int(datos["total_preguntas"])
        arr_correctas = datos["clave_correctas"]
    except KeyError as e:
        raise ValueError(f"JSON inválido, falta clave: {e}")

    clave = {i + 1: val for i, val in enumerate(arr_correctas)}
    # Las claves viejas no traen formato: todas eran de hoja completa
    formato = LH.obtener(datos.get("formato")).clave
    return DatosExamen(nombre, total, clave, formato)


# ─────────────────────────────────────────────────────────────────────────────
# Flujo de la app: una foto → nota
# ─────────────────────────────────────────────────────────────────────────────
# Carpetas dentro de profesores/<Profesor>/<Grado>/<Curso>/
SUB_CRUDOS     = "examenes_crudos"              # fotos tal como llegan
SUB_ARCHIVADOS = "examenes_crudos_archivados"   # fotos ya calificadas (originales)
SUB_RESULTADOS = "resultados"                   # imágenes marcadas + notas_<examen>.json


def nombre_unico(nombre_original: str, prefijo: str = "") -> str:
    """
    Nombre de archivo que no choca con fotos anteriores: dos fotos llamadas
    'IMG_0001.jpg' tomadas en días distintos ya no se reemplazan entre sí.
    Ej.: 'IMG 0001.JPG' → 'IMG_0001_20261012-153012_a3f9.jpg'
    """
    import re
    import uuid
    from datetime import datetime
    p = Path(nombre_original)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", p.stem).strip("_") or "foto"
    stem = (prefijo + stem)[:40]
    sufijo = p.suffix.lower() or ".jpg"
    marca = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stem}_{marca}_{uuid.uuid4().hex[:4]}{sufijo}"


def calificar_foto(ruta_cruda: Path, ruta_json: Path, ruta_curso: Path,
                   datos: Optional[DatosExamen] = None) -> dict:
    """
    Califica UNA foto que ya está en '<curso>/examenes_crudos/'.

      1-2. La escanea con el digitalizador y la lee (ver leer_foto).
      3. Guarda la imagen anotada en '<curso>/resultados/'.
      4. Archiva la foto original, para que nunca se vuelva a calificar en
         otra corrida ni en otro examen.

    Devuelve el registro para notas_<examen>.json:
      {archivo, codigo, aciertos, total, respuestas, fecha, imagen}
    o, si no se pudo leer, {archivo, codigo: "ERROR", error, ...}.
    """
    from datetime import datetime
    ruta_cruda = Path(ruta_cruda)
    if datos is None:
        datos = cargar_datos_examen(ruta_json)

    archivados = ruta_curso / SUB_ARCHIVADOS
    salida     = ruta_curso / SUB_RESULTADOS
    for d in (archivados, salida):
        d.mkdir(parents=True, exist_ok=True)

    registro = {"archivo": ruta_cruda.name, "total": datos.total_preguntas,
                "fecha": datetime.now().isoformat(timespec="seconds")}

    resultado: Optional[ResultadoLectura] = None
    ultimo_error = ""
    try:
        # 1-2. Escanear y leer (con la foto original como respaldo)
        original = cargar_imagen(ruta_cruda)
        recta, scale, resultado = leer_foto(original, datos.total_preguntas, datos.formato)
    except Exception as e:
        ultimo_error = str(e)

    # 3. Imagen anotada
    if resultado is not None:
        anotada = dibujar_anotaciones(recta, resultado, datos.clave, scale,
                                      datos.nombre, datos.formato)
        registro["imagen"] = f"{ruta_cruda.stem}_calificado.jpg"
        guardar_imagen(salida / registro["imagen"], anotada)

    # 4. Archivar la original
    try:
        shutil.move(str(ruta_cruda), str(archivados / ruta_cruda.name))
    except Exception:
        pass

    if resultado is None:
        registro.update({"codigo": "ERROR", "aciertos": 0, "error": ultimo_error})
        return registro

    aciertos = sum(1 for p, r in resultado.respuestas.items()
                   if r is not None and r == datos.clave.get(p))
    registro.update({
        "codigo": resultado.codigo_estudiante,
        "aciertos": aciertos,
        "respuestas": {str(p): r for p, r in sorted(resultado.respuestas.items())},
    })
    return registro
