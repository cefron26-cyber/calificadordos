# Calificador OMR — Liceo Sahagún

Aplicación (hecha en **Kivy**, Python) que califica exámenes de opción múltiple a
partir de **fotos** de las hojas de respuestas. Lee el código del estudiante y las
respuestas marcadas, calcula la nota y arma el registro en Excel. Funciona en PC y
se empaqueta como **app de Android** con Buildozer.

---

## Qué hace

1. **Configuración inicial (una sola vez):** nombre del profesor, sus grados y
   cuántos cursos tiene cada uno (los cursos se nombran A, B, C…).
2. **Estudiantes:** lista por curso con código automático según la posición
   (001, 002…). Permite agregar, **editar o eliminar** (toca al estudiante) e
   **importar una planilla** (Excel/CSV).
3. **Exámenes:** crea la **clave** de respuestas y el **PDF** de la hoja:
   - **Media hoja** (2 iguales por página, para recortar): hasta 40 preguntas.
   - **Hoja completa** (1 por página): hasta 50 preguntas.
4. **Calificar:** carga una o varias fotos → las **escanea** (recorta el fondo,
   endereza y quita sombras, al estilo CamScanner) → las **lee y califica** →
   guarda la imagen marcada y las notas.
   Muestra una ventana de progreso y, al final, el **historial** de hojas del
   examen: toca **Ver** para ver la hoja marcada, corregir su código o eliminarla.
5. **Registro de notas:** genera el **Excel** cruzando código → estudiante, con
   nota, promedio y "No presentó" para los ausentes. Se guarda en **Descargas**
   y se puede abrir o **compartir** (WhatsApp, Drive, correo).
6. **Copia de seguridad** (pantalla de inicio): guarda todo en un `.zip` en
   Descargas y lo restaura en otro celular o tras reinstalar la app.

---

## Requisitos (PC)

- **Python 3.11, 3.12 o 3.13** (recomendado el de **Anaconda**). No usar 3.14.
- Librerías:
  ```
  pip install kivy opencv-python numpy openpyxl
  ```

---

## Archivos del proyecto

| Archivo | Para qué |
|---|---|
| `main.py` | La aplicación (interfaz y pantallas). |
| `layout_hoja.py` | **Geometría única** de las hojas (marcas, burbujas, encabezado) para cada formato. La usan el generador y el lector. |
| `generador_pdf.py` | Dibuja el PDF de la hoja (Python puro, funciona en Android). |
| `digitalizador.py` | "Escáner": encuentra la hoja en la foto, la endereza y quita sombras. |
| `lector_omr.py` | Motor OMR: marcas de esquina, orientación, lectura de burbujas, nota. |
| `registro_notas.py` | Arma el Excel cruzando código → estudiante. |
| `gestor_salones.py` | Crea las carpetas Profesor/Grado/Curso. |
| `plataforma.py` | Lo que cambia entre PC y Android: elegir archivos, Descargas, abrir/compartir, copia de seguridad. |
| `assets/` | Fuentes de la app y `logo_pdf.png` (logo reducido para el PDF). |
| `logo.png` | Logo original en alta resolución (no se empaqueta en el APK). |
| `buildozer.spec`, `build.yml` | Compilación del APK de Android. |

---

## Cómo ejecutarla (PC)

1. Abre una terminal (en Windows, **Anaconda Prompt**) en la carpeta del proyecto.
2. Ejecuta:
   ```
   python main.py
   ```
3. La primera vez pide la configuración inicial; luego abre directo en el menú.

Para probar solo el escáner con fotos reales:
```
python digitalizador.py foto1.jpg foto2.jpg
```
Guarda `foto1_escaneada.jpg` junto a cada foto.

---

## Cómo se usa (flujo típico)

1. **Estudiantes** → importa o crea la lista del curso (cada alumno recibe un código).
2. **Exámenes** → crea el examen con su clave y descarga el PDF para imprimir.
3. Si es media hoja, recorta por la línea punteada. Cada alumno rellena **su
   código** y sus respuestas.
4. Toma una foto de cada hoja (en cualquier orientación; se corrige sola).
5. **Calificar** → elige el examen → carga las fotos → revisa los resultados
   (corrige el código si alguno salió dudoso).
6. **Registro de notas** → genera y abre el Excel del curso.

> **Importante:** el código que el alumno rellena en la hoja debe coincidir con su
> código en la lista. Si no coincide, o si dos hojas distintas traen el mismo
> código, esa hoja aparece en la pestaña **"Por revisar"** del Excel con el motivo.
> Usa **Corregir código** para arreglarlo.

---

## La hoja de respuestas

- **4 cuadrados negros en las esquinas:** el lector los busca para enderezar la
  hoja y ubicar cada burbuja con precisión.
- **Cuadrado pequeño debajo del de arriba a la izquierda:** marca de orientación.
  Le dice al lector cuál es la esquina superior izquierda, así la foto se puede
  tomar girada o de cabeza. No debe taparse ni rellenarse.

---

## Dónde quedan los datos

Todo se guarda dentro de la carpeta `profesores/` (en PC, junto a `main.py`;
en Android, en la carpeta privada de la app; por eso conviene la copia de
seguridad):

```
profesores/<Profesor>/<Grado>/<Curso>/
    estudiantes/                 → lista_estudiantes.xlsx
    examenes_claves/             → respuestas_<examen>.json (clave + formato de hoja)
    examenes_crudos/             → fotos recién cargadas (de paso)
    examenes_crudos_archivados/  → fotos originales ya calificadas
    resultados/                  → imágenes marcadas + notas_<examen>.json
    resultados_excel/            → registro_<examen>.xlsx
```

Los PDF de las hojas, los registros Excel y las copias de seguridad se guardan
en la carpeta **Descargas**.

---

## Escala de notas

Lineal de **0 a 10**: `nota = 10 × (aciertos / total)`, truncada a un decimal
(sin redondear hacia arriba) y con **piso de 1.0** (ninguna nota baja de 1).
Aprueba con **6.0**.
