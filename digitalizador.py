#!/usr/bin/env python3
"""
digitalizador.py — "Escáner" de hojas, al estilo CamScanner.

A partir de una foto de celular:
  1. Busca el contorno de la hoja de papel combinando varias pistas
     (bordes, brillo y "papel = claro y sin color"), y se queda con el
     cuadrilátero más creíble: grande, convexo, con ángulos razonables y
     proporción de hoja.
  2. Endereza la perspectiva y recorta el fondo.
  3. Aplana la iluminación: quita sombras y deja el papel blanco y la tinta
     nítida, para que la lectura no dependa de la luz.

No conoce el diseño de la hoja: de eso se encarga lector_omr.py, que después
busca las 4 marcas negras sobre la imagen ya escaneada.

Uso en PC para probar con fotos reales:
    python digitalizador.py foto1.jpg [foto2.jpg ...]
    → guarda foto1_escaneada.jpg junto a cada foto.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

LADO_DETECCION = 900      # px del lado mayor al buscar la hoja (rápido y suficiente)
AREA_MIN = 0.12           # la hoja debe ocupar al menos el 12 % de la foto
LADO_MAX_SALIDA = 2400    # px máximos del lado mayor de la hoja escaneada


# ─────────────────────────────────────────────────────────────────────────────
# Iluminación
# ─────────────────────────────────────────────────────────────────────────────

def aplanar_iluminacion(img: np.ndarray, kernel: Optional[int] = None) -> np.ndarray:
    """
    Quita sombras y degradados de luz: estima el brillo del PAPEL en cada zona
    (borrando la tinta con un cierre morfológico) y divide la imagen por él.
    El papel queda ~blanco en todas partes y la tinta conserva su contraste.

    Acepta gris o color (en color se procesa cada canal, así el resultado
    sigue en color). kernel: tamaño en px; debe ser mayor que las manchas de
    tinta más grandes que importan (burbujas, marcas de esquina).
    """
    if kernel is None:
        kernel = max(15, min(img.shape[:2]) // 12)
    # Se calcula a la mitad de tamaño en fotos grandes: mucho más rápido
    chico = 2 if max(img.shape[:2]) > 1200 else 1
    reducida = cv2.resize(img, None, fx=1 / chico, fy=1 / chico,
                          interpolation=cv2.INTER_AREA) if chico > 1 else img
    k = (kernel // chico) | 1  # impar
    # El cierre "borra" lo oscuro más chico que el kernel → queda solo el papel.
    # Kernel rectangular: OpenCV lo calcula por filas y columnas (muy rápido).
    fondo = cv2.morphologyEx(reducida, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    fondo = cv2.GaussianBlur(fondo, (0, 0), max(1.0, k / 6))
    if chico > 1:
        fondo = cv2.resize(fondo, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR)
    return cv2.divide(img, np.maximum(fondo, 1), scale=255)


# ─────────────────────────────────────────────────────────────────────────────
# Detección del contorno de la hoja
# ─────────────────────────────────────────────────────────────────────────────

def ordenar_esquinas(pts: np.ndarray) -> np.ndarray:
    """
    Ordena 4 puntos en sentido horario empezando por el de arriba-izquierda:
    [arriba-izq, arriba-der, abajo-der, abajo-izq].
    Ordena por ángulo alrededor del centro (no por suma/resta de coordenadas,
    que falla cuando la hoja está girada cerca de 45°).
    """
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    centro = pts.mean(axis=0)
    angulos = np.arctan2(pts[:, 1] - centro[1], pts[:, 0] - centro[0])
    pts = pts[np.argsort(angulos)]                 # horario (la Y crece hacia abajo)
    inicio = int(np.argmin(pts.sum(axis=1)))       # el más cercano a arriba-izq
    return np.roll(pts, -inicio, axis=0)


def _a_cuadrilatero(contorno: np.ndarray) -> Optional[np.ndarray]:
    """Aproxima un contorno por 4 vértices (o None si no se parece a un cuadrilátero)."""
    casco = cv2.convexHull(contorno)
    peri = cv2.arcLength(casco, True)
    for eps in (0.01, 0.02, 0.03, 0.045, 0.06, 0.08):
        aprox = cv2.approxPolyDP(casco, eps * peri, True)
        if len(aprox) == 4:
            return aprox.reshape(4, 2).astype(np.float32)
        if len(aprox) < 4:
            break
    # Respaldo: rectángulo mínimo, solo si el contorno lo llena bien
    rect = cv2.minAreaRect(casco)
    area_rect = rect[1][0] * rect[1][1]
    if area_rect > 0 and cv2.contourArea(casco) / area_rect > 0.9:
        return cv2.boxPoints(rect).astype(np.float32)
    return None


UMBRAL_BORDE = 3.5        # cambio de brillo mínimo (% por px) para contar como borde


def _mapa_bordes(chica: np.ndarray) -> np.ndarray:
    """
    Intensidad de borde por píxel, RELATIVA al brillo de la zona (en % por px).

    Relativa porque una sombra oscurece por igual el papel y la mesa: el salto
    absoluto de brillo se achica, pero la proporción (papel ~17 % más claro
    que una mesa blanca) se mantiene. Se toma el mayor de los 3 canales de
    color, así también se ve el borde cuando papel y mesa son igual de claros
    pero de distinto color.
    """
    suave = cv2.GaussianBlur(chica, (5, 5), 0).astype(np.float32)
    canales = cv2.split(suave) if suave.ndim == 3 else [suave]
    mag = np.zeros(chica.shape[:2], np.float32)
    for c in canales:
        gx = cv2.Sobel(c, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(c, cv2.CV_32F, 0, 1, ksize=3)
        brillo_local = cv2.blur(c, (9, 9)) + 12      # +12: evita exagerar el ruido en lo muy oscuro
        mag = np.maximum(mag, cv2.magnitude(gx, gy) / 8.0 / brillo_local * 100)
    return mag


def _muestrear(mapa: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Valores del mapa en esos puntos (0 si caen fuera de la imagen)."""
    h, w = mapa.shape
    pts = np.rint(pts).astype(int)
    dentro = (pts[:, 0] >= 0) & (pts[:, 0] < w) & (pts[:, 1] >= 0) & (pts[:, 1] < h)
    vals = np.zeros(len(pts), np.float32)
    vals[dentro] = mapa[pts[dentro, 1], pts[dentro, 0]]
    return vals


