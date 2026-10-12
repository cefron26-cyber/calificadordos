#!/usr/bin/env python3
"""
layout_hoja.py — Geometría ÚNICA de las hojas de respuesta OMR.

Este archivo es la única fuente de verdad sobre dónde va cada cosa en la hoja:
marcas de esquina, encabezado, burbujas del código y de las respuestas.
  • generador_pdf.py lo usa para DIBUJAR la hoja.
  • lector_omr.py   lo usa para LEERLA.
Así es imposible que el dibujo y la lectura no coincidan.

Sistema de coordenadas: puntos PDF (1 pt = 1/72 pulgada), con el origen
ARRIBA-IZQUIERDA de UNA hoja (la que se fotografía) y la Y creciendo hacia
abajo, igual que en una imagen. El generador las convierte al sistema del PDF.

Formatos:
  • "carta"        Hoja completa (una por página). Es el formato original: su
                   geometría NO debe cambiar, para que las hojas ya impresas se
                   sigan leyendo.
  • "media_carta"  Media hoja horizontal: dos hojas iguales por página carta,
                   para recortar y ahorrar papel. Hasta 40 preguntas.
"""

from __future__ import annotations

from dataclasses import dataclass

CM: float = 72.0 / 2.54           # 1 cm en puntos PDF
CARTA_W: float = 612.0            # página carta en puntos
CARTA_H: float = 792.0

OPCIONES: tuple[str, ...] = ("A", "B", "C", "D")
DIGITOS_CODIGO: int = 3
OPCIONES_CODIGO: tuple[str, ...] = tuple(str(i) for i in range(10))


@dataclass(frozen=True)
class Formato:
    clave: str                    # identificador guardado en la clave JSON
    descripcion: str              # texto para la interfaz
    ancho: float                  # tamaño de UNA hoja (lo que se fotografía)
    alto: float
    hojas_por_pagina: int         # copias de la hoja en cada página carta

    # Marcas de esquina (cuadrados negros)
    margen_marca: float           # distancia del borde de la hoja a la marca
    tam_marca: float
    # Marca de orientación: cuadrado más pequeño debajo de la marca
    # superior-izquierda. Permite saber si la foto está girada o al revés.
    tam_orientacion: float

    # Encabezado
    x_texto: float
    y_titulo: float               # línea base del título
    tam_titulo: float
    y_nombre: float
    y_grado: float
    tam_campos: float
    campo_nombre: str
    campo_grado: str
    logo: tuple[float, float, float, float]          # x, y (arriba), ancho, alto
    instrucciones: tuple[tuple[float, str, float, bool], ...]  # (y, texto, tamaño, negrita)

    # Cuadrículas de burbujas
    y_titulos: float              # línea base de "CÓDIGO ALUMNO" / "RESPUESTAS"
    tam_titulos: float
    y_burbujas: float             # centro de la primera fila de burbujas
    x_codigo_titulo: float
    x_codigo: float               # centro de la primera columna del código
    x_preguntas: float            # inicio (número de pregunta) de la 1.ª columna
    ancho_columna: float          # distancia entre columnas de preguntas
    filas_por_columna: int
    max_preguntas: int
    radio: float = 0.22 * CM
    esp_x: float = 0.65 * CM      # distancia entre burbujas de una fila
    esp_y: float = 0.58 * CM      # distancia entre filas
    sep_numero: float = 0.8 * CM  # del número de pregunta a la burbuja A

    # ── Posiciones derivadas ────────────────────────────────────────────────
    def esquinas(self) -> list[tuple[float, float]]:
        """Centros de las 4 marcas: [arriba-izq, arriba-der, abajo-der, abajo-izq]."""
        m = self.margen_marca + self.tam_marca / 2
        return [(m, m), (self.ancho - m, m),
                (self.ancho - m, self.alto - m), (m, self.alto - m)]

    def orientacion(self) -> tuple[float, float, float]:
        """(cx, cy, lado) de la marca de orientación, debajo de la marca
        superior-izquierda, separada 0.3 cm para que no se fundan en la foto."""
        cx = self.margen_marca + self.tam_marca / 2
        cy = (self.margen_marca + self.tam_marca + 0.3 * CM
              + self.tam_orientacion / 2)
        return cx, cy, self.tam_orientacion

    def burbujas_codigo(self) -> list[tuple[int, str, float, float]]:
        """[(columna, dígito, x, y)] de las burbujas del código del estudiante."""
        out = []
        for col in range(DIGITOS_CODIGO):
            x = self.x_codigo + col * self.esp_x
            for fila, dig in enumerate(OPCIONES_CODIGO):
                out.append((col, dig, x, self.y_burbujas + fila * self.esp_y))
        return out

    def posicion_pregunta(self, i: int) -> tuple[float, float]:
        """(x del número, y del centro de la fila) de la pregunta i (1-based)."""
        col = (i - 1) // self.filas_por_columna
        fila = (i - 1) % self.filas_por_columna
        return (self.x_preguntas + col * self.ancho_columna,
                self.y_burbujas + fila * self.esp_y)

    def burbujas_respuesta(self, n: int) -> list[tuple[int, str, float, float]]:
        """[(pregunta, opción, x, y)] de las burbujas de respuesta."""
        out = []
        for i in range(1, n + 1):
            x_base, y = self.posicion_pregunta(i)
            for j, opc in enumerate(OPCIONES):
                out.append((i, opc, x_base + self.sep_numero + j * self.esp_x, y))
        return out


