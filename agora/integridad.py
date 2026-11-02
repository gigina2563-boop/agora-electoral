"""Integridad de resultados electorales.

Este módulo concentra tres decisiones que antes estaban dispersas:

1. una clave territorial estable (``LA_RIOJA`` y ``LA RIOJA`` son el mismo
   departamento);
2. una clave de fuerza tolerante a tildes, mayúsculas y separadores, sin
   fusionar denominaciones que solo se parecen;
3. una deduplicación explícita con auditoría. Los registros repetidos se
   apartan de la tabla canónica, pero nunca se pierden: quedan en
   ``registros_auditoria`` y en la planilla original guardada en
   ``planillas_fuentes``.

No se usa similitud difusa. La única equivalencia automática es la
normalización ortográfica exacta (tildes, mayúsculas, guiones y espacios).
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import pandas as pd

from .config import COLUMNAS_SALIDA
from .utils import clave_fuerza, texto

COLUMNAS_RESULTADOS = [
    "anio",
    "distrito",
    "distrito_clave",
    "distrito_original",
    "circuito",
    "cargo",
    "tipo_eleccion",
    "fuerza",
    "fuerza_clave",
    "fuerza_original",
    "votos_positivos",
    "porcentaje",
    "estado_registro",
    "fuente_huella",
    "creado_en",
]

COLUMNAS_AUDITORIA = [
    "huella_fuente",
    "archivo",
    "anio",
    "distrito",
    "distrito_clave",
    "circuito",
    "cargo",
    "fuerza",
    "fuerza_clave",
    "votos_positivos",
    "porcentaje",
    "estado",
    "detalle",
    "requiere_revision",
    "creado_en",
]

ESTADOS_AUDITORIA = ["duplicado_exacto", "conflicto", "normalizado"]

_ALIAS_DISTRITO = {
    "la rioja": "LA RIOJA",
    "capital": "Capital",
}

_TIPOS_ELECCION = (
    ("paso", "PASO"),
    ("primaria", "PASO"),
    ("general", "GENERAL"),
    ("provincial", "PROVINCIAL"),
    ("municipal", "MUNICIPAL"),
)


def ahora_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clave_territorio(valor: Any) -> str:
    """Clave territorial consistente para distritos y departamentos.

    No se apoya en una tabla externa: normaliza tildes, mayúsculas, guiones y
    guiones bajos. ``LA_RIOJA`` y ``LA RIOJA`` producen ``la rioja``.
    """
    cadena = unicodedata.normalize("NFKD", texto(valor))
    cadena = "".join(c for c in cadena if not unicodedata.combining(c))
    cadena = cadena.casefold().replace("_", " ").replace("-", " ")
    cadena = re.sub(r"[^a-z0-9]+", " ", cadena)
    return re.sub(r"\s+", " ", cadena).strip()


def canonizar_distrito(valor: Any) -> str:
    """Devuelve el nombre visible del distrito sin perder su clave estable."""
    clave = clave_territorio(valor)
    if clave in _ALIAS_DISTRITO:
        return _ALIAS_DISTRITO[clave]
    return re.sub(r"\s+", " ", texto(valor).replace("_", " ")).strip()


def tipo_eleccion_desde_cargo(cargo: Any) -> str:
    """Infiere la instancia electoral a partir del texto del cargo.

    La app ya usa cargos como ``DIPUTADO NACIONAL — GENERALES`` o
    ``DIPUTADOS/AS NACIONALES — PASO``. Si no hay marca explícita se devuelve
    una cadena vacía: no se inventa el tipo.
    """
    normalizado = clave_fuerza(cargo)
    if not normalizado:
        return ""
    for token, tipo in _TIPOS_ELECCION:
        if token in normalizado:
            return tipo
    return ""


def _misma_fila(valor_a: Any, valor_b: Any) -> bool:
    if pd.isna(valor_a) and pd.isna(valor_b):
        return True
    try:
        return float(valor_a) == float(valor_b)
    except (TypeError, ValueError):
        return texto(valor_a) == texto(valor_b)


def _puntaje_fila(fila: pd.Series) -> tuple[int, int, int, int, int]:
    """Elige la variante más informativa y mejor escrita de un duplicado."""
    nombre = texto(fila.get("fuerza"))
    tildes = sum(
        1 for caracter in unicodedata.normalize("NFC", nombre) if ord(caracter) > 127
    )
    prioridad = int(fila.get("_prioridad", 0) or 0)
    votos = 1 if pd.notna(fila.get("votos_positivos")) else 0
    porcentaje = 1 if pd.notna(fila.get("porcentaje")) else 0
    return (prioridad, tildes, len(nombre), votos, porcentaje)


def preparar_resultados(
    datos: pd.DataFrame,
    *,
    fuente_huella: Optional[str] = None,
    archivo: Optional[str] = None,
    prioridad: int = 0,
) -> pd.DataFrame:
    """Agrega claves internas y normaliza el territorio sin tocar los valores.

    ``distrito_original`` y ``fuerza_original`` conservan el texto de origen.
    ``distrito`` queda con una forma visible canónica y ``distrito_clave`` con
    la clave estable que se usa para comparar.
    """
    if datos is None or datos.empty:
        return pd.DataFrame(columns=COLUMNAS_RESULTADOS)

    tabla = datos.copy()
    for columna in COLUMNAS_SALIDA:
        if columna not in tabla.columns:
            tabla[columna] = pd.NA

    for columna in ("distrito", "circuito", "cargo", "fuerza"):
        tabla[columna] = (
            tabla[columna].fillna("").astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
        )

    if "distrito_original" not in tabla.columns:
        tabla["distrito_original"] = tabla["distrito"]
    else:
        tabla["distrito_original"] = tabla["distrito_original"].where(
            tabla["distrito_original"].notna(), tabla["distrito"]
        )
    if "fuerza_original" not in tabla.columns:
        tabla["fuerza_original"] = tabla["fuerza"]
    else:
        tabla["fuerza_original"] = tabla["fuerza_original"].where(
            tabla["fuerza_original"].notna(), tabla["fuerza"]
        )

    tabla["distrito_clave"] = tabla["distrito"].map(clave_territorio)
    tabla["distrito"] = tabla["distrito"].map(canonizar_distrito)
    tabla["fuerza_clave"] = tabla["fuerza"].map(clave_fuerza)
    tabla["tipo_eleccion"] = [
        texto(tipo) or tipo_eleccion_desde_cargo(cargo)
        for tipo, cargo in zip(
            tabla.get("tipo_eleccion", pd.Series("", index=tabla.index)),
            tabla["cargo"],
        )
    ]
    tabla["anio"] = pd.to_numeric(tabla["anio"], errors="coerce")
    tabla["votos_positivos"] = pd.to_numeric(
        tabla["votos_positivos"], errors="coerce"
    )
    tabla["porcentaje"] = pd.to_numeric(tabla["porcentaje"], errors="coerce")
    tabla = tabla.dropna(subset=["anio"]).copy()
    tabla["anio"] = tabla["anio"].astype(int)
    if "fuente_huella" in tabla.columns:
        if fuente_huella is not None:
            tabla["fuente_huella"] = tabla["fuente_huella"].fillna(
                fuente_huella
            )
    else:
        tabla["fuente_huella"] = fuente_huella
    if archivo:
        tabla["archivo"] = archivo
    if "estado_registro" in tabla.columns:
        tabla["estado_registro"] = tabla["estado_registro"].fillna("ok")
    else:
        tabla["estado_registro"] = "ok"
    if "creado_en" in tabla.columns:
        tabla["creado_en"] = tabla["creado_en"].fillna(ahora_utc())
    else:
        tabla["creado_en"] = ahora_utc()
    if "_prioridad" in tabla.columns:
        tabla["_prioridad"] = pd.to_numeric(
            tabla["_prioridad"], errors="coerce"
        ).fillna(prioridad)
    else:
        tabla["_prioridad"] = prioridad
    return tabla


def _clave_resultado(fila: pd.Series) -> tuple[Any, ...]:
    return (
        int(fila["anio"]),
        texto(fila["distrito_clave"]),
        clave_fuerza(fila["circuito"]),
        clave_fuerza(fila["cargo"]),
        texto(fila["fuerza_clave"]),
    )


def _fila_auditoria(
    fila: pd.Series,
    *,
    estado: str,
    detalle: str,
    huella: Optional[str] = None,
    archivo: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "huella_fuente": texto(fila.get("fuente_huella")) or texto(huella),
        "archivo": texto(fila.get("archivo")) or texto(archivo),
        "anio": int(fila["anio"]) if pd.notna(fila["anio"]) else None,
        "distrito": texto(fila.get("distrito")),
        "distrito_clave": texto(fila.get("distrito_clave")),
        "circuito": texto(fila.get("circuito")),
        "cargo": texto(fila.get("cargo")),
        "fuerza": texto(fila.get("fuerza")),
        "fuerza_clave": texto(fila.get("fuerza_clave")),
        "votos_positivos": (
            float(fila["votos_positivos"])
            if pd.notna(fila.get("votos_positivos"))
            else None
        ),
        "porcentaje": (
            float(fila["porcentaje"])
            if pd.notna(fila.get("porcentaje"))
            else None
        ),
        "estado": estado,
        "detalle": detalle,
        "requiere_revision": 1 if estado == "conflicto" else 0,
        "creado_en": ahora_utc(),
    }


def normalizar_resultados(
    datos: pd.DataFrame,
    *,
    fuente_huella: Optional[str] = None,
    archivo: Optional[str] = None,
    prioridad: int = 0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Deduplica resultados y devuelve ``(canónicos, auditoría)``.

    - Dos filas con la misma clave y los mismos votos/porcentaje son un
      duplicado exacto: se conserva la mejor escritura y se audita la otra.
    - Dos filas con la misma clave y valores distintos son un conflicto: se
      conserva una fila determinista y se marca el conflicto para revisión.
      Nunca se descarta silenciosamente.
    """
    preparado = preparar_resultados(
        datos,
        fuente_huella=fuente_huella,
        archivo=archivo,
        prioridad=prioridad,
    )
    if preparado.empty:
        return preparado.drop(columns=["_prioridad"], errors="ignore"), pd.DataFrame(
            columns=COLUMNAS_AUDITORIA
        )

    preparado["_clave_resultado"] = preparado.apply(_clave_resultado, axis=1)
    canonicas: list[pd.Series] = []
    auditoria: list[dict[str, Any]] = []

    for _, grupo in preparado.groupby("_clave_resultado", sort=False, dropna=False):
        if len(grupo) == 1:
            canonicas.append(grupo.iloc[0])
            continue

        ordenado = sorted(
            (fila for _, fila in grupo.iterrows()),
            key=_puntaje_fila,
            reverse=True,
        )
        conservada = ordenado[0]
        canonicas.append(conservada)
        for fila in ordenado[1:]:
            mismo_valor = _misma_fila(
                fila.get("votos_positivos"), conservada.get("votos_positivos")
            ) and _misma_fila(
                fila.get("porcentaje"), conservada.get("porcentaje")
            )
            if mismo_valor:
                auditoria.append(
                    _fila_auditoria(
                        fila,
                        estado="duplicado_exacto",
                        detalle=(
                            "Misma clave y mismos votos/porcentaje. Se conservó "
                            f"'{texto(conservada.get('fuerza'))}'."
                        ),
                        huella=fuente_huella,
                        archivo=archivo,
                    )
                )
            else:
                auditoria.append(
                    _fila_auditoria(
                        fila,
                        estado="conflicto",
                        detalle=(
                            "Misma clave con votos o porcentaje distintos. "
                            "Requiere revisión de la fuente original; se "
                            f"conservó '{texto(conservada.get('fuerza'))}'."
                        ),
                        huella=fuente_huella,
                        archivo=archivo,
                    )
                )

    resultado = pd.DataFrame(canonicas).reset_index(drop=True)
    resultado = resultado.drop(columns=["_clave_resultado", "_prioridad"], errors="ignore")
    resultado = resultado.sort_values(
        ["anio", "distrito_clave", "circuito", "cargo", "fuerza_clave"]
    ).reset_index(drop=True)
    auditoria_df = pd.DataFrame(auditoria, columns=COLUMNAS_AUDITORIA)
    return resultado, auditoria_df


