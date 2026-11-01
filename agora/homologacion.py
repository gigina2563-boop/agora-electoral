"""Homologación de partidos, alianzas y coaliciones entre elecciones.

La tabla evita comparar como fuerzas distintas a la misma organización por
cambios de nombre, tildes o denominación de alianza. Se persiste en SQLite y se
inicializa desde `data/homologacion_partidos.csv`.
"""

from __future__ import annotations

import io
import os
import sqlite3
from typing import Any, Optional

import pandas as pd
import streamlit as st

from .config import SEED_HOMOLOGACION, get_db_path
from .utils import (
    clave_fuerza,
    decodificar,
    leer_bytes,
    normalizar_nombre,
    numero,
    texto,
)

COLUMNAS_HOMOLOGACION = [
    "fuerza_origen",
    "fuerza_homologada",
    "tipo",
    "anio_desde",
    "anio_hasta",
    "notas",
]
TIPOS_HOMOLOGACION = ["Partido", "Alianza", "Coalición", "Otro"]


def _tabla_vacia() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNAS_HOMOLOGACION)


def _crear_tabla_homologacion(conexion: sqlite3.Connection) -> None:
    conexion.execute(
        """
        CREATE TABLE IF NOT EXISTS homologacion_fuerzas (
            fuerza_origen TEXT NOT NULL,
            fuerza_homologada TEXT NOT NULL,
            tipo TEXT NOT NULL DEFAULT 'Partido',
            anio_desde INTEGER,
            anio_hasta INTEGER,
            notas TEXT
        )
        """
    )


def _leer_csv_homologacion(fuente: object) -> pd.DataFrame:
    contenido = decodificar(leer_bytes(fuente))
    if not contenido.strip():
        raise ValueError("La tabla de homologación está vacía.")
    try:
        tabla = pd.read_csv(
            io.StringIO(contenido),
            dtype=str,
            sep=None,
            engine="python",
        )
    except Exception as error:
        raise ValueError(
            "No pude leer la tabla de homologación. Debe ser un CSV con "
            "encabezados."
        ) from error
    return tabla


def limpiar_homologacion(tabla: pd.DataFrame) -> pd.DataFrame:
    """Valida y normaliza la tabla de homologación."""
    if tabla is None or tabla.empty:
        return _tabla_vacia()

    datos = tabla.copy()
    datos.columns = [normalizar_nombre(columna) for columna in datos.columns]
    datos = datos.rename(
        columns={
            "origen": "fuerza_origen",
            "fuerza": "fuerza_origen",
            "nombre_origen": "fuerza_origen",
            "homologada": "fuerza_homologada",
            "canonica": "fuerza_homologada",
            "nombre_canonico": "fuerza_homologada",
            "desde": "anio_desde",
            "hasta": "anio_hasta",
            "nota": "notas",
        }
    )
    for columna in COLUMNAS_HOMOLOGACION:
        if columna not in datos.columns:
            datos[columna] = pd.NA
    datos = datos[COLUMNAS_HOMOLOGACION].copy()

    datos["fuerza_origen"] = (
        datos["fuerza_origen"].fillna("").astype(str).str.strip()
    )
    datos["fuerza_homologada"] = (
        datos["fuerza_homologada"].fillna("").astype(str).str.strip()
    )
    datos["tipo"] = (
        datos["tipo"]
        .fillna("Partido")
        .astype(str)
        .str.strip()
        .replace({"": "Partido"})
    )
    datos["notas"] = datos["notas"].fillna("").astype(str).str.strip()
    datos["anio_desde"] = pd.to_numeric(
        datos["anio_desde"], errors="coerce"
    ).astype("Int64")
    datos["anio_hasta"] = pd.to_numeric(
        datos["anio_hasta"], errors="coerce"
    ).astype("Int64")

    vacias = datos["fuerza_origen"].eq("") | datos[
        "fuerza_homologada"
    ].eq("")
    if vacias.any():
        raise ValueError(
            "Toda fila de homologación necesita 'fuerza_origen' y "
            "'fuerza_homologada'."
        )
    for columna in ("anio_desde", "anio_hasta"):
        no_entero = datos[columna].notna() & (
            datos[columna].astype("Float64") % 1 != 0
        )
        if no_entero.any():
            raise ValueError(f"'{columna}' debe contener años enteros.")
    comparables = datos["anio_desde"].notna() & datos["anio_hasta"].notna()
    if (
        datos.loc[comparables, "anio_desde"]
        > datos.loc[comparables, "anio_hasta"]
    ).any():
        raise ValueError(
            "'anio_desde' no puede ser mayor que 'anio_hasta'."
        )

    datos["_clave"] = datos["fuerza_origen"].map(clave_fuerza)
    datos = datos.drop_duplicates(
        subset=["_clave", "anio_desde", "anio_hasta"],
        keep="last",
    ).drop(columns="_clave")
    return datos.reset_index(drop=True)


