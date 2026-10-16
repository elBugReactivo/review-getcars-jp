# Subastas — captura de resultados y fotos a una base local

Herramienta en Python para guardar en una base de datos **local** (SQLite) los resultados de las búsquedas de subastas de maquinaria que ves en el navegador, junto con sus fotos.

Tú navegas la página normalmente; Chrome guarda lo que va recibiendo en un archivo `.har`; el script lo lee, limpia los datos, los guarda sin duplicados y organiza las fotos en carpetas.

```
Navegas en Chrome  →  Guardas un .har  →  py subastas.py ingest  →  subastas.db + imagenes\
                                                                          │
                                         py subastas.py imagenes  ────────┤  (fotos en buen tamaño)
                                         py subastas.py export    ────────┤  (CSV para Excel)
                                         py subastas.py ver       ────────┘  (buscar un lote y sus fotos)
```

---

## Índice

1. [Requisitos](#1-requisitos)
2. [Instalación](#2-instalación)
3. [Estructura de carpetas](#3-estructura-de-carpetas)
4. [Uso rápido (resumen)](#4-uso-rápido-resumen)
5. [Paso 1 — Capturar el HAR en Chrome](#5-paso-1--capturar-el-har-en-chrome)
6. [Paso 2 — Procesar el HAR (`ingest`)](#6-paso-2--procesar-el-har-ingest)
7. [Paso 3 — Revisar qué falta (`status`)](#7-paso-3--revisar-qué-falta-status)
8. [Paso 4 — Descargar las fotos (`imagenes`)](#8-paso-4--descargar-las-fotos-imagenes)
9. [Paso 5 — Exportar a Excel (`export`)](#9-paso-5--exportar-a-excel-export)
10. [Buscar un lote y sus fotos (`ver`)](#10-buscar-un-lote-y-sus-fotos-ver)
11. [Otros comandos](#11-otros-comandos)
12. [Referencia de comandos y opciones](#12-referencia-de-comandos-y-opciones)
13. [La base de datos](#13-la-base-de-datos)
14. [Las fotos](#14-las-fotos)
15. [Consultas SQL útiles](#15-consultas-sql-útiles)
16. [Rutina recomendada](#16-rutina-recomendada)
17. [Respaldo](#17-respaldo)
18. [Solución de problemas](#18-solución-de-problemas)
19. [Seguridad y buenas prácticas](#19-seguridad-y-buenas-prácticas)
20. [Referencia de campos del sitio](#20-referencia-de-campos-del-sitio)

---

## 1. Requisitos

| Herramienta | Para qué | Obligatorio |
|---|---|---|
| **Python 3.8 o superior** (probado con 3.12 y 3.14) | Ejecutar el script | Sí |
| **Google Chrome** | Navegar la página y guardar el `.har` | Sí |
| **DB Browser for SQLite** ([sqlitebrowser.org](https://sqlitebrowser.org)) | Ver la base y hacer consultas sin programar | Recomendado |
| **Excel o LibreOffice** | Abrir los CSV exportados | Recomendado |
| **VS Code** | Editar el script y usar la terminal integrada | Opcional |
| **pyperclip** (`py -m pip install pyperclip`) | Solo para el modo `vigilar` | Opcional |

El script **no necesita librerías externas** para el uso normal: solo Python.

### Comprobar Python

Abre una terminal (PowerShell) y escribe:

```
py --version
```

Si responde `Python 3.x.x`, está listo. En este README todos los comandos usan `py`. Si tienes `python` en el PATH, puedes usar `python` en su lugar.

> Si `python` abre la Microsoft Store: *Configuración → Aplicaciones → Configuración avanzada de aplicaciones → Alias de ejecución de aplicaciones* y apaga *python.exe* y *python3.exe*.

---

## 2. Instalación

1. Crea la carpeta del proyecto (o usa tu repositorio) y copia `subastas.py` dentro.
2. Crea las carpetas `inbox` y `exports`.
3. Abre una terminal **en esa carpeta** y crea la base:

```
cd C:\Users\TU_USUARIO\Documents\reposjp\review-getcars-jp
mkdir inbox, exports
py subastas.py init
```

Verás `BD lista: subastas.db`. Listo.

> **Importante:** ejecuta siempre los comandos **desde la carpeta del proyecto**. El script busca `subastas.db`, `inbox\` e `imagenes\` en la carpeta donde estás parado. Si lo ejecutas desde otra carpeta, creará una base nueva y vacía y parecerá que perdiste los datos.

---

## 3. Estructura de carpetas

```
review-getcars-jp\
├── subastas.py            ← el script
├── README.md              ← este archivo
├── subastas.db            ← la base de datos (se crea con init)
├── inbox\                 ← aquí dejas los .har por procesar          (la creas tú)
│   └── procesados\        ← los .har ya procesados                    (automática)
├── exports\               ← los CSV que exportes                      (la creas tú)
└── imagenes\              ← las fotos                                 (automática)
    └── TCM\                         ← marca
        ├── OTHER\                   ← búsqueda
        │   └── FD35Z7\              ← modelo
        │       └── 2026-10-15_JU_Aichi_9060\     ← fecha_subasta_lote
        │           ├── 3xvQUkDZZwLbELa_x.webp    ← foto delantera
        │           ├── 3xvQUkDZZwLbELa_y.webp    ← foto trasera
        │           └── 3xvQUkDZZwLbELa_z.webp    ← hoja de subasta
        └── FORKLIFT\
```

Junto a `subastas.db` pueden aparecer `subastas.db-wal` y `subastas.db-shm` mientras el script está corriendo. Son parte de la base: **no los borres**.

---

## 4. Uso rápido (resumen)

```
py subastas.py init                          # solo la primera vez
py subastas.py ingest inbox --mover          # procesa los .har de inbox\
py subastas.py status                        # qué páginas faltan
py subastas.py imagenes                      # descarga las fotos en buen tamaño
py subastas.py export exports\todo.csv       # CSV para Excel
py subastas.py ver FD35Z7                    # busca un lote y muestra sus fotos
```

---

## 5. Paso 1 — Capturar el HAR en Chrome

Un archivo **HAR** es una grabación de todo lo que la página descargó mientras navegabas: los datos de cada página de resultados y las miniaturas de las fotos.

### 5.1 Preparar Chrome

1. Abre la página de subastas e inicia sesión.
2. Presiona **F12** para abrir DevTools y ve a la pestaña **Network** (Red).
3. Marca estas dos casillas de la barra superior:
   - ☑ **Preserve log** — no borra lo capturado al cambiar de página.
   - ☑ **Disable cache** — obliga a descargar cada página de nuevo (si viene de caché, el HAR puede no traer el contenido).
4. Haz clic en 🚫 **Clear** para empezar con la lista vacía.
5. **No escribas nada en el filtro** de Network. Deja que se vea todo; el script elige lo que sirve. Si filtras, el HAR no traerá las fotos.

### 5.2 Navegar

1. Haz tu búsqueda (por ejemplo, TCM → OTHER).
2. Recorre las páginas **en orden**: 1, 2, 3… hasta la última. Espera a que cada una cargue completa, incluidas las miniaturas.
3. Deja uno o dos segundos entre páginas.
4. Puedes seguir con otra búsqueda (TCM → FORKLIFT, Kobelco, Caterpillar…) sin cerrar DevTools: todo se acumula. Recomendado: **no más de 3–4 búsquedas por HAR**, así si algo falla repites poco.
5. Fíjate si en pantalla aparecen los **precios finales**. Si en la web salen vacíos, también vendrán vacíos en el HAR.

> **Tip:** si la página permite mostrar más resultados por página (50 o 100), úsalo. Tendrás menos páginas que recorrer.

### 5.3 Guardar el HAR

1. En la lista de Network, clic derecho sobre cualquier fila → **Save all as HAR** (en Chrome reciente también está el botón ⬇ **Export HAR**).
2. Si te pregunta, elige la versión **completa** (no la "sanitized") para que incluya las miniaturas de las fotos.
3. Guárdalo en `inbox\` con un nombre claro, por ejemplo `tcm_2026-10-16.har`.

> ⚠ **Guarda el HAR antes de cerrar la pestaña o DevTools.** Si los cierras, pierdes la captura.

---

## 6. Paso 2 — Procesar el HAR (`ingest`)

```
py subastas.py ingest inbox --mover
```

El script:

1. Lee cada `.har` (o `.txt`) de `inbox\`.
2. Extrae las respuestas de resultados (`tpl_poisk`), limpia los textos (katakana, acentos, etiquetas HTML) y guarda cada lote en la base.
3. Saca del HAR las **miniaturas** de las fotos y las enlaza a su lote.
4. Con `--mover`, pasa el archivo a `inbox\procesados\`. Si ya existe uno con el mismo nombre, le agrega fecha y hora.

### Qué vas a ver

```
  + TCM OTHER | pág 1/6 | 20 lotes, 20 nuevos (tcm_2026-10-16.har#1)
  + TCM OTHER | pág 2/6 | 20 lotes, 20 nuevos (tcm_2026-10-16.har#130)
  ...
  imágenes en tcm_2026-10-16.har: 500 guardadas, 24 sin foto en el sitio, 0 sin lote conocido, 3 sin contenido en el HAR
```

| Mensaje | Significado |
|---|---|
| `+ TCM OTHER \| pág 3/6 \| 20 lotes, 20 nuevos` | Página guardada. "nuevos" = lotes que no estaban en la base. |
| `= ya procesada` | Esa misma respuesta ya se había procesado. No se duplica nada. |
| `<-- AVISO: ningún lote trae precio final (t=0)` | La página llegó sin precios. Se guarda igual, pero revisa en la web por qué. |
| `? sin datos de resultados` | El archivo no trae páginas de resultados. Repite la captura. |
| `24 sin foto en el sitio` | El sitio no tiene foto para esos lotes (devuelve una imagen vacía). Es normal. |
| `3 sin contenido en el HAR` | Chrome no guardó esas imágenes (error del sitio o captura incompleta). Se pueden bajar con `imagenes`. |
| `! error en ...` | Algo falló al leer una respuesta. El resto del archivo se procesa igual. |

### Reglas importantes

- **Sin duplicados:** cada lote se identifica por su ID interno (`lot_uid`). Si aparece otra vez, se **actualiza**, no se duplica.
- **Los precios no se pierden:** si una captura nueva trae precio final 0 y la base ya tenía un precio real, se conserva el precio real.
- **Los pendientes se actualizan solos:** los lotes de subastas recientes que aún no tienen resultado quedan como `pendiente`. Cuando vuelvas a capturar esa búsqueda, se actualizan con su estado y precio.
- Puedes procesar un archivo concreto: `py subastas.py ingest inbox\tcm_2026-10-16.har`

---

## 7. Paso 3 — Revisar qué falta (`status`)

```
py subastas.py status
```

```
TCM FORKLIFT                 3/3 págs | 57/57 lotes | COMPLETO | últ: 2026-10-16T08:56:32
TCM OTHER                    5/6 págs | 100/119 lotes | faltan [4] | últ: 2026-10-16T08:56:32
```

- **págs:** páginas capturadas / total de páginas de la búsqueda.
- **lotes:** lotes en la base / total de resultados que informa el sitio.
- **faltan [...]:** páginas que te faltan. Captura solo esas en otro HAR y vuelve al Paso 2.

El total de páginas se toma de la **captura más reciente**: si la búsqueda bajó de 7 a 6 páginas, ya no te pedirá la 7.

---

## 8. Paso 4 — Descargar las fotos (`imagenes`)

El HAR solo trae **miniaturas** (66×50 px), porque es lo que muestra el listado. Para tener las fotos en buen tamaño, el script las pide directamente al servidor.

### Primera vez: prueba con pocas

```
py subastas.py imagenes --limite 5
```

```
5 imágenes por descargar en imagenes/ (Ctrl+C para parar)
  [1/5] 3gWBAkJ35kpL3IL_x  41 KB
  [2/5] 3gWBAkJ35kpL3IL_y  44 KB
  ...
Listo: 5 descargadas, 0 con error.
```

Si funciona, baja todas:

```
py subastas.py imagenes
```

### Opciones útiles

```
py subastas.py imagenes --md "TCM OTHER"     # solo una búsqueda
py subastas.py imagenes --campos xz          # solo delantera + hoja de subasta (ahorra espacio)
py subastas.py imagenes --campos z           # solo las hojas de subasta
py subastas.py imagenes --limite 100         # máximo 100 en esta corrida
py subastas.py imagenes --pausa 2            # 2 segundos entre descargas
py subastas.py imagenes --reintentar         # reintenta las que fallaron antes
```

### Cómo funciona

1. Arma la lista de fotos pendientes. Se salta las que ya tienes en igual o mejor tamaño; las miniaturas del HAR **sí** se reemplazan por la versión grande.
2. Pide cada foto a `https://8.ajes.com/imgs/<token>&w=320` con las mismas cabeceras que usa Chrome (incluido el `Referer` del sitio). El servidor redirige solo a `10–14.ajes.com`.
3. Comprueba que lo recibido sea una imagen. Si el servidor rechaza el tamaño pedido, prueba automáticamente otros (640, 320, sin tamaño, 50) y se queda con el primero que funcione.
4. Guarda la foto en su carpeta y la registra en la base.
5. Espera 1 segundo y sigue con la siguiente.

- **Se puede interrumpir** con `Ctrl+C`. Al volver a ejecutarlo, continúa con las que faltan.
- Las que fallan quedan marcadas con su error. No se reintentan salvo que uses `--reintentar`.
- **Si fallan 5 seguidas, se detiene**: probablemente el servidor está bloqueando y no tiene sentido seguir.

> **Tamaño:** el servidor acepta `w=320` (comprobado). Es el valor por defecto. Cada foto pesa ~30–45 KB, unos **120 KB por lote** con las tres fotos.

> **Recomendación:** descarga las fotos **el mismo día** que capturas el HAR. Los tokens de imagen podrían vencer con el tiempo.

---

## 9. Paso 5 — Exportar a Excel (`export`)

```
py subastas.py export exports\todo.csv
py subastas.py export exports\tcm_other.csv --md "TCM OTHER"
```

El CSV se abre directo en Excel con acentos y katakana correctos (UTF-8 con BOM). Trae una fila por lote con todos sus campos y, además:

| Columna | Contenido |
|---|---|
| `url_foto` | Enlace web de la foto delantera |
| `foto_x` | Ruta local de la foto delantera |
| `foto_y` | Ruta local de la foto trasera |
| `foto_z` | Ruta local de la hoja de subasta |

> La carpeta `exports\` tiene que existir antes de exportar.

---

## 10. Buscar un lote y sus fotos (`ver`)

Busca por **modelo**, **número de lote** o **ID** y muestra los datos con la ruta de cada foto.

```
py subastas.py ver FD35Z7
```

```
2026-10-15  JU Aichi  lote 9060  |  TCM OTHER  FD35Z7  フォ-ク Lift BUCKET
  ID: 3xvQUkDZZwLbELa  |  salida ¥70,000  final ¥901,000  |  vendido
  delantera        imagenes\TCM\OTHER\FD35Z7\2026-10-15_JU_Aichi_9060\3xvQUkDZZwLbELa_x.webp  (320px)
  trasera          imagenes\TCM\OTHER\FD35Z7\2026-10-15_JU_Aichi_9060\3xvQUkDZZwLbELa_y.webp  (320px)
  hoja de subasta  imagenes\TCM\OTHER\FD35Z7\2026-10-15_JU_Aichi_9060\3xvQUkDZZwLbELa_z.webp  (320px)

2026-10-08  JU Aichi  lote 9059  |  ...
```

Cuando el resultado es **un solo lote**, puedes abrirlo:

```
py subastas.py ver 3xvQUkDZZwLbELa --abrir              # abre la carpeta de sus fotos
py subastas.py ver 3xvQUkDZZwLbELa --foto               # abre la foto delantera
py subastas.py ver 9060 --fecha 2026-10-15 --abrir      # por número de lote + fecha
```

> El **número de lote se repite** entre subastas (el 9057 aparece en varias fechas con máquinas distintas). Para quedarte con uno, agrega `--fecha` o usa el ID.

---

## 11. Otros comandos

### `vigilar` — capturar desde el portapapeles

Alternativa al HAR para completar páginas sueltas. Requiere `py -m pip install pyperclip`.

```
py subastas.py vigilar
```

1. Deja la terminal abierta.
2. En DevTools → Network, busca la petición de la página (Ctrl+F dentro de Network → `tpl_poisk`).
3. Clic derecho → **Copy → Copy response**.
4. La terminal muestra la página guardada. Repite con cada página. `Ctrl+C` para salir.

Este modo **no guarda fotos**.

### `reorganizar` — ordenar las fotos en carpetas

Mueve las fotos a la estructura `marca\búsqueda\modelo\lote` y actualiza las rutas en la base. Úsalo si tienes fotos guardadas con una versión anterior del script. Ejecutarlo dos veces no hace nada la segunda vez.

```
py subastas.py reorganizar
```

### `init` — crear la base

```
py subastas.py init
```

No borra nada si la base ya existe; solo agrega lo que falte (tablas nuevas de versiones nuevas del script).

---

## 12. Referencia de comandos y opciones

### Opciones globales

Van **antes** del comando:

```
py subastas.py --db D:\datos\subastas.db status
py subastas.py --carpeta-img D:\fotos imagenes
```

| Opción | Por defecto | Descripción |
|---|---|---|
| `--db RUTA` | `subastas.db` | Archivo de la base de datos |
| `--carpeta-img RUTA` | `imagenes` | Carpeta raíz de las fotos |
| `--img-base URL` | `https://8.ajes.com/imgs/` | URL base del servidor de imágenes |

> Si cambias `--carpeta-img`, usa **siempre la misma** también en `ingest`, o las fotos quedarán repartidas en dos lugares.

### Comandos

| Comando | Opciones | Descripción |
|---|---|---|
| `init` | — | Crea o actualiza la base |
| `ingest RUTAS...` | `--mover` | Procesa `.har`/`.txt` (archivos o carpetas) |
| `vigilar` | `--intervalo SEG` (1) | Procesa lo que copies al portapapeles |
| `status` | — | Páginas capturadas y faltantes por búsqueda |
| `export SALIDA.csv` | `--md "BUSQUEDA"` | Exporta los lotes a CSV |
| `imagenes` | `--md`, `--ancho` (320), `--limite` (0 = todas), `--pausa` (1.0), `--campos` (xyz), `--reintentar` | Descarga fotos |
| `reorganizar` | — | Ordena las fotos en carpetas |
| `ver TEXTO` | `--fecha AAAA-MM-DD`, `--abrir`, `--foto` | Busca un lote y sus fotos |

Ayuda en la terminal: `py subastas.py -h` o `py subastas.py imagenes -h`.

---

## 13. La base de datos

Archivo único: `subastas.db` (SQLite). Se abre con **DB Browser for SQLite**.

### Tabla `capturas`

Una fila por cada página de resultados procesada.

| Columna | Descripción |
|---|---|
| `id` | Número interno |
| `raw_hash` | Huella de la respuesta (evita procesarla dos veces) |
| `origen` | Archivo de donde vino (`tcm.har#3`, `portapapeles`) |
| `md` | Búsqueda (`TCM OTHER`) |
| `pagina`, `paginas_total` | Página y total de páginas |
| `rows_total`, `por_pagina` | Total de resultados y resultados por página |
| `orden` | Orden de la búsqueda (`auct_date#desc`) |
| `lotes_en_pagina`, `lotes_nuevos` | Lotes de la página y cuántos eran nuevos |
| `capturado_en` | Fecha y hora del procesamiento |
| `raw` | **Respuesta original completa**, para volver a procesarla si se descubre algo nuevo |

### Tabla `lotes`

Una fila por lote. Clave: `lot_uid`.

| Columna | Descripción |
|---|---|
| `lot_uid` | ID interno único del lote |
| `md`, `marca` | Búsqueda y marca (`TCM OTHER`, `TCM`) |
| `subasta` | Casa de subasta (`JU Aichi`) |
| `lote_num` | Número de lote (se repite entre fechas) |
| `fecha`, `hora` | Fecha (AAAA-MM-DD) y hora de la subasta |
| `modelo` | Código de modelo / chasis (`FD35Z7`) |
| `transmision` | Transmisión (`MT`, `AT`, `-`) |
| `descripcion` | Grado / descripción (katakana ya normalizado) |
| `extra` | Información adicional (`フメイ` = desconocido) |
| `campo_g` | Año (0 = sin dato) |
| `campo_h` | Horas de uso (0 o 9999 = sin dato) |
| `campo_q` | Probablemente cilindrada (cc) |
| `rating` | Puntaje de inspección (`-`, `*`, `X`…) |
| `precio_salida` | Precio de salida en yenes |
| `precio_final` | Precio final / última puja en yenes (0 = sin pujas o sin dato) |
| `vendido_flag` | Indicador del sitio (no fiable para negociaciones; usar `estado`) |
| `estado_txt` | Estado tal como lo da el sitio |
| `estado` | Estado normalizado: `vendido`, `vendido_negociacion`, `no_vendido`, `pendiente`, `otro` |
| `color` | Color |
| `tok_x`, `tok_y`, `tok_z` | Tokens de las 3 fotos |
| `primera_captura`, `ultima_captura` | Primera y última captura en que apareció |
| `actualizado_en` | Última actualización |
| `raw_json` | Datos originales del lote |

### Tabla `imagenes`

Una fila por foto. Clave: `lot_uid` + `campo`.

| Columna | Descripción |
|---|---|
| `lot_uid` | Lote al que pertenece |
| `campo` | `x` delantera, `y` trasera, `z` hoja de subasta |
| `token` | Token de la imagen en el servidor |
| `ruta` | Ruta del archivo (relativa a la carpeta del proyecto) |
| `bytes` | Tamaño del archivo |
| `ancho` | Tamaño pedido (50 = miniatura del HAR, 320 = descargada) |
| `origen` | `har` o `descarga` |
| `descargado_en` | Fecha y hora |
| `error` | Último error, si falló la descarga |

### Vistas

| Vista | Contenido |
|---|---|
| `resumen_modelo` | Por búsqueda y modelo: lotes, vendidos, precio promedio / mínimo / máximo de vendidos, rango de fechas |
| `posibles_relistados` | Máquinas no vendidas que vuelven a aparecer (misma subasta, modelo, descripción, color y precio de salida, dentro de 35 días). **Limitación:** si bajan el precio de salida, no las detecta. |

---

## 14. Las fotos

- Cada lote tiene **3 fotos**: `x` delantera, `y` trasera, `z` **hoja de subasta** (ficha de inspección en japonés).
- Formato **`.webp`**. Se abren con el visor de fotos de Windows o arrastrándolas a Chrome.
- Nombre del archivo: `<lot_uid>_<campo>.webp`. Puedes buscar un ID en el Explorador de Windows y aparecen sus tres fotos.
- **Fotos repetidas entre lotes son normales:** cuando una máquina no se vende y vuelve a subastarse, el sitio reutiliza las mismas fotos. Cada aparición es un lote distinto con su copia. Gracias a la estructura de carpetas, quedan juntas dentro del mismo modelo, ordenadas por fecha.
- Lotes sin modelo o con modelo desconocido van a la carpeta `SIN_MODELO`.

### Espacio estimado

| Lotes | Con 3 fotos (320 px) | Solo `--campos xz` |
|---|---|---|
| 1,000 | ~120 MB | ~80 MB |
| 10,000 | ~1.2 GB | ~800 MB |

La base de datos en sí es pequeña: ~2 KB por lote.

---

## 15. Consultas SQL útiles

En DB Browser for SQLite → pestaña **Ejecutar SQL**.

**Lotes de un modelo con sus fotos:**
```sql
SELECT l.fecha, l.subasta, l.lote_num, l.modelo, l.precio_final, l.estado,
       i.campo, i.ruta
FROM lotes l
JOIN imagenes i ON i.lot_uid = l.lot_uid
WHERE l.modelo = 'FD35Z7'
ORDER BY l.fecha DESC, i.campo;
```

**Resumen de precios por modelo:**
```sql
SELECT * FROM resumen_modelo
WHERE vendidos > 0
ORDER BY precio_prom_vendido DESC;
```

**Vendidos en el último mes:**
```sql
SELECT fecha, subasta, modelo, descripcion, precio_salida, precio_final
FROM lotes
WHERE estado LIKE 'vendido%' AND fecha >= date('now', '-30 days')
ORDER BY fecha DESC;
```

**Posibles máquinas re-subastadas (mismo modelo, subasta, descripción y color), con su historial de precios.** Es aproximado: si la descripción viene vacía, puede agrupar máquinas distintas del mismo modelo. Confírmalo comparando las fotos.
```sql
SELECT modelo, subasta, descripcion, COUNT(*) AS veces,
       GROUP_CONCAT(fecha || ' ¥' || precio_salida || '→¥' || precio_final || ' ' || estado, '  |  ') AS historial
FROM lotes
GROUP BY modelo, subasta, descripcion, color
HAVING COUNT(*) > 1
ORDER BY veces DESC;
```

**Lotes que aún no tienen foto grande:**
```sql
SELECT l.md, COUNT(*) AS lotes
FROM lotes l
WHERE NOT EXISTS (SELECT 1 FROM imagenes i
                  WHERE i.lot_uid = l.lot_uid AND i.campo = 'x' AND i.ancho >= 320)
GROUP BY l.md;
```

**Fotos que fallaron y por qué:**
```sql
SELECT lot_uid, campo, error FROM imagenes WHERE error IS NOT NULL;
```

**Espacio usado por las fotos:**
```sql
SELECT origen, COUNT(*) AS fotos, ROUND(SUM(bytes) / 1048576.0, 1) AS MB
FROM imagenes WHERE ruta IS NOT NULL GROUP BY origen;
```

---

## 16. Rutina recomendada

### Primera vez por búsqueda

1. Captura **todas las páginas** de la búsqueda en una misma sesión (el orden es por fecha, y si entra una subasta nueva a mitad del recorrido las páginas se desplazan).
2. `ingest` → `status` → completa lo que falte.
3. `imagenes` el mismo día.

### Actualización semanal

1. Captura desde la **página 1** hacia adelante.
2. `py subastas.py ingest inbox --mover`
3. Cuando una página diga `0 nuevos`, ya alcanzaste lo que tenías: puedes parar con esa búsqueda.
4. `py subastas.py imagenes`
5. `py subastas.py export exports\todo.csv`

Los lotes `pendiente` de la semana anterior se actualizan solos con su resultado.

---

## 17. Respaldo

Si borras `subastas.db`, pierdes todos los datos. Para respaldar:

1. Cierra cualquier comando que esté corriendo.
2. Copia **juntos** `subastas.db` y la carpeta `imagenes\` (y `subastas.db-wal` / `-shm` si existen) a OneDrive, Google Drive o un disco externo.

Las rutas de las fotos son relativas: si restauras ambas cosas en la misma estructura, todo sigue funcionando. Si faltan fotos, `py subastas.py imagenes` vuelve a bajar las que no encuentra.

---

## 18. Solución de problemas

| Problema | Causa probable | Solución |
|---|---|---|
| `py` o `python` no se reconoce | Python no está en el PATH | Usa `py`. O reinstala Python marcando *Add Python to PATH* |
| `python` abre la Microsoft Store | Alias de Windows | Desactiva los alias (ver [Requisitos](#1-requisitos)) |
| La base aparece vacía | Ejecutaste desde otra carpeta | `cd` a la carpeta del proyecto, o usa `--db` con la ruta completa |
| `? sin datos de resultados` | HAR guardado antes de cargar, con filtro o desde caché | Repite la captura con *Disable cache* y sin filtro |
| `AVISO: ningún lote trae precio final` | El sitio no mostró precios en esa búsqueda | Revisa en la web si los precios aparecen en pantalla |
| `imágenes ... 0 guardadas` | HAR filtrado o "sanitized" | Guarda el HAR sin filtro y en versión completa |
| `ERROR: no es una imagen (text/html ...)` | El servidor rechazó la petición | El script prueba otros tamaños solo. Si todas fallan: abre una `url_foto` del CSV en Chrome. Si tampoco carga, los tokens vencieron: captura de nuevo y baja las fotos el mismo día |
| `5 errores seguidos ... Me detengo` | Bloqueo, tokens vencidos o sin conexión | Espera un rato, revisa tu conexión y prueba `imagenes --limite 5 --reintentar` |
| `FileExistsError` al mover a `procesados` | Versión vieja del script | Actualiza `subastas.py` (la nueva agrega fecha al nombre) |
| `faltan [7]` pero la búsqueda ya tiene 6 páginas | Versión vieja del script | Actualiza `subastas.py` |
| `archivo no encontrado` en `ver` | Moviste o borraste fotos | `py subastas.py reorganizar` y luego `py subastas.py imagenes` |
| `pip` no se reconoce | `Scripts\` no está en el PATH | Usa `py -m pip install pyperclip` |
| `database is locked` | Otro programa (DB Browser) tiene la base abierta con cambios sin guardar | Guarda o cierra DB Browser y repite |

---

## 19. Seguridad y buenas prácticas

- **Un HAR completo contiene datos de tu sesión** (cookies de acceso). No lo compartas y borra los de `inbox\procesados\` cuando ya no los necesites.
- **No subas a Git** la base, las fotos ni los HAR. Usa este `.gitignore`:
  ```
  subastas.db
  subastas.db-*
  inbox/
  exports/
  imagenes/
  *.har
  ```
- **No navegues demasiado rápido** ni bajes fotos sin pausa: el sitio podría bloquear tu cuenta. El script ya espera 1 segundo entre descargas.
- Revisa que los **términos de uso del sitio** permitan guardar esta información.

---

## 20. Referencia de campos del sitio

Para quien quiera entender o ampliar el parser. Cada página de resultados llega como un bloque `<script>` que llama a `parent.ajx.dataReady(...)`. El campo `tpl_poisk` contiene `var data = { navi: {...}, body: [...] }` con claves sin comillas.

### `navi` (datos de la búsqueda)

| Campo | Ejemplo | Significado |
|---|---|---|
| `md` | `TCM OTHER` | Búsqueda (marca + categoría) |
| `rows` | `119` | Total de resultados |
| `page` | `1` | Página actual |
| `limit_step` | `20` | Resultados por página |
| `n_finish` | `6` | Última página |
| `sort_ord` | `auct_date#desc` | Orden |
| `curr` | `yen` | Moneda |
| `xCnt` | `406` | Contador que sube con cada página vista |

### `body[]` (un objeto por lote)

| Campo | Significado | Columna en la base |
|---|---|---|
| `a` | ID único del lote | `lot_uid` |
| `c` | Número de lote | `lote_num` |
| `d` | Casa de subasta | `subasta` |
| `e` | Fecha (DD.MM.AAAA) | `fecha` |
| `f` | Hora `[HH:MM]` | `hora` |
| `g` | Año | `campo_g` |
| `h` | Horas de uso | `campo_h` |
| `j` | Modelo / chasis | `modelo` |
| `k` | Transmisión | `transmision` |
| `l` | Descripción / grado | `descripcion` |
| `m` | Info adicional | `extra` |
| `q` | Probable cilindrada | `campo_q` |
| `r` | Rating | `rating` |
| `s` | Precio de salida | `precio_salida` |
| `t` | Precio final | `precio_final` |
| `u` | Flag de vendido | `vendido_flag` |
| `v` | Estado en texto | `estado_txt` / `estado` |
| `w` | Color | `color` |
| `x`, `y`, `z` | Tokens de foto delantera, trasera y hoja de subasta | `tok_x`, `tok_y`, `tok_z` |

### URL de las fotos

```
https://8.ajes.com/imgs/<token>&w=320      ← redirige a 10–14.ajes.com
https://8.ajes.com/imgs/<token>&h=50       ← miniatura del listado
```
