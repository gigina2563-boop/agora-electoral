"""Configuración central de Ágora Electoral.

Este módulo no ejecuta comandos de Streamlit al importarse: solo expone
constantes y funciones. `setup_page()` debe llamarse desde `app.py` antes de
cualquier otra instrucción de Streamlit.
"""

from __future__ import annotations

import os
import tempfile
from functools import lru_cache
from typing import Any, Optional

import streamlit as st

APP_VERSION = "2.1.0"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DATOS_DIR = os.path.join(BASE_DIR, "datos")
SEED_RESULTADOS = os.path.join(DATA_DIR, "seed_resultados.csv")
SEED_HOMOLOGACION = os.path.join(DATA_DIR, "homologacion_partidos.csv")

EXTENSIONES_PLANILLA = (".csv", ".xls", ".xlsx", ".xlsm")
COLUMNAS_SALIDA = [
    "anio",
    "distrito",
    "circuito",
    "cargo",
    "fuerza",
    "votos_positivos",
    "porcentaje",
]
CLAVE_ELECTORAL = ["anio", "distrito", "circuito", "cargo", "fuerza"]

ALIAS_COLUMNAS = {
    "ano": "anio",
    "ano_electoral": "anio",
    "year": "anio",
    "election_year": "anio",
    "agrupacion": "fuerza",
    "agrupacion_politica": "fuerza",
    "nombre_agrupacion": "fuerza",
    "agrupacion_nombre": "fuerza",
    "fuerza_politica": "fuerza",
    "nombre_fuerza": "fuerza",
    "partido": "fuerza",
    "partido_politico": "fuerza",
    "alianza": "fuerza",
    "lista": "fuerza",
    "zona": "circuito",
    "seccion": "circuito",
    "seccion_electoral": "circuito",
    "circuito_electoral": "circuito",
    "nombre_circuito": "circuito",
    "circuito_nombre": "circuito",
    "circuito_zona": "circuito",
    "departamento": "distrito",
    "seccion_nombre": "distrito",
    "provincia": "distrito",
    "categoria": "cargo",
    "cargo_nombre": "cargo",
    "puesto": "cargo",
    "office": "cargo",
    "votos": "votos_positivos",
    "votos_validos": "votos_positivos",
    "votos_positivos_totales": "votos_positivos",
    "votos_cantidad": "votos_positivos",
    "porcentaje_de_votos": "porcentaje",
    "porcentaje_votos": "porcentaje",
    "pct_de_votos": "porcentaje",
    "pct_votos": "porcentaje",
    "pct": "porcentaje",
    "variacion": "diferencia",
    "variacion_pct": "diferencia",
    "diferencia": "diferencia",
}

ALIAS_FUERZA = {
    "fuerza",
    "fuerza_politica",
    "agrupacion",
    "agrupacion_politica",
    "partido",
    "partido_politico",
    "alianza",
    "lista",
    "nombre_agrupacion",
    "agrupacion_nombre",
}

PALETA = {
    "azul": "#2563eb",
    "azul_oscuro": "#1d4ed8",
    "celeste": "#0284c7",
    "azul_profundo": "#1e40af",
    "grafito": "#0f172a",
    "borde": "#e2e8f0",
    "fondo": "#f8fafc",
}


def setup_page() -> None:
    """Configura la página. Debe ser la primera llamada a Streamlit."""
    st.set_page_config(
        page_title="Ágora Electoral | Observatorio de La Rioja",
        page_icon="🏛️",
        layout="wide",
        initial_sidebar_state="expanded",
    )


def _bandera_activa(valor: Optional[str]) -> bool:
    return str(valor or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "si",
        "sí",
        "on",
    }


def config(nombre: str, defecto: Optional[str] = None) -> Optional[str]:
    """Lee secretos de Streamlit o variables de entorno sin romper la app.

    Prioridad: `st.secrets` (producción) y luego variables de entorno
    (desarrollo local, Docker, Render/Railway). Nunca devuelve valores vacíos.
    `AGORA_OFFLINE=1` desactiva las claves de nube para pruebas o desarrollo
    sin red; Ollama local y el motor de reglas siguen disponibles.
    """
    if _bandera_activa(os.getenv("AGORA_OFFLINE")) and nombre in {
        "GROQ_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    }:
        return defecto
    valor: Any = None
    try:
        valor = st.secrets.get(nombre)
    except Exception:
        valor = None
    if valor is None:
        try:
            if nombre in st.secrets:
                valor = st.secrets[nombre]
        except Exception:
            valor = None
    if valor is None:
        valor = os.getenv(nombre)
    if valor is None:
        return defecto
    texto = str(valor).strip()
    return texto or defecto


def entorno_actual() -> str:
    """Devuelve `produccion`, `local`, `dev` o `test`."""
    return (config("AGORA_ENTORNO", "produccion") or "produccion").strip().lower()


def es_entorno_local() -> bool:
    return entorno_actual() in {"local", "dev", "development", "test"}


def ollama_habilitado() -> bool:
    """Ollama se reserva para entornos locales o cuando se habilita a mano."""
    explicito = (config("OLLAMA_ENABLED", "") or "").strip().lower()
    if explicito:
        return explicito in {"1", "true", "yes", "si", "sí", "on"}
    return es_entorno_local()


def seed_habilitado() -> bool:
    valor = (config("AGORA_SEED", "1") or "1").strip().lower()
    return valor not in {"0", "false", "no", "off"}


@lru_cache(maxsize=1)
def get_db_path() -> str:
    """Devuelve una ruta SQLite escribible.

    En Streamlit Community Cloud el directorio del proyecto puede ser de solo
    lectura o efímero. Se usa el directorio del proyecto cuando es escribible y,
    si no, un directorio temporal. La base se reconstruye desde `data/` al
    iniciar si está vacía.
    """
    candidatos: list[str] = []
    override = (config("AGORA_DB_PATH", "") or "").strip()
    if override:
        candidatos.append(os.path.abspath(os.path.expanduser(override)))
    candidatos.append(os.path.join(BASE_DIR, "observatorio.db"))
    candidatos.append(
        os.path.join(tempfile.gettempdir(), "agora_electoral", "observatorio.db")
    )

    ultimo = candidatos[-1]
    for ruta in candidatos:
        carpeta = os.path.dirname(ruta) or "."
        try:
            os.makedirs(carpeta, exist_ok=True)
            prueba = os.path.join(carpeta, ".agora_write_test")
            with open(prueba, "w", encoding="utf-8") as archivo:
                archivo.write("ok")
            os.remove(prueba)
            return ruta
        except OSError:
            ultimo = ruta
            continue
    return ultimo


def api_timeout(nombre: str, defecto: float) -> float:
    try:
        return float(config(nombre, str(defecto)) or defecto)
    except (TypeError, ValueError):
        return defecto
