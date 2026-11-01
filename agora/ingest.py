"""Lectura, validación y persistencia de planillas electorales.

La lectura y normalización vive en :mod:`agora.lectura_planillas`. Este módulo
conserva la API histórica (`normalizar_planilla`, `guardar_fuentes_en_db`,
`cargar_datos_db`) y agrega el resguardo de la fuente original para auditoría.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import pandas as pd
import streamlit as st

from .auth import exigir_admin
from .config import (
    CLAVE_ELECTORAL,
    COLUMNAS_SALIDA,
    DATOS_DIR,
    EXTENSIONES_PLANILLA,
    SEED_RESULTADOS,
    get_db_path,
    seed_habilitado,
)
from .lectura_planillas import (
    InspeccionPlanilla,
    _normalizar_con_inspeccion,
    inspeccionar_planilla,
    normalizar_planilla,
)
from .utils import (
    leer_bytes,
    nombre_fuente,
    normalizar_nombre,
    ruta_segura,
    texto,
)

__all__ = [
    "asegurar_db_sembrada",
    "cargar_datos_db",
    "csv_resultados",
    "guardar_fuentes_en_db",
    "inspeccionar_planilla",
    "listar_fuentes_db",
    "listar_planillas_locales",
    "normalizar_planilla",
    "ruta_local",
]


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------
def _crear_tabla_resultados(conexion: sqlite3.Connection) -> None:
    conexion.execute(
        """
        CREATE TABLE IF NOT EXISTS resultados_electorales (
            anio INTEGER NOT NULL,
            distrito TEXT NOT NULL,
            circuito TEXT NOT NULL,
            cargo TEXT NOT NULL,
            fuerza TEXT NOT NULL,
            votos_positivos REAL,
            porcentaje REAL NOT NULL
        )
        """
    )


def _crear_tabla_fuentes(conexion: sqlite3.Connection) -> None:
    conexion.execute(
        """
        CREATE TABLE IF NOT EXISTS planillas_fuentes (
            huella TEXT PRIMARY KEY,
            archivo TEXT NOT NULL,
            formato TEXT,
            tamano INTEGER,
            contenido BLOB NOT NULL,
            resumen_json TEXT,
            importado_en TEXT NOT NULL
        )
        """
    )


def _resumen_inspeccion(inspeccion: InspeccionPlanilla) -> dict[str, Any]:
    return {
        "nombre": inspeccion.nombre,
        "formato": inspeccion.formato,
        "hojas": list(inspeccion.hojas),
        "bloques": [
            {
                "hoja": bloque.hoja,
                "fila_encabezado": bloque.fila_encabezado,
                "filas_encabezado": bloque.filas_encabezado,
                "columnas": bloque.columnas,
                "mapeo": bloque.mapeo,
                "anio": bloque.anio,
                "cargo": bloque.cargo,
                "distrito": bloque.distrito,
                "circuito": bloque.circuito,
                "filas_utiles": bloque.filas_utiles,
                "filas_excluidas": bloque.filas_excluidas,
                "filas_excluidas_detalle": bloque.filas_excluidas_detalle,
                "metadatos": bloque.metadatos,
                "advertencias": bloque.advertencias,
                "errores": bloque.errores,
            }
            for bloque in inspeccion.bloques
        ],
        "advertencias": inspeccion.advertencias,
        "errores": inspeccion.errores,
    }


def _guardar_fuente_original(
    conexion: sqlite3.Connection,
    fuente: object,
    inspeccion: InspeccionPlanilla,
) -> None:
    contenido = leer_bytes(fuente)
    huella = hashlib.sha256(contenido).hexdigest()
    resumen = json.dumps(
        _resumen_inspeccion(inspeccion),
        ensure_ascii=False,
        default=str,
    )
    conexion.execute(
        """
        INSERT OR REPLACE INTO planillas_fuentes
            (huella, archivo, formato, tamano, contenido, resumen_json,
             importado_en)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            huella,
            nombre_fuente(fuente) or "planilla",
            inspeccion.formato,
            len(contenido),
            contenido,
            resumen,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )


def guardar_fuentes_en_db(
    fuentes: Iterable[object],
    opciones: Optional[Iterable[Optional[dict[str, Any]]]] = None,
) -> int:
    """Importa planillas. Requiere una sesión de administradora.

    La autorización se comprueba en Python, no sólo en la interfaz. La carga
    pública de la app nunca llama a esta función.
    """
    exigir_admin()
    return _persistir_fuentes_en_db(fuentes, opciones)