@st.cache_data(ttl=60, show_spinner=False)
def cargar_homologacion() -> pd.DataFrame:
    """Lee la homologación de SQLite; si no existe, usa el CSV semilla."""
    ruta = get_db_path()
    if os.path.isfile(ruta):
        conexion = sqlite3.connect(ruta, timeout=30)
        try:
            tablas = {
                fila[0]
                for fila in conexion.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "homologacion_fuerzas" in tablas:
                cantidad = conexion.execute(
                    "SELECT COUNT(*) FROM homologacion_fuerzas"
                ).fetchone()[0]
                if cantidad:
                    tabla = pd.read_sql_query(
                        "SELECT * FROM homologacion_fuerzas", conexion
                    )
                    return limpiar_homologacion(tabla)
        finally:
            conexion.close()

    if os.path.isfile(SEED_HOMOLOGACION):
        try:
            return limpiar_homologacion(
                _leer_csv_homologacion(SEED_HOMOLOGACION)
            )
        except Exception:
            pass
    return _tabla_vacia()


def guardar_homologacion(tabla: pd.DataFrame) -> int:
    """Reemplaza la tabla de homologación de forma atómica."""
    datos = limpiar_homologacion(tabla)
    ruta = get_db_path()
    conexion = sqlite3.connect(ruta, timeout=30)
    try:
        with conexion:
            _crear_tabla_homologacion(conexion)
            datos.to_sql(
                "homologacion_fuerzas_tmp",
                conexion,
                if_exists="replace",
                index=False,
            )
            conexion.execute("DROP TABLE IF EXISTS homologacion_fuerzas")
            conexion.execute(
                "ALTER TABLE homologacion_fuerzas_tmp "
                "RENAME TO homologacion_fuerzas"
            )
            conexion.execute(
                "CREATE INDEX IF NOT EXISTS idx_homologacion_origen "
                "ON homologacion_fuerzas (fuerza_origen)"
            )
    finally:
        conexion.close()
    st.cache_data.clear()
    return len(datos)


def asegurar_homologacion_sembrada() -> None:
    """Carga la tabla semilla si la base todavía no tiene homologación."""
    if not os.path.isfile(SEED_HOMOLOGACION):
        return
    ruta = get_db_path()
    existe = False
    if os.path.isfile(ruta):
        conexion = sqlite3.connect(ruta, timeout=30)
        try:
            tablas = {
                fila[0]
                for fila in conexion.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "homologacion_fuerzas" in tablas:
                existe = bool(
                    conexion.execute(
                        "SELECT COUNT(*) FROM homologacion_fuerzas"
                    ).fetchone()[0]
                )
        finally:
            conexion.close()
    if not existe:
        guardar_homologacion(_leer_csv_homologacion(SEED_HOMOLOGACION))


def _entradas_mapeo(tabla: pd.DataFrame) -> list[tuple[str, Any, Any, str]]:
    if tabla is None or tabla.empty:
        return []
    entradas: list[tuple[str, Any, Any, str]] = []
    for fila in tabla.itertuples(index=False):
        clave = clave_fuerza(getattr(fila, "fuerza_origen", ""))
        canonica = texto(getattr(fila, "fuerza_homologada", ""))
        if not clave or not canonica:
            continue
        desde = getattr(fila, "anio_desde", None)
        hasta = getattr(fila, "anio_hasta", None)
        desde = int(desde) if pd.notna(desde) else None
        hasta = int(hasta) if pd.notna(hasta) else None
        entradas.append((clave, desde, hasta, canonica))
    # Las reglas con año explícito tienen prioridad sobre las generales.
    entradas.sort(key=lambda item: (item[1] is None and item[2] is None,))
    return entradas


def _mapear(
    nombre: Any,
    anio: Any,
    entradas: list[tuple[str, Any, Any, str]],
) -> tuple[str, bool]:
    original = texto(nombre)
    clave = clave_fuerza(original)
    try:
        anio_numero = int(anio)
    except (TypeError, ValueError):
        anio_numero = None
    for clave_mapeo, desde, hasta, canonica in entradas:
        if clave != clave_mapeo:
            continue
        if anio_numero is None:
            if desde is None and hasta is None:
                return canonica, True
            continue
        if desde is not None and anio_numero < desde:
            continue
        if hasta is not None and anio_numero > hasta:
            continue
        return canonica, True
    return original, False


def aplicar_homologacion(
    datos: pd.DataFrame, tabla: pd.DataFrame
) -> pd.DataFrame:
    """Reemplaza `fuerza` por su nombre homologado conservando el original."""
    if datos is None or datos.empty:
        return datos.copy() if datos is not None else pd.DataFrame()
    resultado = datos.copy()
    entradas = _entradas_mapeo(tabla)
    if not entradas:
        resultado["fuerza_origen"] = resultado["fuerza"]
        return resultado

    originales = resultado["fuerza"].tolist()
    anios = (
        resultado["anio"].tolist()
        if "anio" in resultado.columns
        else [None] * len(resultado)
    )
    homologadas: list[str] = []
    for nombre, anio in zip(originales, anios):
        canonica, _ = _mapear(nombre, anio, entradas)
        homologadas.append(canonica)
    resultado["fuerza_origen"] = originales
    resultado["fuerza"] = homologadas
    return resultado


def fuerzas_sin_mapear(datos: pd.DataFrame, tabla: pd.DataFrame) -> list[str]:
    """Lista fuerzas del dataset sin una regla de homologación aplicable."""
    if datos is None or datos.empty:
        return []
    entradas = _entradas_mapeo(tabla)
    if not entradas:
        return sorted(datos["fuerza"].dropna().astype(str).unique().tolist())
    pendientes: set[str] = set()
    for nombre, anio in zip(datos["fuerza"], datos.get("anio", [None] * len(datos))):
        _, encontrado = _mapear(nombre, anio, entradas)
        if not encontrado:
            pendientes.add(texto(nombre))
    return sorted(nombre for nombre in pendientes if nombre)


def sembrar_identidades(datos: pd.DataFrame, tabla: pd.DataFrame) -> int:
    """Agrega reglas identidad para fuerzas que aún no tienen homologación."""
    if datos is None or datos.empty:
        return 0
    entradas = _entradas_mapeo(tabla)
    existentes = {entrada[0] for entrada in entradas}
    nuevas = []
    for nombre in sorted(datos["fuerza"].dropna().astype(str).unique()):
        clave = clave_fuerza(nombre)
        if not clave or clave in existentes:
            continue
        nuevas.append(
            {
                "fuerza_origen": nombre,
                "fuerza_homologada": nombre,
                "tipo": "Partido",
                "anio_desde": pd.NA,
                "anio_hasta": pd.NA,
                "notas": "Identidad agregada automáticamente",
            }
        )
    if not nuevas:
        return 0
    combinada = pd.concat(
        [tabla if not tabla.empty else _tabla_vacia(), pd.DataFrame(nuevas)],
        ignore_index=True,
    )
    guardar_homologacion(combinada)
    return len(nuevas)


def cargar_homologacion_desde_archivo(fuente: object) -> pd.DataFrame:
    return limpiar_homologacion(_leer_csv_homologacion(fuente))


def csv_homologacion(tabla: pd.DataFrame) -> bytes:
    return limpiar_homologacion(tabla).to_csv(index=False).encode("utf-8-sig")