# ─────────────────────────────────────────────────────────────────────────────
# HOJA COMPLETA (formato original — NO cambiar estas medidas)
# ─────────────────────────────────────────────────────────────────────────────
CARTA = Formato(
    clave="carta",
    descripcion="Hoja completa (1 por página)",
    ancho=CARTA_W, alto=CARTA_H, hojas_por_pagina=1,
    margen_marca=1.5 * CM, tam_marca=0.5 * CM, tam_orientacion=0.4 * CM,
    x_texto=2.5 * CM,
    y_titulo=2.5 * CM, tam_titulo=16,
    y_nombre=3.8 * CM, y_grado=4.8 * CM, tam_campos=11,
    campo_nombre="Nombres y Apellidos: _____________________________________",
    campo_grado="Grado y Curso: ________________   Asignatura: _______________",
    logo=(CARTA_W - 7.0 * CM, 1.5 * CM, 4.0 * CM, 3.5 * CM),
    instrucciones=(
        (6.2 * CM, "INSTRUCCIONES DE MARCADO:", 9, True),
        (6.7 * CM, "Use lápiz o lapicero negro. Rellene COMPLETAMENTE el círculo sin salirse.", 8.5, False),
        (7.1 * CM, "- Forma correcta: el círculo totalmente relleno. Evite equis, chulos o medio relleno.", 8.5, False),
        (7.5 * CM, "- El código del estudiante se rellena con los tres dígitos que indique el docente.", 8.5, False),
    ),
    y_titulos=8.7 * CM, tam_titulos=10,
    y_burbujas=9.7 * CM,
    x_codigo_titulo=2.5 * CM, x_codigo=2.8 * CM,
    x_preguntas=8.0 * CM, ancho_columna=4.6 * CM,
    filas_por_columna=25, max_preguntas=50,
)

# ─────────────────────────────────────────────────────────────────────────────
# MEDIA HOJA (dos iguales por página carta, para recortar)
# ─────────────────────────────────────────────────────────────────────────────
MEDIA_CARTA = Formato(
    clave="media_carta",
    descripcion="Media hoja (2 por página)",
    ancho=CARTA_W, alto=CARTA_H / 2, hojas_por_pagina=2,
    margen_marca=1.0 * CM, tam_marca=0.5 * CM, tam_orientacion=0.4 * CM,
    x_texto=2.0 * CM,
    y_titulo=1.6 * CM, tam_titulo=13,
    y_nombre=2.45 * CM, y_grado=3.2 * CM, tam_campos=10,
    campo_nombre="Nombres y Apellidos: ________________________________________________",
    campo_grado="Grado y Curso: _______________   Asignatura: _______________________",
    logo=(17.3 * CM, 1.0 * CM, 2.4 * CM, 2.4 * CM),
    instrucciones=(
        (3.95 * CM, "Use lápiz o lapicero negro y rellene COMPLETAMENTE el círculo. "
                    "Evite equis, chulos o medio relleno.", 7.5, False),
        (4.35 * CM, "En CÓDIGO rellene los tres dígitos que le indique el docente.", 7.5, False),
    ),
    y_titulos=5.0 * CM, tam_titulos=9,
    y_burbujas=5.9 * CM,
    x_codigo_titulo=2.0 * CM, x_codigo=2.3 * CM,
    x_preguntas=5.2 * CM, ancho_columna=3.8 * CM,
    filas_por_columna=10, max_preguntas=40,
)

FORMATOS: dict[str, Formato] = {f.clave: f for f in (MEDIA_CARTA, CARTA)}
FORMATO_PREDETERMINADO = MEDIA_CARTA.clave


def obtener(formato=None) -> Formato:
    """Acepta un Formato, su clave ("carta", "media_carta") o None.
    None o una clave desconocida → "carta" (las claves JSON viejas no tienen
    formato y todas son de hoja completa)."""
    if isinstance(formato, Formato):
        return formato
    return FORMATOS.get(str(formato or "carta"), CARTA)


def formato_para(num_preguntas: int) -> Formato:
    """El formato que más papel ahorra para esa cantidad de preguntas."""
    return MEDIA_CARTA if num_preguntas <= MEDIA_CARTA.max_preguntas else CARTA