def resumen_sumas(datos: pd.DataFrame) -> dict[str, Any]:
    """Resumen verificable de filas, votos y porcentajes."""
    if datos is None or datos.empty:
        return {
            "filas": 0,
            "votos_positivos": 0.0,
            "porcentaje": 0.0,
            "fuerzas": 0,
            "anios": [],
        }
    votos = pd.to_numeric(datos.get("votos_positivos"), errors="coerce")
    porcentaje = pd.to_numeric(datos.get("porcentaje"), errors="coerce")
    anios = sorted(
        int(anio)
        for anio in pd.to_numeric(datos.get("anio"), errors="coerce").dropna().unique()
    )
    return {
        "filas": int(len(datos)),
        "votos_positivos": float(votos.sum(min_count=1)) if votos.notna().any() else 0.0,
        "porcentaje": float(porcentaje.sum(min_count=1)) if porcentaje.notna().any() else 0.0,
        "fuerzas": int(datos["fuerza"].nunique()) if "fuerza" in datos.columns else 0,
        "anios": anios,
    }


def comparar_sumas(antes: pd.DataFrame, despues: pd.DataFrame) -> dict[str, Any]:
    """Compara los totales antes/después y calcula el esperado sin duplicados."""
    resumen_antes = resumen_sumas(antes)
    resumen_despues = resumen_sumas(despues)
    esperado, _ = normalizar_resultados(antes)
    resumen_esperado = resumen_sumas(esperado)
    return {
        "antes": resumen_antes,
        "despues": resumen_despues,
        "esperado_sin_duplicados": resumen_esperado,
        "filas_retiradas": resumen_antes["filas"] - resumen_despues["filas"],
        "votos_retirados_por_duplicados": round(
            resumen_antes["votos_positivos"]
            - resumen_despues["votos_positivos"],
            4,
        ),
        "cuadra_votos": abs(
            resumen_despues["votos_positivos"]
            - resumen_esperado["votos_positivos"]
        )
        < 0.01,
    }