def _ajustar_a_bordes(q: np.ndarray, mag: np.ndarray, rango: int = 10) -> np.ndarray:
    """
    Corre cada lado del cuadrilátero, en paralelo, hasta la posición
    (±rango px) donde el borde real es más fuerte, y recalcula las esquinas
    como intersección de los lados ajustados. Corrige que el contorno de una
    máscara quede unos px adentro o afuera del borde verdadero del papel.
    """
    lineas = []
    for i in range(4):
        a, b = q[i], q[(i + 1) % 4]
        d = b - a
        largo = float(np.linalg.norm(d))
        if largo < 1:
            return q
        u = d / largo
        n = np.array([-u[1], u[0]], np.float32)          # normal al lado
        base = a + d * np.linspace(0.1, 0.9, 40)[:, None]
        fuerza = [float(np.mean(_muestrear(mag, base + n * o))) for o in range(-rango, rango + 1)]
        mejor = int(np.argmax(fuerza)) - rango
        lineas.append((a + n * mejor, u))

    esquinas = []
    for i in range(4):
        (p1, u1), (p2, u2) = lineas[i - 1], lineas[i]   # la esquina i une el lado i-1 y el i
        A = np.array([u1, -u2], np.float32).T
        if abs(np.linalg.det(A)) < 1e-6:
            return q
        s = np.linalg.solve(A, p2 - p1)[0]
        esquinas.append(p1 + u1 * s)
    return np.array(esquinas, np.float32)