def _persistir_fuentes_en_db(
    fuentes: Iterable[object],
    opciones: Optional[Iterable[Optional[dict[str, Any]]]] = None,
) -> int:
    """Normaliza y persiste planillas. El llamador ya autorizó la operación.

    `opciones` permite acompañar cada fuente con un mapeo manual. La fuente
    original se guarda completa (bytes y resumen de lectura) en
    `planillas_fuentes` para poder auditar qué se importó.
    """
    lista = list(fuentes)
    if not lista:
        return 0

    if opciones is None:
        opciones_lista: list[Optional[dict[str, Any]]] = [None] * len(lista)
    elif isinstance(opciones, dict):
        opciones_lista = [opciones] * len(lista)
    else:
        opciones_lista = list(opciones)
    if len(opciones_lista) != len(lista):
        raise ValueError(
            "La cantidad de opciones de lectura no coincide con la cantidad "
            "de planillas."
        )

    normalizadas: list[pd.DataFrame] = []
    inspecciones: list[InspeccionPlanilla] = []
    for fuente, opcion in zip(lista, opciones_lista):
        datos, inspeccion = _normalizar_con_inspeccion(fuente, opcion)
        normalizadas.append(datos)
        inspecciones.append(inspeccion)

    nuevos = pd.concat(normalizadas, ignore_index=True)
    if nuevos.empty:
        return 0

    ruta = get_db_path()
    conexion = sqlite3.connect(ruta, timeout=30)
    try:
        with conexion:
            _crear_tabla_resultados(conexion)
            _crear_tabla_fuentes(conexion)
            for fuente, inspeccion in zip(lista, inspecciones):
                _guardar_fuente_original(conexion, fuente, inspeccion)

            actuales = pd.read_sql_query(
                "SELECT * FROM resultados_electorales", conexion
            )
            combinado = pd.concat([actuales, nuevos], ignore_index=True)
            combinado["anio"] = pd.to_numeric(
                combinado["anio"], errors="coerce"
            )
            combinado = combinado.dropna(subset=["anio"])
            combinado["anio"] = combinado["anio"].astype(int)
            for columna in ("distrito", "circuito", "cargo", "fuerza"):
                combinado[columna] = (
                    combinado[columna].fillna("").astype(str).str.strip()
                )
            combinado = combinado.drop_duplicates(
                subset=CLAVE_ELECTORAL, keep="last"
            )
            combinado = combinado[COLUMNAS_SALIDA]

            combinado.to_sql(
                "resultados_electorales_tmp",
                conexion,
                if_exists="replace",
                index=False,
            )
            conexion.execute("DROP TABLE IF EXISTS resultados_electorales")
            conexion.execute(
                "ALTER TABLE resultados_electorales_tmp "
                "RENAME TO resultados_electorales"
            )
            conexion.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_resultados_clave "
                "ON resultados_electorales "
                "(anio, distrito, circuito, cargo, fuerza)"
            )
            conexion.execute(
                "CREATE INDEX IF NOT EXISTS idx_resultados_filtro "
                "ON resultados_electorales (cargo, anio, distrito)"
            )
    finally:
        conexion.close()

    st.cache_data.clear()
    return len(nuevos)


