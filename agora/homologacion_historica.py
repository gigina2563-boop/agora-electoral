"""Modelo multinivel de homologación histórica de fuerzas electorales.

La tabla histórica ``homologacion_fuerzas`` sigue existiendo por compatibilidad:
limpia nombres y aplica reglas simples por año. Este módulo agrega el modelo
que el observatorio necesita para comparar elecciones sin confundir una
coincidencia ortográfica con una continuidad política.

Niveles del modelo
------------------
- ``fuerzas_identidades``: identidad estable (partido, lista, alianza,
  coalición o bloque).
- ``fuerzas_denominaciones``: nombre electoral concreto, con su año, tipo de
  elección, cargo, distrito y nivel de análisis.
- ``fuerzas_relaciones``: continuidad, cambio de nombre, escisión, fusión o
  integración de alianza, siempre con fuente, confianza y estado.
- ``bloques_electorales`` y ``bloque_miembros``: agrupación documentada para
  el escenario de bloques.
- ``ausencias_verificadas``: ausencias que la fuente permite afirmar. Una
  ausencia no verificada no se completa con cero.

Regla de oro: ninguna fuerza se unifica por parecido de nombres. Solo se aplican
denominaciones y relaciones explícitas cargadas por la administradora.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import pandas as pd
import streamlit as st

from .auth import exigir_admin
from .config import (
    SEED_AUSENCIAS,
    SEED_BLOQUE_MIEMBROS,
    SEED_BLOQUES,
    SEED_DENOMINACIONES,
    SEED_IDENTIDADES,
    SEED_RELACIONES,
    get_db_path,
)
from .integridad import (
    ahora_utc,
    canonizar_distrito,
    clave_territorio,
    preparar_resultados,
    tipo_eleccion_desde_cargo,
)
from .utils import clave_fuerza, texto

NIVELES = ["partido", "lista", "alianza", "coalicion", "bloque"]
ESTADOS = ["validada", "provisoria", "pendiente"]
CONFIANZAS = ["alta", "media", "baja"]
TIPOS_RELACION = [
    "continuidad",
    "cambio_nombre",
    "escision",
    "fusion",
    "integracion_alianza",
    "composicion",
]
ESCENARIOS = {
    "estricto": "Comparación estricta (identidad y denominación)",
    "relaciones": "Relaciones históricas validadas",
    "bloques": "Bloques documentados validados",
}

COLUMNAS_IDENTIDADES = [
    "id",
    "nombre",
    "nivel",
    "tipo",
    "estado",
    "fuente",
    "confianza",
    "notas",
    "creado_en",
]
COLUMNAS_DENOMINACIONES = [
    "id",
    "identidad",
    "nombre_electoral",
    "nombre_clave",
    "anio_desde",
    "anio_hasta",
    "tipo_eleccion",
    "cargo",
    "cargo_clave",
    "distrito",
    "distrito_clave",
    "nivel",
    "estado",
    "fuente",
    "confianza",
    "notas",
    "creado_en",
]
COLUMNAS_RELACIONES = [
    "id",
    "origen",
    "destino",
    "tipo_relacion",
    "anio_desde",
    "anio_hasta",
    "cargo",
    "cargo_clave",
    "distrito",
    "distrito_clave",
    "nivel",
    "estado",
    "fuente",
    "confianza",
    "notas",
    "creado_en",
]
COLUMNAS_BLOQUES = [
    "id",
    "nombre",
    "nivel",
    "estado",
    "fuente",
    "confianza",
    "notas",
    "creado_en",
]
COLUMNAS_BLOQUE_MIEMBROS = [
    "id",
    "bloque",
    "identidad",
    "anio_desde",
    "anio_hasta",
    "estado",
    "fuente",
    "confianza",
    "notas",
    "creado_en",
]
COLUMNAS_AUSENCIAS = [
    "id",
    "anio",
    "cargo",
    "cargo_clave",
    "distrito",
    "distrito_clave",
    "identidad",
    "motivo",
    "estado",
    "fuente",
    "confianza",
    "notas",
    "creado_en",
]

COLUMNAS_SALIDA_HOMOLOGACION = [
    "fuerza_origen",
    "fuerza_denominacion",
    "fuerza",
    "fuerza_identidad",
    "fuerza_identidad_id",
    "nivel_analisis",
    "bloque",
    "estado_homologacion",
    "fuente_homologacion",
    "relacion_aplicada",
    "conflicto_homologacion",
]


@dataclass
class ModeloHistorico:
    identidades: pd.DataFrame
    denominaciones: pd.DataFrame
    relaciones: pd.DataFrame
    bloques: pd.DataFrame
    bloque_miembros: pd.DataFrame
    ausencias: pd.DataFrame

    @classmethod
    def vacio(cls) -> "ModeloHistorico":
        return cls(
            identidades=pd.DataFrame(columns=COLUMNAS_IDENTIDADES),
            denominaciones=pd.DataFrame(columns=COLUMNAS_DENOMINACIONES),
            relaciones=pd.DataFrame(columns=COLUMNAS_RELACIONES),
            bloques=pd.DataFrame(columns=COLUMNAS_BLOQUES),
            bloque_miembros=pd.DataFrame(columns=COLUMNAS_BLOQUE_MIEMBROS),
            ausencias=pd.DataFrame(columns=COLUMNAS_AUSENCIAS),
        )


def _conectar(ruta: Optional[str] = None) -> sqlite3.Connection:
    return sqlite3.connect(ruta or get_db_path())


def crear_tablas(conexion: sqlite3.Connection) -> None:
    """Crea el modelo histórico de forma idempotente."""
    conexion.execute(
        """
        CREATE TABLE IF NOT EXISTS fuerzas_identidades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL UNIQUE,
            nivel TEXT NOT NULL DEFAULT 'partido',
            tipo TEXT,
            estado TEXT NOT NULL DEFAULT 'pendiente',
            fuente TEXT,
            confianza TEXT,
            notas TEXT,
            creado_en TEXT NOT NULL
        )
        """
    )
    conexion.execute(
        """
        CREATE TABLE IF NOT EXISTS fuerzas_denominaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identidad_id INTEGER NOT NULL,
            nombre_electoral TEXT NOT NULL,
            nombre_clave TEXT NOT NULL,
            anio_desde INTEGER,
            anio_hasta INTEGER,
            tipo_eleccion TEXT,
            cargo TEXT,
            cargo_clave TEXT,
            distrito TEXT,
            distrito_clave TEXT,
            nivel TEXT NOT NULL DEFAULT 'partido',
            estado TEXT NOT NULL DEFAULT 'pendiente',
            fuente TEXT,
            confianza TEXT,
            notas TEXT,
            creado_en TEXT NOT NULL,
            FOREIGN KEY(identidad_id) REFERENCES fuerzas_identidades(id)
        )
        """
    )
    conexion.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_denom_contexto
        ON fuerzas_denominaciones (
            nombre_clave,
            IFNULL(anio_desde, -9999),
            IFNULL(anio_hasta, 9999),
            IFNULL(tipo_eleccion, ''),
            IFNULL(cargo_clave, ''),
            IFNULL(distrito_clave, ''),
            nivel
        )
        """
    )
    conexion.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_denom_busqueda
        ON fuerzas_denominaciones (nombre_clave, anio_desde, anio_hasta)
        """
    )
    conexion.execute(
        """
        CREATE TABLE IF NOT EXISTS fuerzas_relaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origen_id INTEGER NOT NULL,
            destino_id INTEGER NOT NULL,
            tipo_relacion TEXT NOT NULL,
            anio_desde INTEGER,
            anio_hasta INTEGER,
            cargo TEXT,
            cargo_clave TEXT,
            distrito TEXT,
            distrito_clave TEXT,
            nivel TEXT,
            estado TEXT NOT NULL DEFAULT 'pendiente',
            fuente TEXT,
            confianza TEXT,
            notas TEXT,
            creado_en TEXT NOT NULL,
            FOREIGN KEY(origen_id) REFERENCES fuerzas_identidades(id),
            FOREIGN KEY(destino_id) REFERENCES fuerzas_identidades(id)
        )
        """
    )
    conexion.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_relacion_contexto
        ON fuerzas_relaciones (
            origen_id,
            destino_id,
            tipo_relacion,
            IFNULL(anio_desde, -9999),
            IFNULL(anio_hasta, 9999),
            IFNULL(cargo_clave, ''),
            IFNULL(distrito_clave, ''),
            IFNULL(nivel, '')
        )
        """
    )
    conexion.execute(
        """
        CREATE TABLE IF NOT EXISTS bloques_electorales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL UNIQUE,
            nivel TEXT NOT NULL DEFAULT 'bloque',
            estado TEXT NOT NULL DEFAULT 'pendiente',
            fuente TEXT,
            confianza TEXT,
            notas TEXT,
            creado_en TEXT NOT NULL
        )
        """
    )
    conexion.execute(
        """
        CREATE TABLE IF NOT EXISTS bloque_miembros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bloque_id INTEGER NOT NULL,
            identidad_id INTEGER NOT NULL,
            anio_desde INTEGER,
            anio_hasta INTEGER,
            estado TEXT NOT NULL DEFAULT 'pendiente',
            fuente TEXT,
            confianza TEXT,
            notas TEXT,
            creado_en TEXT NOT NULL,
            FOREIGN KEY(bloque_id) REFERENCES bloques_electorales(id),
            FOREIGN KEY(identidad_id) REFERENCES fuerzas_identidades(id)
        )
        """
    )
    conexion.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_bloque_miembro_contexto
        ON bloque_miembros (
            bloque_id,
            identidad_id,
            IFNULL(anio_desde, -9999),
            IFNULL(anio_hasta, 9999)
        )
        """
    )
    conexion.execute(
        """
        CREATE TABLE IF NOT EXISTS ausencias_verificadas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anio INTEGER NOT NULL,
            cargo TEXT NOT NULL,
            cargo_clave TEXT NOT NULL,
            distrito TEXT NOT NULL,
            distrito_clave TEXT NOT NULL,
            identidad_id INTEGER NOT NULL,
            motivo TEXT,
            estado TEXT NOT NULL DEFAULT 'pendiente',
            fuente TEXT,
            confianza TEXT,
            notas TEXT,
            creado_en TEXT NOT NULL,
            FOREIGN KEY(identidad_id) REFERENCES fuerzas_identidades(id)
        )
        """
    )
    conexion.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_ausencia_contexto
        ON ausencias_verificadas (anio, cargo_clave, distrito_clave, identidad_id)
        """
    )


