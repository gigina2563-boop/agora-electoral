"""Migraciones reproducibles y seguras de Ágora Electoral.

La migración no depende de que una tabla esté vacía. Crea el modelo histórico,
reconstruye la tabla de resultados con claves internas y auditoría, y vuelve a
sembrar las reglas que falten mediante ``INSERT OR IGNORE``. Antes de modificar
una base existente hace una copia consistente usando la API de respaldo de
SQLite.

Uso::

    python -m agora.migraciones --db observatorio.db

La función ``aplicar_migraciones`` es idempotente y puede invocarse desde la
aplicación al iniciar. Si la base es de solo lectura, la aplicación debe seguir
funcionando con los datos disponibles.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd

from .config import get_db_path
from .homologacion_historica import (
    crear_tablas as crear_tablas_historicas,
    sembrar_modelo_historico,
)
from .integridad import (
    COLUMNAS_RESULTADOS,
    crear_tabla_auditoria,
    crear_tabla_resultados,
    guardar_auditoria,
    normalizar_resultados,
    resumen_sumas,
)
from .utils import texto

VERSION_ACTUAL = "2026_11_02_homologacion_historica_v1"
TABLAS_MODELO = {
    "fuerzas_identidades",
    "fuerzas_denominaciones",
    "fuerzas_relaciones",
    "bloques_electorales",
    "bloque_miembros",
    "ausencias_verificadas",
}


def _tablas(conexion: sqlite3.Connection) -> set[str]:
    return {
        fila[0]
        for fila in conexion.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def _columna_existe(conexion: sqlite3.Connection, tabla: str, columna: str) -> bool:
    return any(
        fila[1] == columna
        for fila in conexion.execute(f"PRAGMA table_info({tabla})").fetchall()
    )


def _crear_tabla_version(conexion: sqlite3.Connection) -> None:
    conexion.execute(
        """
        CREATE TABLE IF NOT EXISTS esquema_migraciones (
            version TEXT PRIMARY KEY,
            aplicada_en TEXT NOT NULL,
            detalle TEXT
        )
        """
    )


def version_aplicada(conexion: sqlite3.Connection) -> Optional[str]:
    if "esquema_migraciones" not in _tablas(conexion):
        return None
    fila = conexion.execute(
        "SELECT version FROM esquema_migraciones ORDER BY aplicada_en DESC LIMIT 1"
    ).fetchone()
    return str(fila[0]) if fila else None


def crear_respaldo(
    conexion: sqlite3.Connection, ruta: str, version: str = VERSION_ACTUAL
) -> Optional[str]:
    """Copia consistente de SQLite antes de una migración."""
    if not os.path.exists(ruta) or os.path.getsize(ruta) == 0:
        return None
    directorio = os.path.join(os.path.dirname(os.path.abspath(ruta)), "backups")
    os.makedirs(directorio, exist_ok=True)
    sello = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destino = os.path.join(directorio, f"{os.path.basename(ruta)}.{version}.{sello}.bak")
    with closing(sqlite3.connect(destino)) as respaldo:
        conexion.backup(respaldo)
        respaldo.commit()
    return destino


def _reconstruir_resultados(conexion: sqlite3.Connection) -> dict[str, Any]:
    """Reconstruye la tabla canónica y conserva duplicados en auditoría."""
    tablas = _tablas(conexion)
    if "resultados_electorales" not in tablas:
        crear_tabla_resultados(conexion)
        crear_tabla_auditoria(conexion)
        return {
            "antes": resumen_sumas(pd.DataFrame()),
            "despues": resumen_sumas(pd.DataFrame()),
            "auditoria": 0,
            "duplicados": 0,
            "conflictos": 0,
        }

    antes = pd.read_sql_query("SELECT * FROM resultados_electorales", conexion)
    canonico, auditoria = normalizar_resultados(antes)
    if not auditoria.empty:
        auditoria = auditoria.copy()
        sin_huella = auditoria["huella_fuente"].map(texto).eq("")
        auditoria.loc[sin_huella, "huella_fuente"] = (
            f"migracion:{VERSION_ACTUAL}"
        )
        sin_archivo = auditoria["archivo"].map(texto).eq("")
        auditoria.loc[sin_archivo, "archivo"] = "migración"

    conexion.execute("DROP TABLE IF EXISTS resultados_electorales_nueva")
    conexion.execute(
        """
        CREATE TABLE resultados_electorales_nueva (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anio INTEGER NOT NULL,
            distrito TEXT NOT NULL,
            distrito_clave TEXT NOT NULL,
            distrito_original TEXT,
            circuito TEXT NOT NULL,
            cargo TEXT NOT NULL,
            tipo_eleccion TEXT,
            fuerza TEXT NOT NULL,
            fuerza_clave TEXT NOT NULL,
            fuerza_original TEXT,
            votos_positivos REAL,
            porcentaje REAL,
            estado_registro TEXT NOT NULL DEFAULT 'ok',
            fuente_huella TEXT,
            creado_en TEXT NOT NULL
        )
        """
    )
    columnas = [columna for columna in COLUMNAS_RESULTADOS if columna in canonico.columns]
    if columnas:
        canonico.reindex(columns=columnas).to_sql(
            "resultados_electorales_nueva",
            conexion,
            if_exists="append",
            index=False,
        )
    conexion.execute("DROP TABLE resultados_electorales")
    conexion.execute(
        "ALTER TABLE resultados_electorales_nueva RENAME TO resultados_electorales"
    )
    conexion.execute(
        "CREATE UNIQUE INDEX idx_resultados_clave "
        "ON resultados_electorales "
        "(anio, distrito_clave, circuito, cargo, fuerza_clave)"
    )
    conexion.execute(
        "CREATE INDEX idx_resultados_filtro "
        "ON resultados_electorales (cargo, anio, distrito_clave)"
    )
    crear_tabla_auditoria(conexion)
    guardar_auditoria(conexion, auditoria)
    return {
        "antes": resumen_sumas(antes),
        "despues": resumen_sumas(canonico),
        "auditoria": int(len(auditoria)),
        "duplicados": int(
            auditoria["estado"].eq("duplicado_exacto").sum()
        )
        if not auditoria.empty
        else 0,
        "conflictos": int(auditoria["estado"].eq("conflicto").sum())
        if not auditoria.empty
        else 0,
    }


def aplicar_migraciones(
    ruta: Optional[str] = None,
    *,
    respaldar: bool = True,
    sembrar: bool = True,
) -> dict[str, Any]:
    """Aplica la migración actual y devuelve un informe de lo realizado.

    Es segura para ejecutar varias veces: si la versión ya está registrada,
    solo asegura las tablas y agrega semillas o reglas que falten.
    """
    ruta = ruta or get_db_path()
    os.makedirs(os.path.dirname(os.path.abspath(ruta)) or ".", exist_ok=True)
    conexion = sqlite3.connect(ruta)
    informe: dict[str, Any] = {
        "ruta": ruta,
        "version": VERSION_ACTUAL,
        "respaldo": None,
        "migro": False,
        "resultados": None,
        "semillas": None,
    }
    try:
        conexion.execute("PRAGMA foreign_keys = ON")
        _crear_tabla_version(conexion)
        version = version_aplicada(conexion)
        if version == VERSION_ACTUAL:
            crear_tablas_historicas(conexion)
            if sembrar:
                informe["semillas"] = sembrar_modelo_historico(conexion)
            conexion.commit()
            informe["tablas"] = sorted(_tablas(conexion))
            return informe

        if respaldar and _tablas(conexion):
            informe["respaldo"] = crear_respaldo(conexion, ruta)

        with conexion:
            crear_tablas_historicas(conexion)
            informe["resultados"] = _reconstruir_resultados(conexion)
            if sembrar:
                informe["semillas"] = sembrar_modelo_historico(conexion)
            conexion.execute(
                """
                INSERT OR REPLACE INTO esquema_migraciones
                    (version, aplicada_en, detalle)
                VALUES (?, ?, ?)
                """,
                (
                    VERSION_ACTUAL,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "Modelo histórico, claves territoriales, deduplicación y auditoría.",
                ),
            )
        informe["migro"] = True
        informe["tablas"] = sorted(_tablas(conexion))
        return informe
    finally:
        conexion.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrar la base de Ágora Electoral")
    parser.add_argument("--db", default=None, help="Ruta a observatorio.db")
    parser.add_argument(
        "--sin-respaldo",
        action="store_true",
        help="No crear copia de seguridad antes de migrar",
    )
    parser.add_argument(
        "--sin-semillas",
        action="store_true",
        help="No importar las semillas del modelo histórico",
    )
    argumentos = parser.parse_args()
    informe = aplicar_migraciones(
        argumentos.db,
        respaldar=not argumentos.sin_respaldo,
        sembrar=not argumentos.sin_semillas,
    )
    print(f"Base: {informe['ruta']}")
    print(f"Versión: {informe['version']}")
    print(f"Migración aplicada: {informe['migro']}")
    if informe.get("respaldo"):
        print(f"Respaldo: {informe['respaldo']}")
    if informe.get("resultados"):
        print(f"Resultados: {informe['resultados']}")
    if informe.get("semillas"):
        print(f"Semillas: {informe['semillas']}")
    print(f"Tablas: {', '.join(informe.get('tablas', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