def _soporte_lados(q: np.ndarray, bordes: np.ndarray) -> list[float]:
    """Para cada lado del cuadrilátero: fracción de su recorrido que pasa
    sobre un borde real. La hoja verdadera tiene sus 4 lados sobre bordes;
    un contorno "fundido" con un lápiz o un cuaderno tiene lados que
    cruzan la mesa sin borde debajo."""
    soporte = []
    for i in range(4):
        a, b = q[i], q[(i + 1) % 4]
        n = max(24, int(np.linalg.norm(b - a) / 3))
        pts = a + (b - a) * np.linspace(0.04, 0.96, n)[:, None]   # sin las esquinas
        soporte.append(float(np.mean(_muestrear(bordes, pts) > UMBRAL_BORDE)))
    return soporte


def _puntaje(quad: np.ndarray, gris: np.ndarray, bordes: np.ndarray) -> float:
    """Qué tan creíble es que este cuadrilátero sea la hoja (0 = descartado)."""
    h, w = gris.shape
    q = ordenar_esquinas(quad)
    frac = cv2.contourArea(q) / float(w * h)
    if frac < AREA_MIN or frac > 0.995:
        return 0.0
    if not cv2.isContourConvex(q.reshape(-1, 1, 2).astype(np.int32)):
        return 0.0

    # Ángulos internos razonables (la perspectiva los deforma, pero no tanto)
    for i in range(4):
        a, b, c = q[i - 1], q[i], q[(i + 1) % 4]
        v1, v2 = a - b, c - b
        cos = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-6)
        if not 40 <= np.degrees(np.arccos(np.clip(cos, -1, 1))) <= 140:
            return 0.0

    # Proporción de hoja (carta 1.29, media carta 1.55), con margen por perspectiva
    lados = [np.linalg.norm(q[i] - q[(i + 1) % 4]) for i in range(4)]
    largo = max(lados[0] + lados[2], lados[1] + lados[3])
    corto = min(lados[0] + lados[2], lados[1] + lados[3])
    if largo / max(corto, 1e-6) > 2.4:
        return 0.0

    # Si las 4 esquinas tocan el borde de la foto, es la foto entera (no hay hoja)
    margen = 0.015 * max(w, h)
    en_borde = sum(1 for x, y in q if x < margen or y < margen
                   or x > w - margen or y > h - margen)
    if en_borde == 4:
        return 0.0

    # Los 4 lados deben apoyarse en bordes reales de la imagen
    soporte = _soporte_lados(q, bordes)
    if min(soporte) < 0.5 or np.mean(soporte) < 0.75:
        return 0.0

    # El papel debe ser más claro que lo que lo rodea
    mascara = np.zeros_like(gris)
    cv2.fillConvexPoly(mascara, q.astype(np.int32), 255)
    dentro = cv2.mean(gris, mask=mascara)[0]
    fuera = cv2.mean(gris, mask=cv2.bitwise_not(mascara))[0] if frac < 0.97 else dentro - 30
    if dentro - fuera < 5:
        return 0.0

    # Manda el ajuste a los bordes; entre candidatos igual de bien apoyados,
    # gana el más grande (el borde del papel, no un recuadro impreso adentro)
    return float(np.mean(soporte)) ** 4 * (0.4 + frac) * (1.0 - 0.2 * en_borde)


