"""Cabecera institucional dinámica y compatible con Streamlit."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

_ASSETS = Path(__file__).resolve().parent / "assets"


@lru_cache(maxsize=8)
def _leer(nombre: str) -> str:
    return (_ASSETS / nombre).read_text(encoding="utf-8")


def render_header() -> None:
    """Inyecta CSS/HTML y ejecuta el JS dentro de un iframe del mismo origen.

    El JavaScript debe observar el scroll del documento padre de Streamlit.
    Por eso no se inyecta con `st.markdown` (que no ejecuta scripts): se usa
    `st.iframe` y, en versiones anteriores, `components.html` como respaldo.
    """
    css = _leer("header.css")
    html = _leer("header.html")
    js = _leer("header.js")

    # CSS y HTML en un solo bloque: evita un contenedor vacío de Streamlit
    # antes de la cabecera y deja el sticky como primer ítem del flujo.
    st.markdown(f"<style>{css}</style>{html}", unsafe_allow_html=True)

    documento_js = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        f"<script>{js}</script></body></html>"
    )
    if hasattr(st, "iframe"):
        st.iframe(documento_js, height=1)
    else:  # pragma: no cover - compatibilidad con Streamlit < 1.64
        components.html(documento_js, height=1, scrolling=False)
