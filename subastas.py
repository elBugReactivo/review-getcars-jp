#!/usr/bin/env python3
"""
Ingesta semiautomática de resultados de subastas (respuestas parent.ajx.dataReady)
a una base SQLite.

Uso:
  python subastas.py init                      # crea la BD
  python subastas.py ingest inbox/ --mover     # procesa .txt / .har de una carpeta
  python subastas.py vigilar                   # ingiere todo lo que copies al portapapeles
  python subastas.py status                    # páginas capturadas / faltantes por modelo
  python subastas.py export lotes.csv [--md "TCM OTHER"]
  python subastas.py imagenes [--md "TCM OTHER"] [--ancho 320] [--limite 200]
                                               # descarga las fotos de cada lote
  python subastas.py reorganizar               # ordena las fotos en marca/búsqueda/modelo/lote
  python subastas.py ver FD35Z7                # busca por ID, número de lote o modelo
  python subastas.py ver 9060 --fecha 2026-10-15 --abrir   # abre la carpeta de sus fotos
"""
import argparse
import base64
import csv
import hashlib
import html
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import unicodedata
from datetime import date, datetime
from pathlib import Path

DB_DEFAULT = "subastas.db"
IMG_BASE_DEFAULT = "https://8.ajes.com/imgs/"    # redirige solo a 10-14.ajes.com; URL = base + token + "&w=" + ancho
IMG_DIR_DEFAULT = "imagenes"
REFERER = "https://auc.getcars.jp/"

SCHEMA = """
CREATE TABLE IF NOT EXISTS capturas (
  id               INTEGER PRIMARY KEY,
  raw_hash         TEXT UNIQUE NOT NULL,       -- evita procesar 2 veces la misma respuesta
  origen           TEXT,                       -- archivo / portapapeles
  md               TEXT,                       -- marca+modelo de la búsqueda (navi.md)
  pagina           INTEGER,
  paginas_total    INTEGER,
  rows_total       INTEGER,
  por_pagina       INTEGER,
  orden            TEXT,
  filtro_desde     TEXT,
  filtro_hasta     TEXT,
  lotes_en_pagina  INTEGER,
  lotes_nuevos     INTEGER,
  capturado_en     TEXT NOT NULL,
  raw              TEXT NOT NULL               -- respuesta original para re-parsear si hace falta
);

CREATE TABLE IF NOT EXISTS lotes (
  lot_uid          TEXT PRIMARY KEY,           -- campo a
  md               TEXT,
  marca            TEXT,
  subasta          TEXT,                       -- d
  lote_num         TEXT,                       -- c
  fecha            DATE,                       -- e (ISO)
  hora             TEXT,                       -- f
  modelo           TEXT,                       -- j
  transmision      TEXT,                       -- k
  descripcion      TEXT,                       -- l
  extra            TEXT,                       -- m
  campo_q          TEXT,                       -- q (cilindrada cc, probable)
  campo_g          TEXT,                       -- g (año; 0 = sin dato)
  campo_h          TEXT,                       -- h (horas de uso; 0/9999 = sin dato)
  rating           TEXT,                       -- r
  precio_salida    INTEGER,                    -- s
  precio_final     INTEGER,                    -- t
  vendido_flag     INTEGER,                    -- u
  estado_txt       TEXT,                       -- v limpio
  estado           TEXT,                       -- vendido / vendido_negociacion / no_vendido / otro
  color            TEXT,                       -- w
  tok_x TEXT, tok_y TEXT, tok_z TEXT,
  raw_json         TEXT,
  primera_captura  INTEGER REFERENCES capturas(id),
  ultima_captura   INTEGER REFERENCES capturas(id),
  actualizado_en   TEXT
);

CREATE TABLE IF NOT EXISTS imagenes (
  lot_uid          TEXT NOT NULL REFERENCES lotes(lot_uid),
  campo            TEXT NOT NULL,              -- x / y / z (token del lote)
  token            TEXT NOT NULL,
  ruta             TEXT,                       -- archivo local (relativo a la carpeta del script)
  bytes            INTEGER,
  ancho            INTEGER,                    -- parámetro w usado (0 = original)
  origen           TEXT,                       -- har / descarga
  descargado_en    TEXT,
  error            TEXT,                       -- último error si falló
  PRIMARY KEY (lot_uid, campo)
);
CREATE INDEX IF NOT EXISTS ix_img_token ON imagenes(token);

CREATE INDEX IF NOT EXISTS ix_lotes_md     ON lotes(md);
CREATE INDEX IF NOT EXISTS ix_lotes_fecha  ON lotes(fecha);
CREATE INDEX IF NOT EXISTS ix_lotes_modelo ON lotes(modelo);

CREATE VIEW IF NOT EXISTS resumen_modelo AS
SELECT md, modelo,
       COUNT(*)                                            AS lotes,
       SUM(estado LIKE 'vendido%')                         AS vendidos,
       ROUND(AVG(CASE WHEN estado LIKE 'vendido%' THEN precio_final END)) AS precio_prom_vendido,
       MIN(CASE WHEN estado LIKE 'vendido%' THEN precio_final END)        AS precio_min_vendido,
       MAX(CASE WHEN estado LIKE 'vendido%' THEN precio_final END)        AS precio_max_vendido,
       MIN(fecha) AS desde, MAX(fecha) AS hasta
FROM lotes GROUP BY md, modelo;

-- Misma máquina probablemente re-subastada (heurística)
CREATE VIEW IF NOT EXISTS posibles_relistados AS
SELECT a.md, a.subasta, a.modelo, a.descripcion, a.color, a.precio_salida,
       a.fecha AS fecha_1, a.precio_final AS final_1, a.estado AS estado_1,
       b.fecha AS fecha_2, b.precio_final AS final_2, b.estado AS estado_2
FROM lotes a JOIN lotes b
  ON a.md = b.md AND a.subasta = b.subasta AND a.modelo = b.modelo
 AND a.descripcion = b.descripcion AND a.color = b.color
 AND a.precio_salida = b.precio_salida
 AND a.lot_uid <> b.lot_uid AND a.fecha < b.fecha
 AND julianday(b.fecha) - julianday(a.fecha) <= 35
WHERE a.estado = 'no_vendido';
"""

