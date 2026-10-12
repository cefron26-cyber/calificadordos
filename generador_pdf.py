#!/usr/bin/env python3
"""
generador_pdf.py — Generador de Hojas de Respuesta OMR para el Liceo Sahagún.

** Versión 100% Python puro: NO depende de reportlab, fpdf2 ni fontTools. **
Escribe el PDF byte a byte, por lo que funciona en cualquier dispositivo
(incluido Android ARM) sin librerías que compilen.

La geometría (marcas de esquina, burbujas, encabezado) sale de layout_hoja.py,
el mismo archivo que usa lector_omr.py para leer: dibujo y lectura coinciden
siempre. Formatos: hoja completa ("carta") o dos medias hojas por página
("media_carta").
"""

from pathlib import Path

import layout_hoja as LH
from layout_hoja import CM


# Anchos estándar de la fuente Helvetica (Adobe AFM, en milésimas de em).
# Solo se usan para CENTRAR texto; los caracteres fuera de rango usan 556.
_HELV_W = {
    ' ': 278, '!': 278, '"': 355, '#': 556, '$': 556, '%': 889, '&': 667,
    "'": 191, '(': 333, ')': 333, '*': 389, '+': 584, ',': 278, '-': 333,
    '.': 278, '/': 278, '0': 556, '1': 556, '2': 556, '3': 556, '4': 556,
    '5': 556, '6': 556, '7': 556, '8': 556, '9': 556, ':': 278, ';': 278,
    '<': 584, '=': 584, '>': 584, '?': 556, '@': 1015, 'A': 667, 'B': 667,
    'C': 722, 'D': 722, 'E': 667, 'F': 611, 'G': 778, 'H': 722, 'I': 278,
    'J': 500, 'K': 667, 'L': 556, 'M': 833, 'N': 722, 'O': 778, 'P': 667,
    'Q': 778, 'R': 722, 'S': 667, 'T': 611, 'U': 722, 'V': 667, 'W': 944,
    'X': 667, 'Y': 667, 'Z': 611, '[': 278, '\\': 278, ']': 278, '^': 469,
    '_': 556, '`': 333, 'a': 556, 'b': 556, 'c': 500, 'd': 556, 'e': 556,
    'f': 278, 'g': 556, 'h': 556, 'i': 222, 'j': 222, 'k': 500, 'l': 222,
    'm': 833, 'n': 556, 'o': 556, 'p': 556, 'q': 556, 'r': 333, 's': 500,
    't': 278, 'u': 556, 'v': 500, 'w': 722, 'x': 500, 'y': 500, 'z': 500,
    '{': 334, '|': 260, '}': 334, '~': 584,
}


def _ancho_texto(s, size):
    total = sum(_HELV_W.get(ch, 556) for ch in s)
    return total * size / 1000.0


def _escape_pdf(s):
    """Codifica el texto a bytes (WinAnsi/cp1252) y escapa los caracteres
    especiales del formato PDF, devolviendo la cadena entre paréntesis."""
    b = s.encode("cp1252", "replace")
    b = b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    return b"(" + b + b")"