# ---------------------------------------------------------------------------
# Lectura y limpieza
# ---------------------------------------------------------------------------


def _leer_seed(ruta: str, columnas: Iterable[str]) -> pd.DataFrame:
    columnas = list(columnas)
    if not os.path.exists(ruta):
        return pd.DataFrame(columns=columnas)
    try:
        tabla = pd.read_csv(ruta, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=columnas)
    return tabla.reindex(columns=columnas).fillna("")


def _entero_opcional(valor: Any) -> Any:
    if valor is None or texto(valor) == "":
        return None
    try:
        return int(float(str(valor).replace(",", ".")))
    except (TypeError, ValueError):
        return None


def _booleano(valor: Any) -> bool:
    return texto(valor).casefold() in {"1", "true", "si", "sí", "yes"}


def limpiar_identidades(datos: pd.DataFrame) -> pd.DataFrame:
    if datos is None or datos.empty:
        return pd.DataFrame(columns=[c for c in COLUMNAS_IDENTIDADES if c != "id"])
    tabla = datos.copy().reindex(
        columns=[c for c in COLUMNAS_IDENTIDADES if c != "id"]
    )
    for columna in ("nombre", "nivel", "tipo", "estado", "fuente", "confianza", "notas"):
        tabla[columna] = tabla[columna].fillna("").astype(str).str.strip()
    tabla = tabla[tabla["nombre"].ne("")]
    tabla["nivel"] = tabla["nivel"].str.casefold().map(
        lambda valor: valor if valor in NIVELES else "partido"
    )
    tabla["estado"] = tabla["estado"].str.casefold().map(
        lambda valor: valor if valor in ESTADOS else "pendiente"
    )
    tabla["confianza"] = tabla["confianza"].str.casefold().map(
        lambda valor: valor if valor in CONFIANZAS else "baja"
    )
    tabla = tabla.drop_duplicates(subset=["nombre"], keep="last").reset_index(drop=True)
    tabla["creado_en"] = ahora_utc()
    return tabla