def crear_tabla_resultados(conexion: sqlite3.Connection) -> None:
    """Crea la tabla canónica con claves internas y trazabilidad."""
    conexion.execute(
        """
        CREATE TABLE IF NOT EXISTS resultados_electorales (
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
    conexion.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_resultados_clave "
        "ON resultados_electorales "
        "(anio, distrito_clave, circuito, cargo, fuerza_clave)"
    )
    conexion.execute(
        "CREATE INDEX IF NOT EXISTS idx_resultados_filtro "
        "ON resultados_electorales (cargo, anio, distrito_clave)"
    )


def crear_tabla_auditoria(conexion: sqlite3.Connection) -> None:
    conexion.execute(
        """
        CREATE TABLE IF NOT EXISTS registros_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            huella_fuente TEXT,
            archivo TEXT,
            anio INTEGER,
            distrito TEXT,
            distrito_clave TEXT,
            circuito TEXT,
            cargo TEXT,
            fuerza TEXT,
            fuerza_clave TEXT,
            votos_positivos REAL,
            porcentaje REAL,
            estado TEXT NOT NULL,
            detalle TEXT,
            requiere_revision INTEGER NOT NULL DEFAULT 0,
            creado_en TEXT NOT NULL
        )
        """
    )
    conexion.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_auditoria_unica "
        "ON registros_auditoria "
        "(huella_fuente, anio, distrito_clave, circuito, cargo, "
        "fuerza_clave, estado, votos_positivos, porcentaje)"
    )
    conexion.execute(
        "CREATE INDEX IF NOT EXISTS idx_auditoria_estado "
        "ON registros_auditoria (estado, requiere_revision)"
    )


def guardar_auditoria(
    conexion: sqlite3.Connection, auditoria: Optional[pd.DataFrame]
) -> int:
    """Persiste la auditoría sin duplicar filas ya registradas."""
    if auditoria is None or auditoria.empty:
        return 0
    crear_tabla_auditoria(conexion)
    filas = auditoria.reindex(columns=COLUMNAS_AUDITORIA)
    guardadas = 0
    for fila in filas.itertuples(index=False):
        valores = list(fila)
        conexion.execute(
            """
            INSERT OR IGNORE INTO registros_auditoria
                (huella_fuente, archivo, anio, distrito, distrito_clave,
                 circuito, cargo, fuerza, fuerza_clave, votos_positivos,
                 porcentaje, estado, detalle, requiere_revision, creado_en)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            valores,
        )
        guardadas += conexion.execute("SELECT changes()").fetchone()[0]
    return guardadas