class _PDF:
    """Mini-escritor de PDF de una sola página (carta), con Helvetica e imágenes PNG."""

    def __init__(self, ancho=612.0, alto=792.0):
        self.W = ancho
        self.H = alto
        self._ops = []       # lista de bytes (operadores del contenido)
        self._imagenes = []  # objetos XObject de imagen, ya armados

    # -- operadores de dibujo (coordenadas con origen abajo-izquierda) --
    def color_relleno(self, r, g, b):
        self._ops.append(b"%.3f %.3f %.3f rg\n" % (r, g, b))

    def color_linea(self, r, g, b):
        self._ops.append(b"%.3f %.3f %.3f RG\n" % (r, g, b))

    def grosor_linea(self, w):
        self._ops.append(b"%.2f w\n" % w)

    def punteado(self, trazo=0.0, espacio=0.0):
        """Línea punteada (trazo y espacio en pt). Sin argumentos: línea continua."""
        if trazo:
            self._ops.append(b"[%.1f %.1f] 0 d\n" % (trazo, espacio))
        else:
            self._ops.append(b"[] 0 d\n")

    def rect_relleno(self, x, y, w, h):
        self._ops.append(b"%.2f %.2f %.2f %.2f re f\n" % (x, y, w, h))

    def rect_caja(self, x, y, w, h):
        self._ops.append(b"%.2f %.2f %.2f %.2f re B\n" % (x, y, w, h))

    def linea(self, x1, y1, x2, y2):
        self._ops.append(b"%.2f %.2f m %.2f %.2f l S\n" % (x1, y1, x2, y2))

    def circulo(self, cx, cy, r):
        k = 0.5522847498 * r
        self._ops.append(b"%.2f %.2f m\n" % (cx + r, cy))
        self._ops.append(b"%.2f %.2f %.2f %.2f %.2f %.2f c\n"
                         % (cx + r, cy + k, cx + k, cy + r, cx, cy + r))
        self._ops.append(b"%.2f %.2f %.2f %.2f %.2f %.2f c\n"
                         % (cx - k, cy + r, cx - r, cy + k, cx - r, cy))
        self._ops.append(b"%.2f %.2f %.2f %.2f %.2f %.2f c\n"
                         % (cx - r, cy - k, cx - k, cy - r, cx, cy - r))
        self._ops.append(b"%.2f %.2f %.2f %.2f %.2f %.2f c\n"
                         % (cx + k, cy - r, cx + r, cy - k, cx + r, cy))
        self._ops.append(b"S\n")

    def texto(self, x, y, s, size, bold=False, color=(0, 0, 0)):
        fuente = b"/F2" if bold else b"/F1"
        r, g, b = color
        self._ops.append(
            b"BT %b %.2f Tf %.3f %.3f %.3f rg %.2f %.2f Td %b Tj ET\n"
            % (fuente, size, r, g, b, x, y, _escape_pdf(s)))

    def texto_centrado(self, x, y, s, size, bold=False, color=(0, 0, 0)):
        self.texto(x - _ancho_texto(s, size) / 2.0, y, s, size, bold, color)

    def registrar_png(self, datos_png):
        """Registra un PNG y devuelve (nombre, ancho, alto) para dibujarlo con
        imagen(). Se incrusta sin descomprimir: el PDF entiende el mismo
        compresor y filtros que el PNG. Solo PNG de 8 bits, gris o RGB, sin
        transparencia ni entrelazado; si no, devuelve None."""
        info = _leer_png(datos_png)
        if info is None:
            return None
        ancho, alto, colores, idat = info
        espacio = b"/DeviceRGB" if colores == 3 else b"/DeviceGray"
        self._imagenes.append(
            b"<< /Type /XObject /Subtype /Image /Width %d /Height %d "
            b"/ColorSpace %b /BitsPerComponent 8 /Filter /FlateDecode "
            b"/DecodeParms << /Predictor 15 /Colors %d /BitsPerComponent 8 /Columns %d >> "
            b"/Length %d >>\nstream\n%b\nendstream"
            % (ancho, alto, espacio, colores, ancho, len(idat), idat))
        return b"/Im%d" % len(self._imagenes), ancho, alto

    def imagen(self, nombre, x, y, w, h):
        self._ops.append(b"q %.2f 0 0 %.2f %.2f %.2f cm %b Do Q\n" % (w, h, x, y, nombre))

    def to_bytes(self):
        contenido = b"".join(self._ops)
        # Objetos: 1 catálogo, 2 páginas, 3 página, 4-5 fuentes, 6 contenido, 7… imágenes
        recursos_img = b"".join(b"/Im%d %d 0 R " % (i + 1, 7 + i)
                                for i in range(len(self._imagenes)))
        objetos = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.0f %.0f] "
             b"/Resources << /Font << /F1 4 0 R /F2 5 0 R >> /XObject << %b>> >> "
             b"/Contents 6 0 R >>" % (self.W, self.H, recursos_img)),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
            b"<< /Length %d >>\nstream\n%b\nendstream" % (len(contenido), contenido),
        ] + self._imagenes
        salida = bytearray(b"%PDF-1.4\n")
        offsets = []
        for i, obj in enumerate(objetos, start=1):
            offsets.append(len(salida))
            salida += b"%d 0 obj\n%b\nendobj\n" % (i, obj)
        inicio_xref = len(salida)
        n = len(objetos) + 1
        salida += b"xref\n0 %d\n" % n
        salida += b"0000000000 65535 f \n"
        for off in offsets:
            salida += b"%010d 00000 n \n" % off
        salida += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
                   % (n, inicio_xref))
        return bytes(salida)