def listar_fuentes_db() -> pd.DataFrame:
    """Lista las planillas originales guardadas para auditoría."""
    ruta = get_db_path()
    if not os.path.isfile(ruta):
        return pd.DataFrame(
            columns=["huella", "archivo", "formato", "tamano", "importado_en"]
        )
    conexion = sqlite3.connect(ruta, timeout=30)
    try:
        tablas = {
            fila[0]
            for fila in conexion.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "planillas_fuentes" not in tablas:
            return pd.DataFrame(
                columns=[
                    "huella",
                    "archivo",
                    "formato",
                    "tamano",
                    "importado_en",
                ]
            )
        return pd.read_sql_query(
            "SELECT huella, archivo, formato, tamano, importado_en "
            "FROM planillas_fuentes ORDER BY importado_en DESC",
            conexion,
        )
    finally:
        conexion.close()


def _limpiar_datos_electorales(tabla: pd.DataFrame) -> pd.DataFrame:
    if tabla is None or tabla.empty:
        return pd.DataFrame(columns=COLUMNAS_SALIDA)

    datos = tabla.copy()
    for columna in COLUMNAS_SALIDA:
        if columna not in datos.columns:
            datos[columna] = pd.NA
    datos = datos[COLUMNAS_SALIDA]

    datos["anio"] = pd.to_numeric(datos["anio"], errors="coerce")
    datos["votos_positivos"] = pd.to_numeric(
        datos["votos_positivos"], errors="coerce"
    )
    datos["porcentaje"] = pd.to_numeric(
        datos["porcentaje"], errors="coerce"
    )
    datos = datos.dropna(subset=["anio", "fuerza"])
    datos["anio"] = datos["anio"].astype(int)
    for columna in ("distrito", "circuito", "cargo", "fuerza"):
        datos[columna] = datos[columna].fillna("").astype(str).str.strip()
    return datos.reset_index(drop=True)


def _contar_filas(ruta: str) -> int:
    if not os.path.isfile(ruta):
        return 0
    conexion = sqlite3.connect(ruta, timeout=30)
    try:
        tablas = {
            fila[0]
            for fila in conexion.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "resultados_electorales" not in tablas:
            return 0
        return int(
            conexion.execute(
                "SELECT COUNT(*) FROM resultados_electorales"
            ).fetchone()[0]
        )
    finally:
        conexion.close()


def asegurar_db_sembrada() -> bool:
    """Carga la base semilla si la base está vacía.

    En el servidor gratuito el sistema de archivos es efímero: la base se
    reconstruye desde `data/seed_resultados.csv` al reiniciar el proceso.
    """
    if not seed_habilitado():
        return False
    if _contar_filas(get_db_path()) > 0:
        return False
    if not os.path.isfile(SEED_RESULTADOS):
        return False
    _persistir_fuentes_en_db([SEED_RESULTADOS])
    return True


@st.cache_data(ttl=45, show_spinner=False)
def cargar_datos_db() -> pd.DataFrame:
    """Lee la base SQLite y mantiene compatibilidad con la tabla antigua."""
    ruta = get_db_path()
    if not os.path.isfile(ruta):
        return pd.DataFrame(columns=COLUMNAS_SALIDA)

    conexion = sqlite3.connect(ruta, timeout=30)
    try:
        tablas = {
            fila[0]
            for fila in conexion.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "resultados_electorales" in tablas:
            return _limpiar_datos_electorales(
                pd.read_sql_query(
                    "SELECT * FROM resultados_electorales", conexion
                )
            )
        if "elecciones_circuito" not in tablas:
            return pd.DataFrame(columns=COLUMNAS_SALIDA)

        anterior = pd.read_sql_query(
            "SELECT * FROM elecciones_circuito", conexion
        )
    finally:
        conexion.close()

    anterior.columns = [
        normalizar_nombre(columna) for columna in anterior.columns
    ]
    anterior = anterior.rename(
        columns={
            "partido": "fuerza",
            "zona": "circuito",
            "pct_2023": "pct_2023",
            "pct_2025": "pct_2025",
            "variacion": "diferencia",
            "variacion_pct": "diferencia",
        }
    )
    requeridas = {"fuerza", "pct_2023", "pct_2025"}
    if not requeridas.issubset(anterior.columns):
        return pd.DataFrame(columns=COLUMNAS_SALIDA)

    if "circuito" not in anterior.columns:
        anterior["circuito"] = "General"
    if "distrito" not in anterior.columns:
        anterior["distrito"] = "Departamento no especificado"
    if "cargo" not in anterior.columns:
        anterior["cargo"] = "No especificado"

    largo = anterior.melt(
        id_vars=["distrito", "circuito", "cargo", "fuerza"],
        value_vars=["pct_2023", "pct_2025"],
        var_name="periodo",
        value_name="porcentaje",
    )
    largo["anio"] = largo["periodo"].map({"pct_2023": 2023, "pct_2025": 2025})
    largo["votos_positivos"] = pd.NA
    return _limpiar_datos_electorales(
        largo[COLUMNAS_SALIDA]
    )


def listar_planillas_locales() -> list[str]:
    """Lista planillas dentro de BASE_DIR/datos sin salir del proyecto."""
    if not os.path.isdir(DATOS_DIR):
        return []
    archivos: list[str] = []
    for nombre in sorted(os.listdir(DATOS_DIR)):
        if nombre.startswith("."):
            continue
        ruta = os.path.join(DATOS_DIR, nombre)
        if (
            os.path.isfile(ruta)
            and os.path.splitext(nombre)[1].lower() in EXTENSIONES_PLANILLA
        ):
            archivos.append(ruta)
    return archivos


def csv_resultados(datos: pd.DataFrame) -> bytes:
    """Exporta resultados filtrados con BOM para Excel."""
    return datos.to_csv(index=False).encode("utf-8-sig")


def ruta_local(nombre: str) -> str:
    return ruta_segura(os.path.join("datos", nombre))