def columnas_tabla(conexion: sqlite3.Connection, tabla: str) -> set[str]:
    return {
        fila[1]
        for fila in conexion.execute(f"PRAGMA table_info({tabla})").fetchall()
    }


def agregar_columnas_faltantes(conexion: sqlite3.Connection) -> bool:
    """Compatibilidad con bases antiguas: agrega claves sin perder filas."""
    existentes = columnas_tabla(conexion, "resultados_electorales")
    if not existentes:
        return False
    definiciones = {
        "distrito_clave": "TEXT",
        "distrito_original": "TEXT",
        "tipo_eleccion": "TEXT",
        "fuerza_clave": "TEXT",
        "fuerza_original": "TEXT",
        "estado_registro": "TEXT NOT NULL DEFAULT 'ok'",
        "fuente_huella": "TEXT",
        "creado_en": "TEXT",
    }
    modificado = False
    for columna, definicion in definiciones.items():
        if columna not in existentes:
            conexion.execute(
                f"ALTER TABLE resultados_electorales ADD COLUMN {columna} {definicion}"
            )
            modificado = True
    if modificado:
        filas = pd.read_sql_query("SELECT * FROM resultados_electorales", conexion)
        preparado = preparar_resultados(filas)
        for fila in preparado.itertuples(index=False):
            conexion.execute(
                """
                UPDATE resultados_electorales
                SET distrito = ?, distrito_clave = ?, distrito_original = ?,
                    tipo_eleccion = ?, fuerza_clave = ?, fuerza_original = ?,
                    estado_registro = COALESCE(estado_registro, 'ok'),
                    creado_en = COALESCE(creado_en, ?)
                WHERE anio = ? AND distrito = ? AND circuito = ?
                  AND cargo = ? AND fuerza = ?
                """,
                (
                    fila.distrito,
                    fila.distrito_clave,
                    fila.distrito_original,
                    fila.tipo_eleccion,
                    fila.fuerza_clave,
                    fila.fuerza_original,
                    fila.creado_en,
                    fila.anio,
                    fila.distrito,
                    fila.circuito,
                    fila.cargo,
                    fila.fuerza,
                ),
            )
    return modificado


def filas_auditoria_para_revision(
    conexion: sqlite3.Connection, limite: int = 500
) -> pd.DataFrame:
    crear_tabla_auditoria(conexion)
    return pd.read_sql_query(
        """
        SELECT anio, distrito, circuito, cargo, fuerza, votos_positivos,
               porcentaje, estado, detalle, requiere_revision, creado_en
        FROM registros_auditoria
        WHERE requiere_revision = 1
        ORDER BY creado_en DESC
        LIMIT ?
        """,
        conexion,
        params=(int(limite),),
    )


def resumen_auditoria(conexion: sqlite3.Connection) -> dict[str, int]:
    crear_tabla_auditoria(conexion)
    filas = conexion.execute(
        "SELECT estado, COUNT(*) FROM registros_auditoria GROUP BY estado"
    ).fetchall()
    resultado = {estado: 0 for estado in ESTADOS_AUDITORIA}
    resultado.update({texto(estado): int(cantidad) for estado, cantidad in filas})
    resultado["requiere_revision"] = int(
        conexion.execute(
            "SELECT COUNT(*) FROM registros_auditoria WHERE requiere_revision = 1"
        ).fetchone()[0]
    )
    return resultado