def _leer_png(datos):
    """(ancho, alto, colores, datos_IDAT) de un PNG que el PDF puede usar tal
    cual, o None si el PNG no es compatible."""
    import struct
    if not datos.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    pos, idat, ihdr = 8, [], None
    while pos + 8 <= len(datos):
        largo, tipo = struct.unpack(">I4s", datos[pos:pos + 8])
        cuerpo = datos[pos + 8:pos + 8 + largo]
        if tipo == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", cuerpo)
        elif tipo == b"IDAT":
            idat.append(cuerpo)
        elif tipo == b"IEND":
            break
        pos += 12 + largo
    if ihdr is None or not idat:
        return None
    ancho, alto, bits, tipo_color, _, _, entrelazado = ihdr
    colores = {0: 1, 2: 3}.get(tipo_color)
    if bits != 8 or colores is None or entrelazado:
        return None
    return ancho, alto, colores, b"".join(idat)


# Logo reducido para el PDF (el logo.png original pesa ~1.7 MB). Si no está,
# se usa el original; si ninguno sirve, la hoja sale sin logo.
_DIR = Path(__file__).resolve().parent
_RUTAS_LOGO = [_DIR / "assets" / "logo_pdf.png", _DIR / "logo.png", Path("logo.png")]


def _cargar_logo(pdf):
    for ruta in _RUTAS_LOGO:
        try:
            if ruta.exists():
                reg = pdf.registrar_png(ruta.read_bytes())
                if reg:
                    return reg
        except Exception:
            pass
    return None