def detectar_hoja(img: np.ndarray) -> Optional[np.ndarray]:
    """
    Busca la hoja en la foto. Devuelve sus 4 esquinas en coordenadas de la
    foto original, ordenadas [arriba-izq, arriba-der, abajo-der, abajo-izq],
    o None si no hay un contorno de hoja creíble.
    """
    h, w = img.shape[:2]
    esc = min(1.0, LADO_DETECCION / max(h, w))
    chica = cv2.resize(img, None, fx=esc, fy=esc, interpolation=cv2.INTER_AREA) if esc < 1 else img
    gris = cv2.cvtColor(chica, cv2.COLOR_BGR2GRAY) if chica.ndim == 3 else chica
    suave = cv2.GaussianBlur(gris, (5, 5), 0)
    mag = _mapa_bordes(chica)
    # Engrosado 2 px para medir el soporte (tolera pequeñas desviaciones)
    bordes = cv2.dilate(mag, np.ones((5, 5), np.uint8))

    # Candidatos: contornos de varias "máscaras" que podrían aislar el papel
    mascaras = []
    # A) Bordes, cerrados para unir los tramos cortados
    mascaras.append(((bordes > UMBRAL_BORDE).astype(np.uint8) * 255, cv2.RETR_LIST))
    # B) Brillo: el papel es más claro que el fondo
    _, claro = cv2.threshold(suave, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mascaras.append((claro, cv2.RETR_EXTERNAL))
    # C) Papel = claro y sin color (fondos claros pero de color: madera, mesas)
    if chica.ndim == 3:
        hsv = cv2.cvtColor(cv2.GaussianBlur(chica, (5, 5), 0), cv2.COLOR_BGR2HSV)
        _, brillo_alto = cv2.threshold(hsv[..., 2], 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        mascaras.append((cv2.bitwise_and(brillo_alto, (hsv[..., 1] < 45).astype(np.uint8) * 255),
                         cv2.RETR_EXTERNAL))

    cierre = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    mejor, mejor_p = None, 0.0
    for m, modo in mascaras:
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cierre)
        contornos, _ = cv2.findContours(m, modo, cv2.CHAIN_APPROX_SIMPLE)
        # Pedazos grandes: una sombra marcada, un lápiz encima o un doblez
        # pueden partir la hoja en dos, así que también se prueba cada par
        # de pedazos unidos (el puntaje por bordes decide si forman la hoja).
        piezas = [c for c in sorted(contornos, key=cv2.contourArea, reverse=True)[:8]
                  if cv2.contourArea(c) >= AREA_MIN / 4 * gris.size]
        candidatos = piezas + [np.vstack([a, b]) for i, a in enumerate(piezas)
                               for b in piezas[i + 1:]]
        for c in candidatos:
            quad = _a_cuadrilatero(c)
            if quad is None:
                continue
            quad = _ajustar_a_bordes(ordenar_esquinas(quad), mag)
            p = _puntaje(quad, gris, bordes)
            if p > mejor_p:
                mejor, mejor_p = quad, p

    if mejor is None:
        return None
    return ordenar_esquinas(mejor / esc)


# ─────────────────────────────────────────────────────────────────────────────
# Escaneo
# ─────────────────────────────────────────────────────────────────────────────

def enderezar(img: np.ndarray, esquinas: np.ndarray) -> np.ndarray:
    """Recorta la hoja y corrige la perspectiva (vista de frente)."""
    tl, tr, br, bl = ordenar_esquinas(esquinas)
    ancho = max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl))
    alto = max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr))
    f = min(1.0, LADO_MAX_SALIDA / max(ancho, alto))
    ancho, alto = int(ancho * f), int(alto * f)
    destino = np.float32([[0, 0], [ancho - 1, 0], [ancho - 1, alto - 1], [0, alto - 1]])
    M = cv2.getPerspectiveTransform(np.float32([tl, tr, br, bl]), destino)
    return cv2.warpPerspective(img, M, (ancho, alto), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)


def alinear(img: np.ndarray) -> Optional[np.ndarray]:
    """
    Escanea una foto: hoja recortada, de frente y con luz pareja.
    Devuelve None si no encontró la hoja (quien llama sigue con la foto original).
    """
    esquinas = detectar_hoja(img)
    if esquinas is None:
        return None
    hoja = enderezar(img, esquinas)
    return aplanar_iluminacion(hoja)


def escanear_archivo(origen: Path, destino: Path) -> bool:
    """Escanea un archivo de imagen y guarda el resultado (acepta tildes en la ruta)."""
    datos = np.fromfile(str(origen), dtype=np.uint8)
    img = cv2.imdecode(datos, cv2.IMREAD_COLOR) if datos.size else None
    if img is None:
        return False
    hoja = alinear(img)
    if hoja is None:
        return False
    ok, buf = cv2.imencode(destino.suffix or ".jpg", hoja)
    if ok:
        buf.tofile(str(destino))
    return bool(ok)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for arg in sys.argv[1:]:
        p = Path(arg)
        salida = p.with_name(p.stem + "_escaneada.jpg")
        print(("✓ " if escanear_archivo(p, salida) else "✗ no se encontró la hoja en ") + str(salida if salida.exists() else p))