# --------------------------------------------------------------------------- parsing

TPL_RE = re.compile(r"""['"]tpl_poisk['"]\s*:\s*'((?:[^'\\]|\\.)*)'""", re.S)
IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*")


def js_unescape(s: str) -> str:
    """Decodifica un literal de string JS ('...')."""
    def rep(m):
        g = m.group(1)
        if g[0] == "u" and len(g) == 5:
            return chr(int(g[1:], 16))
        return {"n": "\n", "t": "\t", "r": "\r"}.get(g, g)
    return re.sub(r"\\(u[0-9a-fA-F]{4}|.)", rep, s, flags=re.S)


def js_obj_to_json(s: str) -> str:
    """Pone comillas a las claves sin comillas, sin tocar el contenido de los strings."""
    out, i, n, in_str, last = [], 0, len(s), False, " "
    while i < n:
        c = s[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(s[i + 1]); i += 2; continue
            if c == '"':
                in_str = False
            i += 1; continue
        if c == '"':
            in_str = True; out.append(c); last = c; i += 1; continue
        if last in "{,":
            m = IDENT_RE.match(s, i)
            if m:
                j = m.end()
                while j < n and s[j].isspace():
                    j += 1
                if j < n and s[j] == ":":
                    out.append('"%s"' % m.group(0)); i = m.end(); last = '"'; continue
        out.append(c)
        if not c.isspace():
            last = c
        i += 1
    return "".join(out)


def extraer_tpl(texto: str):
    """Devuelve la lista de strings tpl_poisk (ya decodificados) que haya en un texto."""
    return [js_unescape(m.group(1)) for m in TPL_RE.finditer(texto)]


def parse_tpl(tpl: str) -> dict:
    m = re.match(r"\s*var\s+data\s*=\s*(.*?);?\s*$", tpl, re.S)
    if not m:
        raise ValueError("formato inesperado en tpl_poisk")
    return json.loads(js_obj_to_json(m.group(1)), strict=False)


def limpiar(v) -> str:
    if v is None:
        return ""
    v = re.sub(r"<[^>]+>", "", str(v))
    v = html.unescape(html.unescape(v))
    return unicodedata.normalize("NFKC", v).strip()


def a_int(v):
    try:
        return int(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def a_fecha(v):
    try:
        return datetime.strptime(v.strip(), "%d.%m.%Y").date().isoformat()
    except (ValueError, AttributeError):
        return None


def normalizar_estado(txt: str) -> str:
    t = txt.lower()
    if not t.strip():
        return "pendiente"                      # subasta reciente, aún sin resultado
    if "no se vend" in t or "not sold" in t or "unsold" in t:
        return "no_vendido"
    if "negoci" in t:
        return "vendido_negociacion"
    if "vendido" in t or "sold" in t:
        return "vendido"
    return "otro"


def item_a_fila(it: dict, md: str) -> dict:
    estado_txt = limpiar(it.get("v"))
    return {
        "lot_uid": it["a"],
        "md": md,
        "marca": md.split()[0] if md else None,
        "subasta": limpiar(it.get("d")),
        "lote_num": limpiar(it.get("c")),
        "fecha": a_fecha(it.get("e", "")),
        "hora": limpiar(it.get("f")).strip("[]"),
        "modelo": limpiar(it.get("j")),
        "transmision": limpiar(it.get("k")),
        "descripcion": limpiar(it.get("l")),
        "extra": limpiar(it.get("m")),
        "campo_q": limpiar(it.get("q")),
        "campo_g": limpiar(it.get("g")),
        "campo_h": limpiar(it.get("h")),
        "rating": limpiar(it.get("r")),
        "precio_salida": a_int(it.get("s")),
        "precio_final": a_int(it.get("t")),
        "vendido_flag": a_int(it.get("u")),
        "estado_txt": estado_txt,
        "estado": normalizar_estado(estado_txt),
        "color": limpiar(it.get("w")),
        "tok_x": it.get("x"), "tok_y": it.get("y"), "tok_z": it.get("z"),
        "raw_json": json.dumps(it, ensure_ascii=False),
    }

# --------------------------------------------------------------------------- BD

def conectar(path):
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def ingerir_tpl(con, tpl: str, origen: str) -> str:
    h = hashlib.sha256(tpl.encode("utf-8")).hexdigest()
    if con.execute("SELECT 1 FROM capturas WHERE raw_hash=?", (h,)).fetchone():
        return f"  = ya procesada ({origen})"

    data = parse_tpl(tpl)
    navi, body = data.get("navi", {}), data.get("body", [])
    md = limpiar(navi.get("md"))
    ahora = datetime.now().isoformat(timespec="seconds")

    cur = con.execute(
        """INSERT INTO capturas (raw_hash, origen, md, pagina, paginas_total, rows_total,
             por_pagina, orden, filtro_desde, filtro_hasta, lotes_en_pagina, capturado_en, raw)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (h, origen, md, a_int(navi.get("page")), a_int(navi.get("n_finish")),
         a_int(navi.get("rows")), a_int(navi.get("limit_step")), navi.get("sort_ord"),
         navi.get("stDt1"), navi.get("stDt2"), len(body), ahora, tpl))
    cap_id = cur.lastrowid

    nuevos = 0
    for it in body:
        if not it.get("a"):
            continue
        f = item_a_fila(it, md)
        existe = con.execute("SELECT 1 FROM lotes WHERE lot_uid=?", (f["lot_uid"],)).fetchone()
        nuevos += 0 if existe else 1
        f.update(primera_captura=cap_id, ultima_captura=cap_id, actualizado_en=ahora)
        cols = list(f)
        # Un precio_final 0 nunca pisa un precio real ya guardado
        upd = ", ".join(
            "precio_final=CASE WHEN excluded.precio_final>0 THEN excluded.precio_final "
            "ELSE lotes.precio_final END" if c == "precio_final" else f"{c}=excluded.{c}"
            for c in cols if c not in ("lot_uid", "primera_captura"))
        con.execute(
            f"INSERT INTO lotes ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
            f"ON CONFLICT(lot_uid) DO UPDATE SET {upd}",
            [f[c] for c in cols])

    con.execute("UPDATE capturas SET lotes_nuevos=? WHERE id=?", (nuevos, cap_id))
    con.commit()
    aviso = ""
    if body and all(a_int(it.get("t")) in (0, None) for it in body):
        aviso = "  <-- AVISO: ningún lote trae precio final (t=0)"
    return (f"  + {md} | pág {navi.get('page')}/{navi.get('n_finish')} | "
            f"{len(body)} lotes, {nuevos} nuevos ({origen}){aviso}")

# --------------------------------------------------------------------------- entradas

def textos_de_archivo(p: Path):
    """Devuelve (origen, texto) para .har o cualquier archivo de texto."""
    if p.suffix.lower() == ".har":
        har = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        for i, e in enumerate(har.get("log", {}).get("entries", [])):
            c = e.get("response", {}).get("content", {})
            t = c.get("text") or ""
            if c.get("encoding") == "base64":
                t = base64.b64decode(t).decode("utf-8", errors="replace")
            if "tpl_poisk" in t:
                yield f"{p.name}#{i}", t
    else:
        yield p.name, p.read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- imágenes

TOKEN_URL_RE = re.compile(r"/imgs/([A-Za-z0-9]+)(?:.*?[?&][wh]=(\d+))?")
EXT = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
       "image/webp": ".webp", "image/gif": ".gif"}


def slug(t: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", t or "sin_md").strip("_") or "sin_md"


def buscar_token(con, token):
    """Devuelve (lot_uid, campo, md) del lote al que pertenece un token de imagen."""
    for campo in ("x", "y", "z"):
        r = con.execute(f"SELECT lot_uid, md FROM lotes WHERE tok_{campo}=?", (token,)).fetchone()
        if r:
            return r[0], campo, r[1]
    return None


def carpeta_lote(con, carpeta: Path, lot_uid) -> Path:
    """imagenes/<MARCA>/<BUSQUEDA>/<MODELO>/<fecha>_<subasta>_<lote>/"""
    r = con.execute("SELECT md, modelo, fecha, subasta, lote_num FROM lotes WHERE lot_uid=?",
                    (lot_uid,)).fetchone()
    md, modelo, fecha, subasta, lote_num = r if r else ("", "", "", "", "")
    partes = (md or "SIN_MARCA").split(None, 1)
    marca = slug(partes[0])
    busqueda = slug(partes[1]) if len(partes) > 1 else "GENERAL"
    mod = re.sub(r"[^A-Za-z0-9-]+", "_", modelo or "").strip("_") or "SIN_MODELO"
    lote_dir = "_".join(x for x in (fecha or "sin_fecha", slug(subasta), str(lote_num or lot_uid)) if x)
    return carpeta / marca / busqueda / mod / lote_dir


def guardar_imagen(con, carpeta: Path, lot_uid, campo, md, token, datos: bytes,
                   mime: str, ancho, origen):
    previa = con.execute("SELECT ruta, ancho FROM imagenes WHERE lot_uid=? AND campo=? "
                         "AND ruta IS NOT NULL", (lot_uid, campo)).fetchone()
    # no reemplazar una imagen más grande por una miniatura
    if previa and Path(previa[0]).exists() and (previa[1] == 0 or (ancho and previa[1] >= ancho)):
        return False
    ext = EXT.get((mime or "").split(";")[0].strip().lower(), ".jpg")
    destino = carpeta_lote(con, carpeta, lot_uid) / f"{lot_uid}_{campo}{ext}"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(datos)
    if previa and previa[0] and Path(previa[0]) != destino and Path(previa[0]).exists():
        Path(previa[0]).unlink()                # borra la miniatura vieja si quedó en otra ruta
    con.execute(
        """INSERT INTO imagenes (lot_uid, campo, token, ruta, bytes, ancho, origen, descargado_en, error)
           VALUES (?,?,?,?,?,?,?,?,NULL)
           ON CONFLICT(lot_uid, campo) DO UPDATE SET token=excluded.token, ruta=excluded.ruta,
             bytes=excluded.bytes, ancho=excluded.ancho, origen=excluded.origen,
             descargado_en=excluded.descargado_en, error=NULL""",
        (lot_uid, campo, token, destino.as_posix(), len(datos), ancho or 0, origen,
         datetime.now().isoformat(timespec="seconds")))
    return True


def imagenes_de_har(con, p: Path, carpeta: Path):
    """Guarda las imágenes que vengan embebidas en un HAR y las enlaza a su lote."""
    har = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    guardadas = sin_lote = sin_contenido = sin_foto = 0
    for e in har.get("log", {}).get("entries", []):
        url = e.get("request", {}).get("url", "")
        m = TOKEN_URL_RE.search(url)
        if not m:
            continue
        if e.get("response", {}).get("status") in (301, 302, 303, 307, 308):
            continue                                # redirección a 10-14.ajes.com; la imagen viene en otra entrada
        c = e.get("response", {}).get("content", {})
        if not c.get("text"):
            sin_contenido += 1
            continue
        lote = buscar_token(con, m.group(1))
        if not lote:
            sin_lote += 1
            continue
        datos = (base64.b64decode(c["text"]) if c.get("encoding") == "base64"
                 else c["text"].encode("latin-1", errors="replace"))
        if len(datos) < 200:                    # GIF de 43 bytes = marcador "sin foto" del sitio
            sin_foto += 1
            continue
        if guardar_imagen(con, carpeta, lote[0], lote[1], lote[2], m.group(1), datos,
                          c.get("mimeType"), a_int(m.group(2)) or 0, "har"):
            guardadas += 1
    con.commit()
    if guardadas or sin_lote or sin_contenido or sin_foto:
        print(f"  imágenes en {p.name}: {guardadas} guardadas, {sin_foto} sin foto en el sitio, "
              f"{sin_lote} sin lote conocido, {sin_contenido} sin contenido en el HAR")


def cmd_imagenes(con, md, ancho, limite, pausa, campos, carpeta: Path, base, reintentar):
    q = "SELECT lot_uid, md, tok_x, tok_y, tok_z FROM lotes"
    args = []
    if md:
        q += " WHERE md=?"; args.append(md)
    q += " ORDER BY fecha DESC"
    pendientes = []
    for uid, lmd, tx, ty, tz in con.execute(q, args).fetchall():
        for campo, tok in (("x", tx), ("y", ty), ("z", tz)):
            if campo not in campos or not tok:
                continue
            r = con.execute("SELECT ruta, ancho, error FROM imagenes WHERE lot_uid=? AND campo=?",
                            (uid, campo)).fetchone()
            if r and r[0] and Path(r[0]).exists() and (r[1] == 0 or (ancho and r[1] >= ancho)):
                continue                      # ya la tengo con igual o mejor tamaño
            if r and r[2] and not reintentar:
                continue                      # falló antes; usa --reintentar
            pendientes.append((uid, lmd, campo, tok))
    if limite:
        pendientes = pendientes[:limite]
    print(f"{len(pendientes)} imágenes por descargar en {carpeta}/ (Ctrl+C para parar)")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": "es-419,es;q=0.9",
        "Referer": REFERER,
        "Sec-Fetch-Dest": "image", "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Site": "cross-site",
    }

    def bajar(tok, sufijo):
        req = urllib.request.Request(base + tok + sufijo, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            mime = resp.headers.get("Content-Type", "")
            datos = resp.read()
        if not mime.startswith("image") or len(datos) < 200:
            texto = datos[:80].decode("utf-8", errors="replace").strip()
            raise ValueError(f"no es una imagen ({mime}, {len(datos)} bytes): {texto!r}")
        return datos, mime

    # tamaños a probar: el pedido y, si el servidor lo rechaza, otros conocidos
    variantes = [(f"&w={ancho}" if ancho else "", ancho)]
    for suf, val in (("&w=640", 640), ("&w=320", 320), ("", 0), ("&h=50", 50)):
        if (suf, val) not in variantes:
            variantes.append((suf, val))
    elegida = None                            # la variante que funcionó, se fija tras el primer éxito

    ok = fallos = seguidos = 0
    try:
        for i, (uid, lmd, campo, tok) in enumerate(pendientes, 1):
            probar = [elegida] if elegida else variantes
            errores = []
            for suf, val in probar:
                try:
                    datos, mime = bajar(tok, suf)
                except (urllib.error.URLError, ValueError, TimeoutError, OSError) as ex:
                    errores.append(f"[{suf or 'sin tamaño'}] {ex}")
                    continue
                if not elegida:
                    elegida = (suf, val)
                    if (suf, val) != variantes[0]:
                        print(f"  (el servidor rechazó el tamaño pedido; uso '{suf or 'sin tamaño'}')")
                guardar_imagen(con, carpeta, uid, campo, lmd, tok, datos, mime, val, "descarga")
                ok += 1; seguidos = 0
                print(f"  [{i}/{len(pendientes)}] {uid}_{campo}  {len(datos)//1024} KB")
                break
            else:
                fallos += 1; seguidos += 1
                msg = " | ".join(errores)
                con.execute(
                    """INSERT INTO imagenes (lot_uid, campo, token, ancho, origen, error)
                       VALUES (?,?,?,?,?,?)
                       ON CONFLICT(lot_uid, campo) DO UPDATE SET error=excluded.error""",
                    (uid, campo, tok, ancho, "descarga", msg[:500]))
                print(f"  [{i}/{len(pendientes)}] {uid}_{campo}  ERROR: {msg}")
                if seguidos >= 5:
                    print("\n5 errores seguidos: el servidor está rechazando las descargas. Me detengo.\n"
                          "Prueba abrir una 'url_foto' del CSV en Chrome. Si tampoco carga, los tokens\n"
                          "probablemente vencen: baja las fotos el mismo día que capturas el HAR.")
                    break
            con.commit()
            time.sleep(pausa)
    except KeyboardInterrupt:
        print("\nInterrumpido.")
    con.commit()
    print(f"Listo: {ok} descargadas, {fallos} con error.")


def abrir(ruta: Path):
    """Abre un archivo o carpeta con el programa por defecto del sistema."""
    import os, subprocess
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(ruta))              # noqa: S606
        elif sys.platform == "darwin":
            subprocess.run(["open", str(ruta)], check=False)
        else:
            subprocess.run(["xdg-open", str(ruta)], check=False)
    except OSError as ex:
        print(f"  no pude abrir {ruta}: {ex}")


def cmd_ver(con, texto, fecha, abrir_carpeta, abrir_foto):
    """Busca lotes por ID, número de lote o modelo y muestra/abre sus fotos."""
    q = """SELECT lot_uid, md, fecha, subasta, lote_num, modelo, descripcion,
                  precio_salida, precio_final, estado
           FROM lotes WHERE (lot_uid = ? OR lote_num = ? OR modelo = ?)"""
    args = [texto, texto, texto]
    if fecha:
        q += " AND fecha = ?"; args.append(fecha)
    q += " ORDER BY fecha DESC LIMIT 50"
    filas = con.execute(q, args).fetchall()
    if not filas:
        print(f"No encontré lotes con ID, número de lote o modelo = '{texto}'.")
        return
    nombres = {"x": "delantera", "y": "trasera", "z": "hoja de subasta"}
    for uid, md, f, sub_, num, mod, desc, ps, pf, est in filas:
        print(f"\n{f}  {sub_}  lote {num}  |  {md}  {mod}  {desc}")
        print(f"  ID: {uid}  |  salida ¥{ps or 0:,}  final ¥{pf or 0:,}  |  {est}")
        fotos = con.execute("SELECT campo, ruta, ancho FROM imagenes WHERE lot_uid=? "
                            "AND ruta IS NOT NULL ORDER BY campo", (uid,)).fetchall()
        if not fotos:
            print("  (sin fotos guardadas; bájalas con: py subastas.py imagenes)")
        for campo, ruta, ancho in fotos:
            existe = "" if Path(ruta).exists() else "  <-- archivo no encontrado"
            print(f"  {nombres.get(campo, campo):<16} {Path(ruta)}  ({ancho}px){existe}")
        if len(filas) == 1 and fotos:
            if abrir_carpeta:
                abrir(Path(fotos[0][1]).parent)
            if abrir_foto:
                abrir(Path(fotos[0][1]))
    if len(filas) > 1:
        print(f"\n{len(filas)} lotes encontrados. Para abrir uno usa su ID: "
              f"py subastas.py ver <ID> --abrir")


def cmd_reorganizar(con, carpeta: Path):
    """Mueve las fotos ya guardadas a la estructura marca/búsqueda/modelo/lote."""
    movidas = faltan = iguales = 0
    for uid, campo, ruta in con.execute(
            "SELECT lot_uid, campo, ruta FROM imagenes WHERE ruta IS NOT NULL").fetchall():
        origen = Path(ruta)
        destino = carpeta_lote(con, carpeta, uid) / origen.name
        if origen == destino:
            iguales += 1; continue
        if not origen.exists():
            if destino.exists():
                con.execute("UPDATE imagenes SET ruta=? WHERE lot_uid=? AND campo=?",
                            (destino.as_posix(), uid, campo)); movidas += 1
            else:
                faltan += 1
            continue
        destino.parent.mkdir(parents=True, exist_ok=True)
        if destino.exists():
            destino.unlink()
        origen.rename(destino)
        con.execute("UPDATE imagenes SET ruta=? WHERE lot_uid=? AND campo=?",
                    (destino.as_posix(), uid, campo))
        movidas += 1
    con.commit()
    # borra las carpetas viejas que quedaron vacías
    for d in sorted((x for x in carpeta.rglob("*") if x.is_dir()), key=lambda x: -len(x.parts)):
        try:
            d.rmdir()
        except OSError:
            pass
    print(f"Reorganizadas: {movidas} movidas, {iguales} ya estaban bien, {faltan} no encontradas.")
    if faltan:
        print("Las no encontradas se vuelven a bajar con:  py subastas.py imagenes --ancho 320")


def cmd_ingest(con, rutas, mover, carpeta_img: Path):
    archivos = []
    for r in map(Path, rutas):
        archivos += sorted(x for x in r.iterdir() if x.is_file()) if r.is_dir() else [r]
    for p in archivos:
        ok = True
        for origen, texto in textos_de_archivo(p):
            tpls = extraer_tpl(texto)
            if not tpls:
                print(f"  ? sin datos de resultados: {origen}")
            for k, tpl in enumerate(tpls):
                try:
                    print(ingerir_tpl(con, tpl, origen if len(tpls) == 1 else f"{origen}[{k}]"))
                except Exception as ex:  # noqa: BLE001
                    ok = False
                    print(f"  ! error en {origen}: {ex}")
        if p.suffix.lower() == ".har":
            try:
                imagenes_de_har(con, p, carpeta_img)
            except Exception as ex:  # noqa: BLE001
                print(f"  ! error leyendo imágenes de {p.name}: {ex}")
        if mover and ok:
            dest = p.parent / "procesados"
            dest.mkdir(exist_ok=True)
            destino = dest / p.name
            if destino.exists():                # Windows no deja sobrescribir: agrega fecha y hora
                destino = dest / f"{p.stem}_{datetime.now():%Y%m%d_%H%M%S}{p.suffix}"
            try:
                p.rename(destino)
            except OSError as ex:
                print(f"  ! no pude mover {p.name} a procesados ({ex}); los datos sí se guardaron")


def cmd_vigilar(con, intervalo):
    try:
        import pyperclip
    except ImportError:
        sys.exit("Instala pyperclip:  pip install pyperclip")
    print("Vigilando el portapapeles. En DevTools: clic derecho en la petición → Copy → Copy response.")
    print("Ctrl+C para salir.\n")
    ultimo = None
    try:
        while True:
            try:
                txt = pyperclip.paste() or ""
            except Exception:  # noqa: BLE001
                txt = ""
            if txt != ultimo and "tpl_poisk" in txt:
                ultimo = txt
                for tpl in extraer_tpl(txt):
                    try:
                        print(ingerir_tpl(con, tpl, "portapapeles"))
                    except Exception as ex:  # noqa: BLE001
                        print(f"  ! error: {ex}")
            time.sleep(intervalo)
    except KeyboardInterrupt:
        print("\nListo.")


def cmd_status(con):
    filas = con.execute(
        """SELECT c.md, c.orden,
                  (SELECT paginas_total FROM capturas x WHERE x.md=c.md AND x.orden IS c.orden
                   ORDER BY x.id DESC LIMIT 1),
                  (SELECT rows_total FROM capturas x WHERE x.md=c.md AND x.orden IS c.orden
                   ORDER BY x.id DESC LIMIT 1),
                  GROUP_CONCAT(DISTINCT c.pagina), MAX(c.capturado_en)
           FROM capturas c GROUP BY c.md, c.orden ORDER BY c.md""").fetchall()
    if not filas:
        print("Sin capturas todavía.")
        return
    for md, orden, tot, rows, pags, ult in filas:
        tengo = {int(x) for x in (pags or "").split(",") if x}
        tengo = {p for p in tengo if p <= (tot or 0)}  # usa los totales de la captura más reciente
        falta = [p for p in range(1, (tot or 0) + 1) if p not in tengo]
        n_lotes = con.execute("SELECT COUNT(*) FROM lotes WHERE md=?", (md,)).fetchone()[0]
        estado = "COMPLETO" if not falta else f"faltan {falta}"
        print(f"{md:<28} {len(tengo)}/{tot} págs | {n_lotes}/{rows} lotes | {estado} | últ: {ult}")


def cmd_export(con, salida, md, base):
    q = """SELECT l.*,
              CASE WHEN l.tok_x <> '' THEN ? || l.tok_x || '&w=640' END AS url_foto,
              (SELECT ruta FROM imagenes i WHERE i.lot_uid=l.lot_uid AND i.campo='x'
                 AND i.ruta IS NOT NULL) AS foto_x,
              (SELECT ruta FROM imagenes i WHERE i.lot_uid=l.lot_uid AND i.campo='y'
                 AND i.ruta IS NOT NULL) AS foto_y,
              (SELECT ruta FROM imagenes i WHERE i.lot_uid=l.lot_uid AND i.campo='z'
                 AND i.ruta IS NOT NULL) AS foto_z
           FROM lotes l"""
    args = [base]
    if md:
        q += " WHERE l.md=?"; args.append(md)
    q += " ORDER BY l.md, l.fecha DESC, l.subasta, l.lote_num"
    cur = con.execute(q, args)
    cols = [d[0] for d in cur.description if d[0] != "raw_json"]
    idx = [i for i, d in enumerate(cur.description) if d[0] != "raw_json"]
    with open(salida, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        n = 0
        for r in cur:
            w.writerow([r[i] for i in idx]); n += 1
    print(f"{n} lotes exportados a {salida}")


def main():
    try:
        sys.stdout.reconfigure(errors="replace")   # evita errores con ¥ o katakana en consolas viejas
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DB_DEFAULT)
    ap.add_argument("--carpeta-img", default=IMG_DIR_DEFAULT, help="carpeta donde guardar las fotos")
    ap.add_argument("--img-base", default=IMG_BASE_DEFAULT, help="URL base de las imágenes")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    pi = sub.add_parser("ingest"); pi.add_argument("rutas", nargs="+"); pi.add_argument("--mover", action="store_true")
    pv = sub.add_parser("vigilar"); pv.add_argument("--intervalo", type=float, default=1.0)
    sub.add_parser("status")
    pe = sub.add_parser("export"); pe.add_argument("salida"); pe.add_argument("--md")
    sub.add_parser("reorganizar", help="mueve las fotos a carpetas marca/búsqueda/modelo/lote")
    pver = sub.add_parser("ver", help="busca un lote y muestra/abre sus fotos")
    pver.add_argument("texto", help="ID del lote, número de lote o modelo")
    pver.add_argument("--fecha", help="AAAA-MM-DD, para filtrar")
    pver.add_argument("--abrir", action="store_true", help="abre la carpeta de las fotos")
    pver.add_argument("--foto", action="store_true", help="abre la foto delantera")
    pim = sub.add_parser("imagenes", help="descarga las fotos de los lotes")
    pim.add_argument("--md", help='solo una búsqueda, ej. "TCM OTHER"')
    pim.add_argument("--ancho", type=int, default=320, help="ancho en px (0 = tamaño original)")
    pim.add_argument("--limite", type=int, default=0, help="máximo de imágenes en esta corrida")
    pim.add_argument("--pausa", type=float, default=1.0, help="segundos entre descargas")
    pim.add_argument("--campos", default="xyz", help="qué tokens bajar: x, y, z (ej. xy)")
    pim.add_argument("--reintentar", action="store_true", help="reintenta las que fallaron")
    a = ap.parse_args()

    con = conectar(a.db)
    if a.cmd == "init":
        print(f"BD lista: {a.db}")
    elif a.cmd == "ingest":
        cmd_ingest(con, a.rutas, a.mover, Path(a.carpeta_img))
    elif a.cmd == "vigilar":
        cmd_vigilar(con, a.intervalo)
    elif a.cmd == "status":
        cmd_status(con)
    elif a.cmd == "export":
        cmd_export(con, a.salida, a.md, a.img_base)
    elif a.cmd == "ver":
        cmd_ver(con, a.texto, a.fecha, a.abrir, a.foto)
    elif a.cmd == "reorganizar":
        cmd_reorganizar(con, Path(a.carpeta_img))
    elif a.cmd == "imagenes":
        cmd_imagenes(con, a.md, a.ancho, a.limite, a.pausa, set(a.campos), Path(a.carpeta_img),
                     a.img_base, a.reintentar)


if __name__ == "__main__":
    main()