def _dibujar_hoja(pdf, f, y0, num_preguntas, logo):
    """Dibuja UNA hoja del formato f. y0 = distancia (pt) del borde superior de
    la página al borde superior de esta hoja. Las posiciones salen de
    layout_hoja (origen arriba-izquierda); aquí se pasan al sistema del PDF."""
    def Y(y):  # de "Y hacia abajo dentro de la hoja" a "Y del PDF"
        return pdf.H - (y0 + y)

    GRIS = (0.4, 0.4, 0.4)
    NEGRO = (0.0, 0.0, 0.0)

    # 1. MARCAS DE REFERENCIA (cuadrados negros en las 4 esquinas)
    pdf.color_relleno(0, 0, 0)
    t = f.tam_marca
    for cx, cy in f.esquinas():
        pdf.rect_relleno(cx - t / 2, Y(cy) - t / 2, t, t)
    # Marca de orientación: le dice al lector cuál es la esquina superior izquierda
    ox, oy, lado = f.orientacion()
    pdf.rect_relleno(ox - lado / 2, Y(oy) - lado / 2, lado, lado)

    # 2. ENCABEZADO
    pdf.texto(f.x_texto, Y(f.y_titulo), "Liceo Sahagún - Hoja de Respuestas",
              f.tam_titulo, bold=True)
    pdf.texto(f.x_texto, Y(f.y_nombre), f.campo_nombre, f.tam_campos)
    pdf.texto(f.x_texto, Y(f.y_grado), f.campo_grado, f.tam_campos)

    # 3. LOGO (centrado en su casilla, sin deformar)
    if logo:
        nombre, iw, ih = logo
        lx, ly, lw, lh = f.logo
        esc = min(lw / iw, lh / ih)
        w, h = iw * esc, ih * esc
        pdf.imagen(nombre, lx + (lw - w) / 2, Y(ly + (lh + h) / 2), w, h)

    # 4. INSTRUCCIONES DE MARCADO
    for y, txt, tam, negrita in f.instrucciones:
        pdf.texto(f.x_texto, Y(y), txt, tam, bold=negrita)

    # 5. TÍTULOS DE COLUMNAS
    pdf.texto(f.x_codigo_titulo, Y(f.y_titulos), "CÓDIGO ALUMNO", f.tam_titulos, bold=True)
    pdf.texto(f.x_preguntas, Y(f.y_titulos), "RESPUESTAS", f.tam_titulos, bold=True)

    # Flecha minimalista sobre las columnas del código
    pdf.color_linea(0.2, 0.2, 0.2)
    pdf.grosor_linea(1)
    x_start = f.x_codigo
    x_end = x_start + 2 * f.esp_x
    y_flecha = Y(f.y_titulos + 0.35 * CM)
    pdf.linea(x_start, y_flecha, x_end, y_flecha)
    pdf.linea(x_end, y_flecha, x_end - 0.15 * CM, y_flecha + 0.1 * CM)
    pdf.linea(x_end, y_flecha, x_end - 0.15 * CM, y_flecha - 0.1 * CM)

    # 6. MATRIZ: CÓDIGO ALUMNO (3 columnas, dígitos 0-9)
    pdf.color_linea(0, 0, 0)
    pdf.grosor_linea(0.8)
    for _, dig, x, y in f.burbujas_codigo():
        pdf.circulo(x, Y(y), f.radio)
        pdf.texto_centrado(x, Y(y) - 0.08 * CM, dig, 7.5, color=GRIS)

    # 7. MATRIZ: RESPUESTAS (A, B, C, D)
    for i in range(1, num_preguntas + 1):
        x_base, y = f.posicion_pregunta(i)
        pdf.texto(x_base, Y(y) - 0.1 * CM, f"{i:02d}.", 9.5, color=NEGRO)
    pdf.color_linea(0, 0, 0)
    pdf.grosor_linea(0.8)
    for _, opc, x, y in f.burbujas_respuesta(num_preguntas):
        pdf.circulo(x, Y(y), f.radio)
        pdf.texto_centrado(x, Y(y) - 0.08 * CM, opc, 7.5, color=GRIS)


def generar_hoja_omr(archivo_salida, num_preguntas: int, formato="carta") -> None:
    """
    Genera el PDF de la hoja OMR (Python puro). La geometría sale de
    layout_hoja.py, la misma que usa el lector.

    formato: "carta" (una hoja por página) o "media_carta" (dos hojas iguales
    por página, con línea de corte).
    """
    f = LH.obtener(formato)
    if not 1 <= num_preguntas <= f.max_preguntas:
        raise ValueError(f"El formato '{f.descripcion}' admite de 1 a "
                         f"{f.max_preguntas} preguntas (pediste {num_preguntas}).")
    pdf = _PDF(LH.CARTA_W, LH.CARTA_H)
    logo = _cargar_logo(pdf)

    for k in range(f.hojas_por_pagina):
        _dibujar_hoja(pdf, f, k * f.alto, num_preguntas, logo)

    # Línea de corte entre las dos hojas
    if f.hojas_por_pagina == 2:
        y_corte = pdf.H - f.alto
        etiqueta = "recortar por aquí"
        ancho_et = _ancho_texto(etiqueta, 7) + 0.4 * CM
        x_et = (pdf.W - ancho_et) / 2
        pdf.color_linea(0.55, 0.55, 0.55)
        pdf.grosor_linea(0.6)
        pdf.punteado(4, 3)
        pdf.linea(0.5 * CM, y_corte, x_et, y_corte)
        pdf.linea(x_et + ancho_et, y_corte, pdf.W - 0.5 * CM, y_corte)
        pdf.punteado()
        pdf.texto_centrado(pdf.W / 2, y_corte - 2.5, etiqueta, 7, color=(0.45, 0.45, 0.45))

    salida = Path(archivo_salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_bytes(pdf.to_bytes())