def limpiar_denominaciones(
    datos: pd.DataFrame,
    identidades: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    columnas = [c for c in COLUMNAS_DENOMINACIONES if c != "id"]
    if datos is None or datos.empty:
        return pd.DataFrame(columns=columnas)
    tabla = datos.copy().reindex(columns=columnas)
    for columna in (
        "identidad",
        "nombre_electoral",
        "tipo_eleccion",
        "cargo",
        "distrito",
        "nivel",
        "estado",
        "fuente",
        "confianza",
        "notas",
    ):
        tabla[columna] = tabla[columna].fillna("").astype(str).str.strip()
    tabla = tabla[
        tabla["identidad"].ne("") & tabla["nombre_electoral"].ne("")
    ].copy()
    tabla["nombre_clave"] = tabla["nombre_electoral"].map(clave_fuerza)
    tabla["cargo_clave"] = tabla["cargo"].map(clave_fuerza)
    tabla["distrito_clave"] = [
        clave_territorio(clave or distrito)
        for clave, distrito in zip(tabla["distrito_clave"], tabla["distrito"])
    ]
    tabla["distrito"] = [
        canonizar_distrito(distrito) if texto(distrito) else ""
        for distrito in tabla["distrito"]
    ]
    tabla["anio_desde"] = tabla["anio_desde"].map(_entero_opcional)
    tabla["anio_hasta"] = tabla["anio_hasta"].map(_entero_opcional)
    for indice, fila in tabla.iterrows():
        desde, hasta = fila["anio_desde"], fila["anio_hasta"]
        if desde is not None and hasta is not None and desde > hasta:
            tabla.at[indice, "anio_desde"], tabla.at[indice, "anio_hasta"] = hasta, desde
    tabla["nivel"] = tabla["nivel"].str.casefold().map(
        lambda valor: valor if valor in NIVELES else "partido"
    )
    tabla["estado"] = tabla["estado"].str.casefold().map(
        lambda valor: valor if valor in ESTADOS else "pendiente"
    )
    tabla["confianza"] = tabla["confianza"].str.casefold().map(
        lambda valor: valor if valor in CONFIANZAS else "baja"
    )
    if identidades is not None and not identidades.empty:
        niveles = dict(
            zip(
                identidades["nombre"].astype(str),
                identidades["nivel"].astype(str),
            )
        )
        tabla["nivel"] = [
            niveles.get(identidad, nivel)
            for identidad, nivel in zip(tabla["identidad"], tabla["nivel"])
        ]
    tabla = tabla.drop_duplicates(
        subset=[
            "nombre_clave",
            "anio_desde",
            "anio_hasta",
            "tipo_eleccion",
            "cargo_clave",
            "distrito_clave",
            "nivel",
        ],
        keep="last",
    ).reset_index(drop=True)
    tabla["creado_en"] = ahora_utc()
    return tabla


def limpiar_relaciones(
    datos: pd.DataFrame,
    identidades: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    columnas = [c for c in COLUMNAS_RELACIONES if c != "id"]
    if datos is None or datos.empty:
        return pd.DataFrame(columns=columnas)
    tabla = datos.copy().reindex(columns=columnas)
    for columna in (
        "origen",
        "destino",
        "tipo_relacion",
        "cargo",
        "distrito",
        "nivel",
        "estado",
        "fuente",
        "confianza",
        "notas",
    ):
        tabla[columna] = tabla[columna].fillna("").astype(str).str.strip()
    tabla = tabla[tabla["origen"].ne("") & tabla["destino"].ne("")].copy()
    tabla = tabla[tabla["origen"].ne(tabla["destino"])].copy()
    tabla["tipo_relacion"] = tabla["tipo_relacion"].str.casefold().map(
        lambda valor: valor if valor in TIPOS_RELACION else "continuidad"
    )
    tabla["cargo_clave"] = tabla["cargo"].map(clave_fuerza)
    tabla["distrito_clave"] = [
        clave_territorio(clave or distrito)
        for clave, distrito in zip(tabla["distrito_clave"], tabla["distrito"])
    ]
    tabla["distrito"] = [
        canonizar_distrito(distrito) if texto(distrito) else ""
        for distrito in tabla["distrito"]
    ]
    tabla["anio_desde"] = tabla["anio_desde"].map(_entero_opcional)
    tabla["anio_hasta"] = tabla["anio_hasta"].map(_entero_opcional)
    tabla["nivel"] = tabla["nivel"].str.casefold().map(
        lambda valor: valor if valor in NIVELES else ""
    )
    tabla["estado"] = tabla["estado"].str.casefold().map(
        lambda valor: valor if valor in ESTADOS else "pendiente"
    )
    tabla["confianza"] = tabla["confianza"].str.casefold().map(
        lambda valor: valor if valor in CONFIANZAS else "baja"
    )
    if identidades is not None and not identidades.empty:
        nombres = set(identidades["nombre"].astype(str))
        tabla = tabla[
            tabla["origen"].isin(nombres) & tabla["destino"].isin(nombres)
        ].copy()
    tabla = tabla.drop_duplicates(
        subset=[
            "origen",
            "destino",
            "tipo_relacion",
            "anio_desde",
            "anio_hasta",
            "cargo_clave",
            "distrito_clave",
            "nivel",
        ],
        keep="last",
    ).reset_index(drop=True)
    tabla["creado_en"] = ahora_utc()
    return tabla


def limpiar_bloques(datos: pd.DataFrame) -> pd.DataFrame:
    columnas = [c for c in COLUMNAS_BLOQUES if c != "id"]
    if datos is None or datos.empty:
        return pd.DataFrame(columns=columnas)
    tabla = datos.copy().reindex(columns=columnas)
    for columna in ("nombre", "nivel", "estado", "fuente", "confianza", "notas"):
        tabla[columna] = tabla[columna].fillna("").astype(str).str.strip()
    tabla = tabla[tabla["nombre"].ne("")].copy()
    tabla["nivel"] = tabla["nivel"].str.casefold().map(
        lambda valor: valor if valor in NIVELES else "bloque"
    )
    tabla["estado"] = tabla["estado"].str.casefold().map(
        lambda valor: valor if valor in ESTADOS else "pendiente"
    )
    tabla["confianza"] = tabla["confianza"].str.casefold().map(
        lambda valor: valor if valor in CONFIANZAS else "baja"
    )
    tabla = tabla.drop_duplicates(subset=["nombre"], keep="last").reset_index(drop=True)
    tabla["creado_en"] = ahora_utc()
    return tabla


def limpiar_bloque_miembros(
    datos: pd.DataFrame,
    identidades: Optional[pd.DataFrame] = None,
    bloques: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    columnas = [c for c in COLUMNAS_BLOQUE_MIEMBROS if c != "id"]
    if datos is None or datos.empty:
        return pd.DataFrame(columns=columnas)
    tabla = datos.copy().reindex(columns=columnas)
    for columna in (
        "bloque",
        "identidad",
        "estado",
        "fuente",
        "confianza",
        "notas",
    ):
        tabla[columna] = tabla[columna].fillna("").astype(str).str.strip()
    tabla = tabla[tabla["bloque"].ne("") & tabla["identidad"].ne("")].copy()
    tabla["anio_desde"] = tabla["anio_desde"].map(_entero_opcional)
    tabla["anio_hasta"] = tabla["anio_hasta"].map(_entero_opcional)
    tabla["estado"] = tabla["estado"].str.casefold().map(
        lambda valor: valor if valor in ESTADOS else "pendiente"
    )
    tabla["confianza"] = tabla["confianza"].str.casefold().map(
        lambda valor: valor if valor in CONFIANZAS else "baja"
    )
    if identidades is not None and not identidades.empty:
        nombres = set(identidades["nombre"].astype(str))
        tabla = tabla[tabla["identidad"].isin(nombres)].copy()
    if bloques is not None and not bloques.empty:
        nombres = set(bloques["nombre"].astype(str))
        tabla = tabla[tabla["bloque"].isin(nombres)].copy()
    tabla = tabla.drop_duplicates(
        subset=["bloque", "identidad", "anio_desde", "anio_hasta"],
        keep="last",
    ).reset_index(drop=True)
    tabla["creado_en"] = ahora_utc()
    return tabla


def limpiar_ausencias(
    datos: pd.DataFrame,
    identidades: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    columnas = [c for c in COLUMNAS_AUSENCIAS if c != "id"]
    if datos is None or datos.empty:
        return pd.DataFrame(columns=columnas)
    tabla = datos.copy().reindex(columns=columnas)
    for columna in (
        "cargo",
        "distrito",
        "identidad",
        "motivo",
        "estado",
        "fuente",
        "confianza",
        "notas",
    ):
        tabla[columna] = tabla[columna].fillna("").astype(str).str.strip()
    tabla = tabla[
        tabla["identidad"].ne("") & tabla["cargo"].ne("") & tabla["distrito"].ne("")
    ].copy()
    tabla["anio"] = pd.to_numeric(tabla["anio"], errors="coerce")
    tabla = tabla.dropna(subset=["anio"]).copy()
    tabla["anio"] = tabla["anio"].astype(int)
    tabla["cargo_clave"] = tabla["cargo"].map(clave_fuerza)
    tabla["distrito_clave"] = tabla["distrito"].map(clave_territorio)
    tabla["distrito"] = tabla["distrito"].map(canonizar_distrito)
    tabla["estado"] = tabla["estado"].str.casefold().map(
        lambda valor: valor if valor in ESTADOS else "pendiente"
    )
    tabla["confianza"] = tabla["confianza"].str.casefold().map(
        lambda valor: valor if valor in CONFIANZAS else "baja"
    )
    if identidades is not None and not identidades.empty:
        nombres = set(identidades["nombre"].astype(str))
        tabla = tabla[tabla["identidad"].isin(nombres)].copy()
    tabla = tabla.drop_duplicates(
        subset=["anio", "cargo_clave", "distrito_clave", "identidad"],
        keep="last",
    ).reset_index(drop=True)
    tabla["creado_en"] = ahora_utc()
    return tabla


def _rango_solapa(
    desde_a: Any, hasta_a: Any, desde_b: Any, hasta_b: Any
) -> bool:
    a1 = -10_000 if _entero_opcional(desde_a) is None else int(desde_a)
    a2 = 10_000 if _entero_opcional(hasta_a) is None else int(hasta_a)
    b1 = -10_000 if _entero_opcional(desde_b) is None else int(desde_b)
    b2 = 10_000 if _entero_opcional(hasta_b) is None else int(hasta_b)
    return a1 <= b2 and b1 <= a2


def validar_denominaciones(datos: pd.DataFrame) -> list[str]:
    """Devuelve conflictos de reglas históricas sin aplicar el cambio."""
    if datos is None or datos.empty:
        return []
    conflictos: list[str] = []
    filas = datos.reset_index(drop=True)
    for indice_a in range(len(filas)):
        fila_a = filas.iloc[indice_a]
        for indice_b in range(indice_a + 1, len(filas)):
            fila_b = filas.iloc[indice_b]
            if texto(fila_a["nombre_clave"]) != texto(fila_b["nombre_clave"]):
                continue
            if texto(fila_a["identidad"]) == texto(fila_b["identidad"]):
                continue
            contexto_a = (
                texto(fila_a.get("tipo_eleccion")),
                texto(fila_a.get("cargo_clave")),
                texto(fila_a.get("distrito_clave")),
                texto(fila_a.get("nivel")),
            )
            contexto_b = (
                texto(fila_b.get("tipo_eleccion")),
                texto(fila_b.get("cargo_clave")),
                texto(fila_b.get("distrito_clave")),
                texto(fila_b.get("nivel")),
            )
            if contexto_a != contexto_b:
                continue
            if _rango_solapa(
                fila_a.get("anio_desde"),
                fila_a.get("anio_hasta"),
                fila_b.get("anio_desde"),
                fila_b.get("anio_hasta"),
            ):
                conflictos.append(
                    "La denominación "
                    f"'{texto(fila_a['nombre_electoral'])}' apunta a "
                    f"'{texto(fila_a['identidad'])}' y a "
                    f"'{texto(fila_b['identidad'])}' en contextos superpuestos."
                )
    return conflictos


def validar_relaciones(datos: pd.DataFrame) -> list[str]:
    """Detecta destinos o tipos contradictorios para un mismo origen."""
    if datos is None or datos.empty:
        return []
    conflictos: list[str] = []
    filas = datos.reset_index(drop=True)
    for indice_a in range(len(filas)):
        fila_a = filas.iloc[indice_a]
        for indice_b in range(indice_a + 1, len(filas)):
            fila_b = filas.iloc[indice_b]
            if texto(fila_a.get("origen")) != texto(fila_b.get("origen")):
                continue
            contexto_a = (
                texto(fila_a.get("cargo_clave")),
                texto(fila_a.get("distrito_clave")),
                texto(fila_a.get("nivel")),
            )
            contexto_b = (
                texto(fila_b.get("cargo_clave")),
                texto(fila_b.get("distrito_clave")),
                texto(fila_b.get("nivel")),
            )
            if contexto_a != contexto_b:
                continue
            if not _rango_solapa(
                fila_a.get("anio_desde"),
                fila_a.get("anio_hasta"),
                fila_b.get("anio_desde"),
                fila_b.get("anio_hasta"),
            ):
                continue
            tipo_a = texto(fila_a.get("tipo_relacion"))
            tipo_b = texto(fila_b.get("tipo_relacion"))
            # Una alianza puede declarar varios componentes en el mismo año.
            # La composición no se sigue para reemplazar identidades, pero
            # tampoco es un destino contradictorio.
            if tipo_a == "composicion" and tipo_b == "composicion":
                continue
            mismo_destino = texto(fila_a.get("destino")) == texto(
                fila_b.get("destino")
            )
            mismo_tipo = tipo_a == tipo_b
            if not mismo_destino or not mismo_tipo:
                conflictos.append(
                    "La relación de "
                    f"'{texto(fila_a.get('origen'))}' apunta a "
                    f"'{texto(fila_a.get('destino'))}' "
                    f"({texto(fila_a.get('tipo_relacion'))}) y a "
                    f"'{texto(fila_b.get('destino'))}' "
                    f"({texto(fila_b.get('tipo_relacion'))}) "
                    "en contextos superpuestos."
                )
    return conflictos


def validar_bloques_miembros(datos: pd.DataFrame) -> list[str]:
    """Detecta una identidad incluida en dos bloques superpuestos."""
    if datos is None or datos.empty:
        return []
    conflictos: list[str] = []
    filas = datos.reset_index(drop=True)
    for indice_a in range(len(filas)):
        fila_a = filas.iloc[indice_a]
        for indice_b in range(indice_a + 1, len(filas)):
            fila_b = filas.iloc[indice_b]
            if texto(fila_a.get("identidad")) != texto(fila_b.get("identidad")):
                continue
            if texto(fila_a.get("bloque")) == texto(fila_b.get("bloque")):
                continue
            if _rango_solapa(
                fila_a.get("anio_desde"),
                fila_a.get("anio_hasta"),
                fila_b.get("anio_desde"),
                fila_b.get("anio_hasta"),
            ):
                conflictos.append(
                    f"La identidad '{texto(fila_a.get('identidad'))}' "
                    f"pertenece a '{texto(fila_a.get('bloque'))}' y a "
                    f"'{texto(fila_b.get('bloque'))}' en años superpuestos."
                )
    return conflictos


# ---------------------------------------------------------------------------
# Siembra e importación heredada
# ---------------------------------------------------------------------------


def _id_identidad(conexion: sqlite3.Connection, nombre: str) -> Optional[int]:
    fila = conexion.execute(
        "SELECT id FROM fuerzas_identidades WHERE nombre = ?", (texto(nombre),)
    ).fetchone()
    return int(fila[0]) if fila else None


def _insertar_identidades(
    conexion: sqlite3.Connection, identidades: pd.DataFrame
) -> int:
    insertadas = 0
    for fila in identidades.itertuples(index=False):
        conexion.execute(
            """
            INSERT OR IGNORE INTO fuerzas_identidades
                (nombre, nivel, tipo, estado, fuente, confianza, notas, creado_en)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fila.nombre,
                fila.nivel,
                fila.tipo,
                fila.estado,
                fila.fuente,
                fila.confianza,
                fila.notas,
                fila.creado_en,
            ),
        )
        insertadas += conexion.execute("SELECT changes()").fetchone()[0]
    return insertadas


def _insertar_denominaciones(
    conexion: sqlite3.Connection, denominaciones: pd.DataFrame
) -> int:
    insertadas = 0
    for fila in denominaciones.itertuples(index=False):
        identidad_id = _id_identidad(conexion, fila.identidad)
        if identidad_id is None:
            continue
        conexion.execute(
            """
            INSERT OR IGNORE INTO fuerzas_denominaciones
                (identidad_id, nombre_electoral, nombre_clave, anio_desde,
                 anio_hasta, tipo_eleccion, cargo, cargo_clave, distrito,
                 distrito_clave, nivel, estado, fuente, confianza, notas,
                 creado_en)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identidad_id,
                fila.nombre_electoral,
                fila.nombre_clave,
                fila.anio_desde,
                fila.anio_hasta,
                fila.tipo_eleccion,
                fila.cargo,
                fila.cargo_clave,
                fila.distrito,
                fila.distrito_clave,
                fila.nivel,
                fila.estado,
                fila.fuente,
                fila.confianza,
                fila.notas,
                fila.creado_en,
            ),
        )
        insertadas += conexion.execute("SELECT changes()").fetchone()[0]
    return insertadas


def _insertar_relaciones(
    conexion: sqlite3.Connection, relaciones: pd.DataFrame
) -> int:
    insertadas = 0
    for fila in relaciones.itertuples(index=False):
        origen_id = _id_identidad(conexion, fila.origen)
        destino_id = _id_identidad(conexion, fila.destino)
        if origen_id is None or destino_id is None:
            continue
        conexion.execute(
            """
            INSERT OR IGNORE INTO fuerzas_relaciones
                (origen_id, destino_id, tipo_relacion, anio_desde,
                 anio_hasta, cargo, cargo_clave, distrito, distrito_clave,
                 nivel, estado, fuente, confianza, notas, creado_en)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                origen_id,
                destino_id,
                fila.tipo_relacion,
                fila.anio_desde,
                fila.anio_hasta,
                fila.cargo,
                fila.cargo_clave,
                fila.distrito,
                fila.distrito_clave,
                fila.nivel,
                fila.estado,
                fila.fuente,
                fila.confianza,
                fila.notas,
                fila.creado_en,
            ),
        )
        insertadas += conexion.execute("SELECT changes()").fetchone()[0]
    return insertadas


def _insertar_bloques(conexion: sqlite3.Connection, bloques: pd.DataFrame) -> int:
    insertadas = 0
    for fila in bloques.itertuples(index=False):
        conexion.execute(
            """
            INSERT OR IGNORE INTO bloques_electorales
                (nombre, nivel, estado, fuente, confianza, notas, creado_en)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fila.nombre,
                fila.nivel,
                fila.estado,
                fila.fuente,
                fila.confianza,
                fila.notas,
                fila.creado_en,
            ),
        )
        insertadas += conexion.execute("SELECT changes()").fetchone()[0]
    return insertadas


def _insertar_bloque_miembros(
    conexion: sqlite3.Connection, miembros: pd.DataFrame
) -> int:
    insertadas = 0
    for fila in miembros.itertuples(index=False):
        bloque = conexion.execute(
            "SELECT id FROM bloques_electorales WHERE nombre = ?", (fila.bloque,)
        ).fetchone()
        identidad_id = _id_identidad(conexion, fila.identidad)
        if bloque is None or identidad_id is None:
            continue
        conexion.execute(
            """
            INSERT OR IGNORE INTO bloque_miembros
                (bloque_id, identidad_id, anio_desde, anio_hasta, estado,
                 fuente, confianza, notas, creado_en)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(bloque[0]),
                identidad_id,
                fila.anio_desde,
                fila.anio_hasta,
                fila.estado,
                fila.fuente,
                fila.confianza,
                fila.notas,
                fila.creado_en,
            ),
        )
        insertadas += conexion.execute("SELECT changes()").fetchone()[0]
    return insertadas


def _insertar_ausencias(
    conexion: sqlite3.Connection, ausencias: pd.DataFrame
) -> int:
    insertadas = 0
    for fila in ausencias.itertuples(index=False):
        identidad_id = _id_identidad(conexion, fila.identidad)
        if identidad_id is None:
            continue
        conexion.execute(
            """
            INSERT OR IGNORE INTO ausencias_verificadas
                (anio, cargo, cargo_clave, distrito, distrito_clave,
                 identidad_id, motivo, estado, fuente, confianza, notas,
                 creado_en)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fila.anio,
                fila.cargo,
                fila.cargo_clave,
                fila.distrito,
                fila.distrito_clave,
                identidad_id,
                fila.motivo,
                fila.estado,
                fila.fuente,
                fila.confianza,
                fila.notas,
                fila.creado_en,
            ),
        )
        insertadas += conexion.execute("SELECT changes()").fetchone()[0]
    return insertadas


def _importar_homologacion_heredada(conexion: sqlite3.Connection) -> int:
    """Incorpora reglas heredadas que no estén cubiertas por la semilla.

    No se reemplaza la tabla antigua: se conserva tal cual para compatibilidad.
    """
    tablas = {
        fila[0]
        for fila in conexion.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "homologacion_fuerzas" not in tablas:
        return 0
    filas = conexion.execute(
        "SELECT fuerza_origen, fuerza_homologada, tipo, anio_desde, anio_hasta, notas "
        "FROM homologacion_fuerzas"
    ).fetchall()
    if not filas:
        return 0
    ya_cubiertas = {
        texto(fila[0])
        for fila in conexion.execute(
            "SELECT nombre_clave FROM fuerzas_denominaciones"
        ).fetchall()
    }
    importadas = 0
    for origen, destino, tipo, desde, hasta, notas in filas:
        origen = texto(origen)
        destino = texto(destino)
        if not origen or not destino:
            continue
        if clave_fuerza(origen) in ya_cubiertas:
            continue
        identidad_id = _id_identidad(conexion, destino)
        if identidad_id is None:
            conexion.execute(
                """
                INSERT OR IGNORE INTO fuerzas_identidades
                    (nombre, nivel, tipo, estado, fuente, confianza, notas, creado_en)
                VALUES (?, ?, ?, 'provisoria', ?, 'baja', ?, ?)
                """,
                (
                    destino,
                    "partido" if texto(tipo).casefold() == "partido" else "alianza",
                    texto(tipo),
                    "Regla heredada de homologacion_fuerzas",
                    "Importada sin fuente primaria. Revisar antes de publicar.",
                    ahora_utc(),
                ),
            )
            identidad_id = _id_identidad(conexion, destino)
        if identidad_id is None:
            continue
        conexion.execute(
            """
            INSERT OR IGNORE INTO fuerzas_denominaciones
                (identidad_id, nombre_electoral, nombre_clave, anio_desde,
                 anio_hasta, tipo_eleccion, cargo, cargo_clave, distrito,
                 distrito_clave, nivel, estado, fuente, confianza, notas,
                 creado_en)
            VALUES (?, ?, ?, ?, ?, '', '', '', '', '', ?, 'provisoria',
                    'Regla heredada de homologacion_fuerzas', 'baja', ?, ?)
            """,
            (
                identidad_id,
                origen,
                clave_fuerza(origen),
                _entero_opcional(desde),
                _entero_opcional(hasta),
                "partido" if texto(tipo).casefold() == "partido" else "alianza",
                texto(notas),
                ahora_utc(),
            ),
        )
        importadas += conexion.execute("SELECT changes()").fetchone()[0]
    return importadas


def sembrar_modelo_historico(conexion: sqlite3.Connection) -> dict[str, int]:
    """Carga las semillas de forma idempotente.

    No depende de que las tablas estén vacías: usa ``INSERT OR IGNORE`` para
    agregar denominaciones o relaciones nuevas sin pisar decisiones tomadas en
    administración.
    """
    crear_tablas(conexion)
    identidades = limpiar_identidades(
        _leer_seed(
            SEED_IDENTIDADES,
            [
                "nombre",
                "nivel",
                "tipo",
                "estado",
                "fuente",
                "confianza",
                "notas",
            ],
        )
    )
    creadas = _insertar_identidades(conexion, identidades)

    identidades_db = pd.read_sql_query(
        "SELECT id, nombre, nivel FROM fuerzas_identidades", conexion
    )
    denominaciones = limpiar_denominaciones(
        _leer_seed(
            SEED_DENOMINACIONES,
            [
                "nombre_electoral",
                "identidad",
                "anio_desde",
                "anio_hasta",
                "tipo_eleccion",
                "cargo",
                "distrito",
                "nivel",
                "estado",
                "fuente",
                "confianza",
                "notas",
            ],
        ),
        identidades_db,
    )
    denominaciones_creadas = _insertar_denominaciones(conexion, denominaciones)

    relaciones = limpiar_relaciones(
        _leer_seed(
            SEED_RELACIONES,
            [
                "origen",
                "destino",
                "tipo_relacion",
                "anio_desde",
                "anio_hasta",
                "cargo",
                "distrito",
                "nivel",
                "estado",
                "fuente",
                "confianza",
                "notas",
            ],
        ),
        identidades_db,
    )
    relaciones_creadas = _insertar_relaciones(conexion, relaciones)

    bloques = limpiar_bloques(
        _leer_seed(
            SEED_BLOQUES,
            ["nombre", "nivel", "estado", "fuente", "confianza", "notas"],
        )
    )
    bloques_creados = _insertar_bloques(conexion, bloques)

    bloques_db = pd.read_sql_query(
        "SELECT id, nombre FROM bloques_electorales", conexion
    )
    miembros = limpiar_bloque_miembros(
        _leer_seed(
            SEED_BLOQUE_MIEMBROS,
            [
                "bloque",
                "identidad",
                "anio_desde",
                "anio_hasta",
                "estado",
                "fuente",
                "confianza",
                "notas",
            ],
        ),
        identidades_db,
        bloques_db,
    )
    miembros_creados = _insertar_bloque_miembros(conexion, miembros)

    ausencias = limpiar_ausencias(
        _leer_seed(
            SEED_AUSENCIAS,
            [
                "anio",
                "cargo",
                "distrito",
                "identidad",
                "motivo",
                "estado",
                "fuente",
                "confianza",
                "notas",
            ],
        ),
        identidades_db,
    )
    ausencias_creadas = _insertar_ausencias(conexion, ausencias)

    heredadas = _importar_homologacion_heredada(conexion)
    return {
        "identidades": creadas,
        "denominaciones": denominaciones_creadas,
        "relaciones": relaciones_creadas,
        "bloques": bloques_creados,
        "bloque_miembros": miembros_creados,
        "ausencias": ausencias_creadas,
        "heredadas": heredadas,
    }


# ---------------------------------------------------------------------------
# Carga del modelo
# ---------------------------------------------------------------------------


def _leer_tabla(
    conexion: sqlite3.Connection, consulta: str, columnas: Iterable[str]
) -> pd.DataFrame:
    columnas = list(columnas)
    try:
        tabla = pd.read_sql_query(consulta, conexion)
    except (pd.errors.DatabaseError, sqlite3.OperationalError):
        return pd.DataFrame(columns=columnas)
    if tabla.empty:
        return pd.DataFrame(columns=columnas)
    return tabla.reindex(columns=columnas).fillna("")


def cargar_modelo_historico(ruta: Optional[str] = None) -> ModeloHistorico:
    with closing(_conectar(ruta)) as conexion:
        identidades = _leer_tabla(
            conexion,
            "SELECT id, nombre, nivel, tipo, estado, fuente, confianza, notas, "
            "creado_en FROM fuerzas_identidades ORDER BY nombre",
            COLUMNAS_IDENTIDADES,
        )
        denominaciones = _leer_tabla(
            conexion,
            """
            SELECT d.id, i.nombre AS identidad, d.nombre_electoral,
                   d.nombre_clave, d.anio_desde, d.anio_hasta,
                   d.tipo_eleccion, d.cargo, d.cargo_clave, d.distrito,
                   d.distrito_clave, d.nivel, d.estado, d.fuente,
                   d.confianza, d.notas, d.creado_en
            FROM fuerzas_denominaciones d
            JOIN fuerzas_identidades i ON i.id = d.identidad_id
            ORDER BY i.nombre, d.anio_desde, d.nombre_electoral
            """,
            COLUMNAS_DENOMINACIONES,
        )
        relaciones = _leer_tabla(
            conexion,
            """
            SELECT r.id, o.nombre AS origen, d.nombre AS destino,
                   r.tipo_relacion, r.anio_desde, r.anio_hasta, r.cargo,
                   r.cargo_clave, r.distrito, r.distrito_clave, r.nivel,
                   r.estado, r.fuente, r.confianza, r.notas, r.creado_en
            FROM fuerzas_relaciones r
            JOIN fuerzas_identidades o ON o.id = r.origen_id
            JOIN fuerzas_identidades d ON d.id = r.destino_id
            ORDER BY o.nombre, d.nombre
            """,
            COLUMNAS_RELACIONES,
        )
        bloques = _leer_tabla(
            conexion,
            "SELECT id, nombre, nivel, estado, fuente, confianza, notas, creado_en "
            "FROM bloques_electorales ORDER BY nombre",
            COLUMNAS_BLOQUES,
        )
        miembros = _leer_tabla(
            conexion,
            """
            SELECT m.id, b.nombre AS bloque, i.nombre AS identidad,
                   m.anio_desde, m.anio_hasta, m.estado, m.fuente,
                   m.confianza, m.notas, m.creado_en
            FROM bloque_miembros m
            JOIN bloques_electorales b ON b.id = m.bloque_id
            JOIN fuerzas_identidades i ON i.id = m.identidad_id
            ORDER BY b.nombre, i.nombre
            """,
            COLUMNAS_BLOQUE_MIEMBROS,
        )
        ausencias = _leer_tabla(
            conexion,
            """
            SELECT a.id, a.anio, a.cargo, a.cargo_clave, a.distrito,
                   a.distrito_clave, i.nombre AS identidad, a.motivo,
                   a.estado, a.fuente, a.confianza, a.notas, a.creado_en
            FROM ausencias_verificadas a
            JOIN fuerzas_identidades i ON i.id = a.identidad_id
            ORDER BY a.anio, a.cargo, a.distrito, i.nombre
            """,
            COLUMNAS_AUSENCIAS,
        )
    return ModeloHistorico(
        identidades=identidades,
        denominaciones=denominaciones,
        relaciones=relaciones,
        bloques=bloques,
        bloque_miembros=miembros,
        ausencias=ausencias,
    )


# ---------------------------------------------------------------------------
# Aplicación de reglas
# ---------------------------------------------------------------------------


def _cumple_rango(valor: int, desde: Any, hasta: Any) -> bool:
    if pd.notna(desde) and texto(desde) != "" and valor < int(desde):
        return False
    if pd.notna(hasta) and texto(hasta) != "" and valor > int(hasta):
        return False
    return True


def _puntaje_denominacion(fila: pd.Series, anio: int) -> tuple[int, int]:
    puntaje = 0
    if pd.notna(fila.get("anio_desde")) or pd.notna(fila.get("anio_hasta")):
        puntaje += 8
        if (
            pd.notna(fila.get("anio_desde"))
            and pd.notna(fila.get("anio_hasta"))
            and int(fila["anio_desde"]) == int(fila["anio_hasta"]) == anio
        ):
            puntaje += 4
    if texto(fila.get("cargo_clave")):
        puntaje += 4
    if texto(fila.get("distrito_clave")):
        puntaje += 2
    if texto(fila.get("tipo_eleccion")):
        puntaje += 1
    identificador = fila.get("id")
    orden = -int(identificador) if pd.notna(identificador) else 0
    return puntaje, orden


def _denominacion_para(
    fila: pd.Series, denominaciones: pd.DataFrame
) -> tuple[Optional[pd.Series], bool]:
    if denominaciones is None or denominaciones.empty:
        return None, False
    candidatas = denominaciones[
        denominaciones["nombre_clave"] == texto(fila["fuerza_clave"])
    ]
    if candidatas.empty:
        return None, False
    anio = int(fila["anio"])
    candidatas = candidatas[
        candidatas.apply(
            lambda regla: _cumple_rango(
                anio, regla.get("anio_desde"), regla.get("anio_hasta")
            ),
            axis=1,
        )
    ]
    if candidatas.empty:
        return None, False
    if texto(fila.get("cargo_clave")):
        candidatas = candidatas[
            candidatas["cargo_clave"].map(texto).isin(["", texto(fila["cargo_clave"])])
        ]
    if texto(fila.get("distrito_clave")):
        candidatas = candidatas[
            candidatas["distrito_clave"]
            .map(texto)
            .isin(["", texto(fila["distrito_clave"])])
        ]
    if texto(fila.get("tipo_eleccion")):
        candidatas = candidatas[
            candidatas["tipo_eleccion"]
            .map(texto)
            .isin(["", texto(fila["tipo_eleccion"])])
        ]
    publicables = candidatas[
        candidatas["estado"].map(texto).isin(["validada", "provisoria"])
    ]
    if publicables.empty:
        pendientes = candidatas[
            candidatas["estado"].map(texto).eq("pendiente")
        ]
        if pendientes.empty:
            return None, False
        evaluadas = sorted(
            (fila_denom for _, fila_denom in pendientes.iterrows()),
            key=lambda regla: _puntaje_denominacion(regla, anio),
            reverse=True,
        )
        return evaluadas[0], False
    candidatas = publicables
    evaluadas = sorted(
        (fila_denom for _, fila_denom in candidatas.iterrows()),
        key=lambda regla: _puntaje_denominacion(regla, anio),
        reverse=True,
    )
    mejor = evaluadas[0]
    conflicto = False
    if len(evaluadas) > 1:
        puntaje_primero = _puntaje_denominacion(evaluadas[0], anio)
        puntaje_segundo = _puntaje_denominacion(evaluadas[1], anio)
        if (
            puntaje_primero == puntaje_segundo
            and texto(evaluadas[0]["identidad"]) != texto(evaluadas[1]["identidad"])
        ):
            conflicto = True
    return mejor, conflicto


def _relaciones_para(
    fila: pd.Series,
    relaciones: pd.DataFrame,
    tipo: Optional[list[str]] = None,
    nivel: str = "",
) -> pd.DataFrame:
    if relaciones is None or relaciones.empty:
        return relaciones.iloc[0:0] if relaciones is not None else pd.DataFrame()
    tabla = relaciones[
        relaciones["estado"].map(texto).eq("validada")
    ].copy()
    if tipo:
        tabla = tabla[tabla["tipo_relacion"].map(texto).isin(tipo)]
    if tabla.empty:
        return tabla
    anio = int(fila["anio"])
    tabla = tabla[
        tabla.apply(
            lambda regla: _cumple_rango(
                anio, regla.get("anio_desde"), regla.get("anio_hasta")
            ),
            axis=1,
        )
    ]
    if texto(nivel):
        tabla = tabla[tabla["nivel"].map(texto).isin(["", texto(nivel)])]
    if texto(fila.get("cargo_clave")):
        tabla = tabla[
            tabla["cargo_clave"].map(texto).isin(["", texto(fila["cargo_clave"])])
        ]
    if texto(fila.get("distrito_clave")):
        tabla = tabla[
            tabla["distrito_clave"]
            .map(texto)
            .isin(["", texto(fila["distrito_clave"])])
        ]
    return tabla


def _destino_relacion(
    identidad: str,
    fila: pd.Series,
    relaciones: pd.DataFrame,
    usadas: set[int],
    nivel: str = "",
) -> tuple[str, list[str], bool]:
    actual = identidad
    aplicadas: list[str] = []
    conflicto = False
    for _ in range(12):
        candidatas = _relaciones_para(
            fila,
            relaciones,
            ["continuidad", "cambio_nombre", "fusion", "integracion_alianza"],
            nivel,
        )
        candidatas = candidatas[candidatas["origen"].map(texto).eq(actual)]
        if candidatas.empty:
            break
        candidatas = candidatas.sort_values("id")
        destinos = candidatas["destino"].map(texto).drop_duplicates().tolist()
        if len(destinos) > 1:
            conflicto = True
        destino = destinos[0]
        if not destino or destino == actual:
            break
        id_regla = int(candidatas.iloc[0]["id"])
        if id_regla in usadas:
            conflicto = True
            break
        usadas.add(id_regla)
        aplicadas.append(texto(candidatas.iloc[0]["tipo_relacion"]))
        actual = destino
    return actual, aplicadas, conflicto


def _bloque_para(
    identidad: str, fila: pd.Series, modelo: ModeloHistorico
) -> tuple[Optional[str], bool]:
    miembros = modelo.bloque_miembros
    if miembros is None or miembros.empty:
        return None, False
    bloques_validos = set(
        modelo.bloques.loc[
            modelo.bloques["estado"].map(texto).eq("validada"), "nombre"
        ].map(texto)
    )
    candidatos = miembros[
        miembros["identidad"].map(texto).eq(texto(identidad))
        & miembros["bloque"].map(texto).isin(bloques_validos)
        & miembros["estado"].map(texto).eq("validada")
    ].copy()
    if candidatos.empty:
        return None, False
    anio = int(fila["anio"])
    candidatos = candidatos[
        candidatos.apply(
            lambda regla: _cumple_rango(
                anio, regla.get("anio_desde"), regla.get("anio_hasta")
            ),
            axis=1,
        )
    ]
    if candidatos.empty:
        return None, False
    nombres = candidatos["bloque"].map(texto).drop_duplicates().tolist()
    return nombres[0], len(nombres) > 1


def _aplicar_legacy(
    fila: pd.Series,
    homologacion_legacy: Optional[pd.DataFrame],
    resultado: dict[str, Any],
) -> dict[str, Any]:
    if homologacion_legacy is None or homologacion_legacy.empty:
        return resultado
    reglas = homologacion_legacy.copy()
    reglas["_origen"] = reglas["fuerza_origen"].map(clave_fuerza)
    reglas = reglas[reglas["_origen"].eq(texto(fila["fuerza_clave"]))]
    if reglas.empty:
        return resultado
    anio = int(fila["anio"])
    reglas = reglas[
        reglas.apply(
            lambda regla: _cumple_rango(
                anio, regla.get("anio_desde"), regla.get("anio_hasta")
            ),
            axis=1,
        )
    ]
    if reglas.empty:
        return resultado
    reglas["_prioridad"] = [
        1
        if pd.notna(desde) or pd.notna(hasta)
        else 0
        for desde, hasta in zip(reglas["anio_desde"], reglas["anio_hasta"])
    ]
    regla = reglas.sort_values("_prioridad", ascending=False).iloc[0]
    resultado.update(
        {
            "fuerza": texto(regla["fuerza_homologada"]),
            "fuerza_identidad": texto(regla["fuerza_homologada"]),
            "nivel_analisis": "legacy",
            "estado_homologacion": "legacy",
            "fuente_homologacion": "homologacion_fuerzas (compatibilidad)",
            "relacion_aplicada": "",
            "conflicto_homologacion": "",
        }
    )
    return resultado


def aplicar_homologacion_historica(
    datos: pd.DataFrame,
    modelo: Optional[ModeloHistorico] = None,
    escenario: str = "estricto",
    homologacion_legacy: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Aplica denominaciones y, si corresponde, relaciones o bloques.

    - ``estricto``: usa la identidad de la denominación. No aplica relaciones.
    - ``relaciones``: aplica continuidades, cambios de nombre y fusiones
      *validadas*. Las provisionales quedan visibles como advertencia.
    - ``bloques``: aplica el bloque *validado* de cada identidad; si no hay
      bloque validado, conserva la identidad.
    """
    if modelo is None:
        modelo = cargar_modelo_historico()
    if escenario not in ESCENARIOS:
        escenario = "estricto"
    preparado = preparar_resultados(datos)
    if preparado.empty:
        return preparado.reindex(columns=COLUMNAS_SALIDA_HOMOLOGACION)

    ids_identidad = {
        texto(nombre): int(identidad_id)
        for nombre, identidad_id in zip(
            modelo.identidades.get("nombre", pd.Series(dtype=str)),
            modelo.identidades.get("id", pd.Series(dtype=float)),
        )
        if pd.notna(identidad_id)
    }
    # El conjunto de combinaciones fuerza/año/territorio es mucho menor que
    # la cantidad de filas. Cachear las búsquedas mantiene el resultado exacto
    # y evita recorrer las tablas de reglas una vez por cada registro.
    cache_denominaciones: dict[
        tuple[str, int, str, str, str], tuple[Optional[pd.Series], bool]
    ] = {}
    cache_legacy: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    cache_relaciones: dict[
        tuple[str, int, str, str, str], tuple[str, tuple[str, ...], bool]
    ] = {}
    cache_bloques: dict[tuple[str, int], tuple[Optional[str], bool]] = {}

    filas: list[dict[str, Any]] = []
    for _, fila in preparado.iterrows():
        clave_denominacion = (
            texto(fila["fuerza_clave"]),
            int(fila["anio"]),
            texto(fila.get("cargo_clave")),
            texto(fila.get("distrito_clave")),
            texto(fila.get("tipo_eleccion")),
        )
        if clave_denominacion not in cache_denominaciones:
            cache_denominaciones[clave_denominacion] = _denominacion_para(
                fila, modelo.denominaciones
            )
        regla, conflicto_regla = cache_denominaciones[clave_denominacion]
        if regla is None:
            resultado: dict[str, Any] = {
                "fuerza_origen": texto(fila.get("fuerza_original")) or texto(fila["fuerza"]),
                "fuerza_denominacion": texto(fila["fuerza"]),
                "fuerza": texto(fila["fuerza"]),
                "fuerza_identidad": texto(fila["fuerza"]),
                "fuerza_identidad_id": None,
                "nivel_analisis": "sin_homologar",
                "bloque": "",
                "estado_homologacion": "sin_denominacion",
                "fuente_homologacion": "",
                "relacion_aplicada": "",
                "conflicto_homologacion": "",
            }
        else:
            identidad = texto(regla["identidad"])
            estado_regla = texto(regla.get("estado")) or "pendiente"
            if estado_regla == "pendiente":
                # Una regla pendiente documenta una hipótesis, no una
                # equivalencia publicable: se conserva la denominación original.
                resultado = {
                    "fuerza_origen": texto(fila.get("fuerza_original")) or texto(fila["fuerza"]),
                    "fuerza_denominacion": texto(fila["fuerza"]),
                    "fuerza": texto(fila["fuerza"]),
                    "fuerza_identidad": texto(fila["fuerza"]),
                    "fuerza_identidad_id": None,
                    "nivel_analisis": "sin_homologar",
                    "bloque": "",
                    "estado_homologacion": "pendiente",
                    "fuente_homologacion": texto(regla.get("fuente")),
                    "relacion_aplicada": "",
                    "conflicto_homologacion": "",
                }
            else:
                resultado = {
                    "fuerza_origen": texto(fila.get("fuerza_original")) or texto(fila["fuerza"]),
                    "fuerza_denominacion": texto(fila["fuerza"]),
                    "fuerza": identidad,
                    "fuerza_identidad": identidad,
                    "fuerza_identidad_id": ids_identidad.get(identidad),
                    "nivel_analisis": texto(regla.get("nivel")) or "partido",
                    "bloque": "",
                    "estado_homologacion": estado_regla,
                    "fuente_homologacion": texto(regla.get("fuente")),
                    "relacion_aplicada": "",
                    "conflicto_homologacion": (
                        "Reglas de denominación empatadas" if conflicto_regla else ""
                    ),
                }
        if resultado["estado_homologacion"] == "sin_denominacion":
            clave_legacy = (
                texto(fila["fuerza_clave"]),
                int(fila["anio"]),
                texto(fila.get("fuerza_original")),
                texto(fila["fuerza"]),
            )
            if clave_legacy not in cache_legacy:
                cache_legacy[clave_legacy] = _aplicar_legacy(
                    fila, homologacion_legacy, dict(resultado)
                )
            resultado = dict(cache_legacy[clave_legacy])
        if (
            escenario in {"relaciones", "bloques"}
            and resultado["estado_homologacion"] in {"validada", "provisoria"}
        ):
            clave_relacion = (
                resultado["fuerza_identidad"],
                int(fila["anio"]),
                texto(fila.get("cargo_clave")),
                texto(fila.get("distrito_clave")),
                resultado["nivel_analisis"],
            )
            if clave_relacion not in cache_relaciones:
                usadas: set[int] = set()
                destino, aplicadas, conflicto = _destino_relacion(
                    resultado["fuerza_identidad"],
                    fila,
                    modelo.relaciones,
                    usadas,
                    resultado["nivel_analisis"],
                )
                cache_relaciones[clave_relacion] = (
                    destino,
                    tuple(aplicadas),
                    conflicto,
                )
            identidad_destino, aplicadas, conflicto = cache_relaciones[
                clave_relacion
            ]
            if aplicadas:
                resultado["fuerza"] = identidad_destino
                resultado["fuerza_identidad"] = identidad_destino
                resultado["relacion_aplicada"] = ",".join(aplicadas)
                if conflicto:
                    resultado["conflicto_homologacion"] = (
                        resultado["conflicto_homologacion"]
                        + " Relación histórica ambigua."
                    ).strip()
        if (
            escenario == "bloques"
            and resultado["estado_homologacion"] in {"validada", "provisoria"}
        ):
            clave_bloque = (resultado["fuerza_identidad"], int(fila["anio"]))
            if clave_bloque not in cache_bloques:
                cache_bloques[clave_bloque] = _bloque_para(
                    resultado["fuerza_identidad"], fila, modelo
                )
            bloque, conflicto_bloque = cache_bloques[clave_bloque]
            if bloque:
                resultado["bloque"] = bloque
                resultado["fuerza"] = bloque
                resultado["nivel_analisis"] = "bloque"
                if conflicto_bloque:
                    resultado["conflicto_homologacion"] = (
                        resultado["conflicto_homologacion"]
                        + " La identidad pertenece a más de un bloque validado."
                    ).strip()
        filas.append(resultado)

    salida = pd.DataFrame(filas, columns=COLUMNAS_SALIDA_HOMOLOGACION)
    base = preparado.reset_index(drop=True).copy()
    for columna in COLUMNAS_SALIDA_HOMOLOGACION:
        base[columna] = salida[columna].values
    base["fuerza"] = salida["fuerza"].values
    return base.reset_index(drop=True)


def diagnosticar_homologacion(
    datos: pd.DataFrame,
    modelo: Optional[ModeloHistorico] = None,
    escenario: str = "estricto",
    homologacion_legacy: Optional[pd.DataFrame] = None,
) -> dict[str, Any]:
    """Resume cobertura, reglas provisionales, conflictos y duplicados."""
    if modelo is None:
        modelo = cargar_modelo_historico()
    mapeado = aplicar_homologacion_historica(
        datos, modelo, escenario, homologacion_legacy
    )
    sin_denominacion = sorted(
        {
            texto(fila)
            for fila in mapeado.loc[
                mapeado["estado_homologacion"].eq("sin_denominacion"), "fuerza_denominacion"
            ]
        }
    )
    provisionales = mapeado[
        mapeado["estado_homologacion"].isin(["provisoria", "legacy"])
    ]
    pendientes = mapeado[
        mapeado["estado_homologacion"].eq("pendiente")
    ]
    conflictos_mapeo = mapeado[
        mapeado["conflicto_homologacion"].map(texto).ne("")
    ]
    relaciones_provisorias = modelo.relaciones[
        ~modelo.relaciones.get("estado", pd.Series(dtype=str))
        .map(texto)
        .eq("validada")
    ] if not modelo.relaciones.empty else modelo.relaciones
    bloques_provisorios = modelo.bloques[
        ~modelo.bloques.get("estado", pd.Series(dtype=str))
        .map(texto)
        .eq("validada")
    ] if not modelo.bloques.empty else modelo.bloques
    return {
        "filas": int(len(mapeado)),
        "por_estado": mapeado["estado_homologacion"].value_counts().to_dict(),
        "sin_denominacion": sin_denominacion,
        "provisorias": provisionales,
        "pendientes": pendientes,
        "conflictos_mapeo": conflictos_mapeo,
        "relaciones_provisorias": relaciones_provisorias,
        "bloques_provisorios": bloques_provisorios,
        "conflictos_denominaciones": validar_denominaciones(modelo.denominaciones),
        "conflictos_relaciones": validar_relaciones(modelo.relaciones),
        "conflictos_bloques": validar_bloques_miembros(modelo.bloque_miembros),
        "fuerzas_sin_homologar": len(sin_denominacion),
        "fuerzas_pendientes": int(
            pendientes["fuerza_denominacion"].nunique()
        ) if not pendientes.empty else 0,
        "fuerzas_provisorias": int(
            provisionales["fuerza_denominacion"].nunique()
        ) if not provisionales.empty else 0,
    }


def fuerzas_sin_homologar_historico(
    datos: pd.DataFrame,
    modelo: Optional[ModeloHistorico] = None,
    escenario: str = "estricto",
    homologacion_legacy: Optional[pd.DataFrame] = None,
) -> list[str]:
    diagnostico = diagnosticar_homologacion(
        datos, modelo, escenario, homologacion_legacy
    )
    return diagnostico["sin_denominacion"]


def ausencias_verificadas(modelo: ModeloHistorico) -> set[tuple[int, str, str, str]]:
    """Devuelve ``(anio, cargo_clave, distrito_clave, identidad)`` validados."""
    if modelo.ausencias is None or modelo.ausencias.empty:
        return set()
    tabla = modelo.ausencias[
        modelo.ausencias["estado"].map(texto).eq("validada")
    ]
    return {
        (
            int(fila["anio"]),
            texto(fila["cargo_clave"]),
            texto(fila["distrito_clave"]),
            texto(fila["identidad"]),
        )
        for _, fila in tabla.iterrows()
    }


def resumen_modelo(modelo: ModeloHistorico) -> dict[str, int]:
    return {
        "identidades": int(len(modelo.identidades)),
        "denominaciones": int(len(modelo.denominaciones)),
        "relaciones": int(len(modelo.relaciones)),
        "relaciones_validadas": int(
            modelo.relaciones["estado"].map(texto).eq("validada").sum()
        )
        if not modelo.relaciones.empty
        else 0,
        "bloques": int(len(modelo.bloques)),
        "miembros_bloque": int(len(modelo.bloque_miembros)),
        "ausencias_verificadas": int(len(modelo.ausencias)),
    }


# ---------------------------------------------------------------------------
# Edición desde administración
# ---------------------------------------------------------------------------


def _total_cambios(conexion: sqlite3.Connection) -> int:
    return int(conexion.execute("SELECT changes()").fetchone()[0])


def guardar_identidades(datos: pd.DataFrame) -> int:
    exigir_admin()
    tabla = limpiar_identidades(datos)
    if tabla.empty:
        raise ValueError("No hay identidades para guardar.")
    with closing(_conectar()) as conexion:
        crear_tablas(conexion)
        for fila in tabla.itertuples(index=False):
            existente = conexion.execute(
                "SELECT id FROM fuerzas_identidades WHERE nombre = ?",
                (fila.nombre,),
            ).fetchone()
            if existente:
                conexion.execute(
                    """
                    UPDATE fuerzas_identidades
                    SET nivel = ?, tipo = ?, estado = ?, fuente = ?,
                        confianza = ?, notas = ?
                    WHERE id = ?
                    """,
                    (
                        fila.nivel,
                        fila.tipo,
                        fila.estado,
                        fila.fuente,
                        fila.confianza,
                        fila.notas,
                        int(existente[0]),
                    ),
                )
            else:
                conexion.execute(
                    """
                    INSERT INTO fuerzas_identidades
                        (nombre, nivel, tipo, estado, fuente, confianza, notas, creado_en)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fila.nombre,
                        fila.nivel,
                        fila.tipo,
                        fila.estado,
                        fila.fuente,
                        fila.confianza,
                        fila.notas,
                        fila.creado_en,
                    ),
                )
        conexion.commit()
    return len(tabla)


def guardar_denominaciones(datos: pd.DataFrame) -> int:
    exigir_admin()
    modelo = cargar_modelo_historico()
    tabla = limpiar_denominaciones(datos, modelo.identidades)
    conflictos = validar_denominaciones(tabla)
    if conflictos:
        raise ValueError("\n".join(conflictos))
    with closing(_conectar()) as conexion:
        crear_tablas(conexion)
        conexion.execute("DELETE FROM fuerzas_denominaciones")
        insertadas = _insertar_denominaciones(conexion, tabla)
        conexion.commit()
    return insertadas


def guardar_relaciones(datos: pd.DataFrame) -> int:
    exigir_admin()
    modelo = cargar_modelo_historico()
    tabla = limpiar_relaciones(datos, modelo.identidades)
    with closing(_conectar()) as conexion:
        crear_tablas(conexion)
        conexion.execute("DELETE FROM fuerzas_relaciones")
        insertadas = _insertar_relaciones(conexion, tabla)
        conexion.commit()
    return insertadas


def guardar_bloques(datos: pd.DataFrame) -> int:
    exigir_admin()
    tabla = limpiar_bloques(datos)
    with closing(_conectar()) as conexion:
        crear_tablas(conexion)
        for fila in tabla.itertuples(index=False):
            existente = conexion.execute(
                "SELECT id FROM bloques_electorales WHERE nombre = ?", (fila.nombre,)
            ).fetchone()
            if existente:
                conexion.execute(
                    """
                    UPDATE bloques_electorales
                    SET nivel = ?, estado = ?, fuente = ?, confianza = ?, notas = ?
                    WHERE id = ?
                    """,
                    (
                        fila.nivel,
                        fila.estado,
                        fila.fuente,
                        fila.confianza,
                        fila.notas,
                        int(existente[0]),
                    ),
                )
            else:
                conexion.execute(
                    """
                    INSERT INTO bloques_electorales
                        (nombre, nivel, estado, fuente, confianza, notas, creado_en)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fila.nombre,
                        fila.nivel,
                        fila.estado,
                        fila.fuente,
                        fila.confianza,
                        fila.notas,
                        fila.creado_en,
                    ),
                )
        conexion.commit()
    return len(tabla)


def guardar_bloque_miembros(datos: pd.DataFrame) -> int:
    exigir_admin()
    modelo = cargar_modelo_historico()
    tabla = limpiar_bloque_miembros(
        datos, modelo.identidades, modelo.bloques
    )
    with closing(_conectar()) as conexion:
        crear_tablas(conexion)
        conexion.execute("DELETE FROM bloque_miembros")
        insertadas = _insertar_bloque_miembros(conexion, tabla)
        conexion.commit()
    return insertadas


def guardar_ausencias(datos: pd.DataFrame) -> int:
    exigir_admin()
    modelo = cargar_modelo_historico()
    tabla = limpiar_ausencias(datos, modelo.identidades)
    with closing(_conectar()) as conexion:
        crear_tablas(conexion)
        conexion.execute("DELETE FROM ausencias_verificadas")
        insertadas = _insertar_ausencias(conexion, tabla)
        conexion.commit()
    return insertadas
