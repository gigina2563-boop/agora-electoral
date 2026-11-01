"""Utilidades de texto, formato y lectura de valores."""

from __future__ import annotations

import html
import os
import re
import unicodedata
from typing import Any, Optional

import pandas as pd

from .config import BASE_DIR


def texto(valor: Any) -> str:
    """Convierte cualquier valor en texto limpio y sin 'nan'."""
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass
    return str(valor).strip()


def escapar_html(valor: Any) -> str:
    return html.escape(texto(valor), quote=True)


def escapar_md(valor: Any) -> str:
    """Escapa los caracteres markdown más conflictivos de nombres."""
    return re.sub(r"([\\`*_{}\[\]#])", r"\\\1", texto(valor))


def fmt_pct(valor: Any, decimales: int = 2) -> str:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return "s/d"
    if pd.isna(numero):
        return "s/d"
    return f"{numero:.{decimales}f}".replace(".", ",") + "%"


def fmt_pts(valor: Any, decimales: int = 2) -> str:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return "s/d"
    if pd.isna(numero):
        return "s/d"
    return f"{numero:+.{decimales}f}".replace(".", ",") + " pts"


def fmt_numero(valor: Any, decimales: int = 2) -> str:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return "s/d"
    if pd.isna(numero):
        return "s/d"
    return f"{numero:.{decimales}f}".replace(".", ",")


def fmt_entero(valor: Any) -> str:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return "s/d"
    if pd.isna(numero):
        return "s/d"
    return f"{int(round(numero)):,}".replace(",", ".")


def normalizar_nombre(valor: object) -> str:
    """Normaliza encabezados: 'Año' → 'ano', '% 2023' → 'pct_2023'."""
    cadena = unicodedata.normalize("NFKD", str(valor))
    cadena = "".join(c for c in cadena if not unicodedata.combining(c))
    cadena = cadena.strip().lower().replace("%", " pct ")
    return re.sub(r"[^a-z0-9]+", "_", cadena).strip("_")


def clave_fuerza(valor: Any) -> str:
    """Clave tolerante a tildes, mayúsculas, guiones y espacios."""
    cadena = unicodedata.normalize("NFKD", texto(valor))
    cadena = "".join(c for c in cadena if not unicodedata.combining(c))
    cadena = cadena.casefold()
    cadena = re.sub(r"[^a-z0-9]+", " ", cadena)
    return re.sub(r"\s+", " ", cadena).strip()


def ruta_segura(ruta: str) -> str:
    """Resuelve una ruta relativa sin permitir salir del proyecto."""
    base = os.path.abspath(BASE_DIR)
    absoluta = os.path.abspath(
        ruta if os.path.isabs(ruta) else os.path.join(base, ruta)
    )
    try:
        dentro = os.path.commonpath([base, absoluta]) == base
    except ValueError:
        dentro = False
    if not dentro:
        raise ValueError("La ruta indicada está fuera del directorio del proyecto.")
    if not os.path.isfile(absoluta):
        raise FileNotFoundError(f"No existe el archivo: {os.path.basename(ruta)}")
    return absoluta


def nombre_fuente(fuente: object) -> str:
    if isinstance(fuente, (str, os.PathLike)):
        return os.path.basename(os.fspath(fuente))
    return texto(getattr(fuente, "name", ""))


def leer_bytes(fuente: object) -> bytes:
    """Lee bytes desde upload, path, bytes o file-like."""
    if hasattr(fuente, "getvalue"):
        return bytes(fuente.getvalue())
    if isinstance(fuente, (bytes, bytearray)):
        return bytes(fuente)
    if isinstance(fuente, (str, os.PathLike)):
        # Las rutas internas (semilla, /datos y pruebas) se leen directamente.
        # La barra lateral solo entrega objetos de archivo, nunca rutas del
        # usuario; `ruta_local` sigue validando el directorio permitido.
        ruta = os.fspath(fuente)
        if not os.path.isfile(ruta):
            raise FileNotFoundError(
                f"No existe el archivo: {os.path.basename(ruta)}"
            )
        with open(ruta, "rb") as archivo:
            return archivo.read()
    if hasattr(fuente, "read"):
        posicion: Optional[int] = None
        try:
            posicion = fuente.tell()
        except Exception:
            pass
        contenido = fuente.read()
        if isinstance(contenido, str):
            contenido = contenido.encode("utf-8")
        try:
            if posicion is not None:
                fuente.seek(posicion)
        except Exception:
            pass
        return bytes(contenido)
    raise ValueError("No pude leer la planilla recibida.")


def decodificar(contenido: bytes) -> str:
    for codificacion in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            return contenido.decode(codificacion)
        except UnicodeDecodeError:
            continue
    raise ValueError("El archivo debe estar codificado en UTF-8 o Latin-1.")


def numero(columna: pd.Series, *, entero: bool = False) -> pd.Series:
    """Lee números con formato argentino (1.234,56) o internacional (1,234.56)."""

    def convertir(valor: object) -> float:
        if valor is None:
            return float("nan")
        try:
            if pd.isna(valor):
                return float("nan")
        except (TypeError, ValueError):
            pass
        if isinstance(valor, (int, float)):
            return float(valor)

        cadena = str(valor).strip().replace("%", "").replace(" ", "")
        cadena = cadena.replace("$", "").replace("\u00a0", "")
        if not cadena or cadena.lower() in {"nan", "none", "null", "-", "--"}:
            return float("nan")
        if "," in cadena and "." in cadena:
            if cadena.rfind(",") > cadena.rfind("."):
                cadena = cadena.replace(".", "").replace(",", ".")
            else:
                cadena = cadena.replace(",", "")
        elif "," in cadena:
            cadena = cadena.replace(",", ".")
        elif entero and re.fullmatch(r"-?\d{1,3}(\.\d{3})+", cadena):
            cadena = cadena.replace(".", "")
        try:
            return float(cadena)
        except ValueError:
            return float("nan")

    return columna.map(convertir)


def coalescer_columnas(tabla: pd.DataFrame) -> pd.DataFrame:
    """Une columnas duplicadas después del renombrado de alias."""
    if not tabla.columns.duplicated().any():
        return tabla

    resultado = pd.DataFrame(index=tabla.index)
    for columna in dict.fromkeys(tabla.columns):
        bloque = tabla.loc[:, tabla.columns == columna]
        if bloque.shape[1] == 1:
            resultado[columna] = bloque.iloc[:, 0]
        else:
            resultado[columna] = bloque.bfill(axis=1).iloc[:, 0]
    return resultado
