"""Interfaz Streamlit de Ágora Electoral.

Rediseño visual del observatorio. La ingesta, SQLite, el motor de métricas y
la integración de IA permanecen en sus módulos originales; este archivo solo
compone la experiencia de usuario.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Iterable, Optional

import pandas as pd
import streamlit as st

from .ai import (
    construir_contexto_dictamen,
    estado_motores,
    generar_dictamen_hibrido,
)
from .auth import (
    admin_configurada,
    cerrar_sesion,
    es_admin,
    iniciar_sesion,
)
from .config import (
    APP_VERSION,
    PALETA,
    config,
    ollama_habilitado,
)
from .header import render_header
from .homologacion import (
    TIPOS_HOMOLOGACION,
    aplicar_homologacion,
    asegurar_homologacion_sembrada,
    cargar_homologacion,
    cargar_homologacion_desde_archivo,
    csv_homologacion,
    fuerzas_sin_mapear,
    guardar_homologacion,
    sembrar_identidades,
)
from .ingest import (
    asegurar_db_sembrada,
    cargar_datos_db,
    csv_resultados,
    guardar_fuentes_en_db,
    inspeccionar_planilla,
    listar_fuentes_db,
    listar_planillas_locales,
    normalizar_planilla,
)
from .metrics import (
    agregar_por_fuerza,
    analizar_supervivencia,
    calcular_indice_pedersen,
    calcular_nep,
    construir_comparacion,
    serie_agregada,
)
from .utils import (
    escapar_html,
    escapar_md,
    fmt_entero,
    fmt_numero,
    fmt_pct,
    fmt_pts,
    texto,
)

PLANTILLA_CSV = (
    "anio,distrito,circuito,cargo,fuerza,votos_positivos,porcentaje\n"
)

PESTANAS = [
    "Inicio",
    "Resultados electorales",
    "Análisis territorial",
    "Volatilidad de Pedersen",
    "Sistema de partidos",
    "Prospectiva 2027",
    "Informes IA",
    "Metodología y fuentes",
]

COLORES_SERIE = [
    "#1d4ed8",
    "#0284c7",
    "#0ea5e9",
    "#475569",
    "#64748b",
    "#94a3b8",
]


# ---------------------------------------------------------------------------
# Utilidades de presentación
# ---------------------------------------------------------------------------


def _encabezado_seccion(eyebrow: str, titulo: str, subtitulo: str) -> None:
    st.markdown(
        f"""
        <div class="agora-section-head">
          <span class="agora-eyebrow">{escapar_html(eyebrow)}</span>
          <h1>{escapar_html(titulo)}</h1>
          <p>{escapar_html(subtitulo)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _hero(eyebrow: str, titulo: str, subtitulo: str) -> None:
    st.markdown(
        f"""
        <div class="agora-hero">
          <span class="agora-eyebrow">{escapar_html(eyebrow)}</span>
          <h1>{escapar_html(titulo)}</h1>
          <p>{escapar_html(subtitulo)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _config_columnas_pct(columnas: Iterable[str]) -> dict[str, Any]:
    return {
        columna: st.column_config.NumberColumn(columna, format="%.2f%%")
        for columna in columnas
    }


def _pct_metrica(valor: Any, decimales: int = 2) -> str:
    """Porcentaje para tarjetas de indicador, con punto decimal."""
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return "s/d"
    if pd.isna(numero):
        return "s/d"
    return f"{numero:.{decimales}f}%"


def _nombre_archivo(partes: Iterable[Any], extension: str) -> str:
    base = "_".join(
        re.sub(r"[^a-zA-Z0-9]+", "_", texto(parte)).strip("_")
        for parte in partes
        if texto(parte)
    )
    return (base or "agora") + extension


def _firma_homologacion(tabla: pd.DataFrame) -> str:
    """Invalida dictámenes guardados cuando cambia la homologación."""
    return hashlib.sha1(csv_homologacion(tabla)).hexdigest()[:10]


def _chip(texto_chip: str, tono: str = "") -> str:
    clase = "agora-chip" + (f" agora-chip--{tono}" if tono else "")
    return f'<span class="{clase}">{escapar_html(texto_chip)}</span>'


def _columnas_geo(tabla: pd.DataFrame) -> tuple[str, str] | None:
    """Devuelve columnas de coordenadas solo si existen y son numéricas."""
    if tabla is None or tabla.empty:
        return None
    mapa = {str(columna).strip().lower(): columna for columna in tabla.columns}
    aliases_lat = ("lat", "latitud", "latitude")
    aliases_lon = ("lon", "lng", "longitud", "longitude")
    lat = next((mapa[nombre] for nombre in aliases_lat if nombre in mapa), None)
    lon = next((mapa[nombre] for nombre in aliases_lon if nombre in mapa), None)
    if lat is None or lon is None:
        return None
    latitudes = pd.to_numeric(tabla[lat], errors="coerce")
    longitudes = pd.to_numeric(tabla[lon], errors="coerce")
    validas = latitudes.between(-90, 90) & longitudes.between(-180, 180)
    if not validas.any():
        return None
    return str(lat), str(lon)


def _resumen_cobertura(
    datos: pd.DataFrame, cargo: str | None = None
) -> dict[str, Any]:
    if datos.empty:
        return {
            "elecciones": 0,
            "cargos": 0,
            "distritos": 0,
            "circuitos": 0,
            "fuerzas": 0,
            "anios": [],
        }
    base = datos
    if cargo and "cargo" in base.columns:
        base = base[base["cargo"].astype(str).eq(str(cargo))]
    anios = sorted(
        int(valor)
        for valor in pd.to_numeric(base["anio"], errors="coerce").dropna().unique()
    )
    return {
        "elecciones": len(anios),
        "cargos": int(base["cargo"].nunique()) if "cargo" in base.columns else 0,
        "distritos": (
            int(base["distrito"].nunique()) if "distrito" in base.columns else 0
        ),
        "circuitos": (
            int(base["circuito"].nunique()) if "circuito" in base.columns else 0
        ),
        "fuerzas": (
            int(base["fuerza"].nunique()) if "fuerza" in base.columns else 0
        ),
        "anios": anios,
    }


def _nep_por_circuito(corte: pd.DataFrame) -> pd.DataFrame:
    filas: list[dict[str, Any]] = []
    if corte is None or corte.empty or "circuito" not in corte.columns:
        return pd.DataFrame(columns=["Circuito", "NEP", "Fuerzas"])
    for circuito, grupo in corte.groupby("circuito"):
        serie = serie_agregada(grupo, "porcentaje", "votos_positivos")
        if not serie.empty:
            filas.append(
                {
                    "Circuito": circuito,
                    "NEP": round(calcular_nep(serie), 2),
                    "Fuerzas": len(serie),
                }
            )
    return pd.DataFrame(filas).sort_values("NEP", ascending=False)


def _serie_historica(
    datos: pd.DataFrame, cargo: str, distrito: str
) -> pd.DataFrame:
    """Serie de participación por fuerza y año, sin alterar el motor."""
    if datos.empty:
        return pd.DataFrame(columns=["anio", "fuerza", "porcentaje"])
    base = datos[datos["cargo"].astype(str).eq(str(cargo))].copy()
    if distrito != "Todos":
        base = base[base["distrito"].astype(str).eq(str(distrito))]
    filas: list[dict[str, Any]] = []
    for anio, grupo in base.groupby("anio"):
        serie = serie_agregada(grupo, "porcentaje", "votos_positivos")
        for fuerza, porcentaje in serie.items():
            filas.append(
                {
                    "anio": int(anio),
                    "fuerza": fuerza,
                    "porcentaje": float(porcentaje),
                }
            )
    return pd.DataFrame(filas)


# ---------------------------------------------------------------------------
# Acceso administrativo
# ---------------------------------------------------------------------------


def _render_acceso_admin() -> None:
    """Bloque de acceso en la parte superior del panel lateral."""
    with st.sidebar:
        st.markdown(
            """
            <div class="agora-side-head">
              <span>ACCESO</span>
              <strong>Administración</strong>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if es_admin():
            st.success("Sesión de administradora activa.")
            st.caption("Carga, validación y homologación habilitadas.")
            if st.button(
                "Cerrar sesión",
                width="stretch",
                key="agora_btn_cerrar_sesion",
            ):
                cerrar_sesion()
                for clave in (
                    "agora_homologacion_abierta",
                    "agora_mostrar_importacion",
                    "agora_mensaje_importacion",
                    "agora_firma_importada",
                    "agora_upload_planillas",
                    "agora_upload_homologacion",
                    "agora_editor_homologacion",
                    "agora_dictamen",
                    "agora_dictamen_clave",
                ):
                    st.session_state.pop(clave, None)
                st.rerun()
            st.divider()
            return

        if not admin_configurada():
            st.button(
                "Acceso administrador",
                width="stretch",
                disabled=True,
                help=(
                    "Configurá ADMIN_PASSWORD_HASH en Secrets para habilitar "
                    "el acceso."
                ),
            )
            st.caption(
                "El acceso administrativo todavía no está configurado. "
                "La consulta pública sigue disponible."
            )
            st.divider()
            return

        if not st.session_state.get("agora_login_abierto"):
            if st.button(
                "Acceso administrador",
                width="stretch",
                key="agora_btn_abrir_login",
            ):
                st.session_state["agora_login_abierto"] = True
                st.rerun()
            st.caption(
                "Consultá resultados sin registrarte. La edición de datos "
                "requiere contraseña."
            )
            st.divider()
            return

        with st.form("agora_admin_login", clear_on_submit=True):
            password = st.text_input(
                "Contraseña",
                type="password",
                key="agora_admin_password",
                autocomplete="current-password",
            )
            col_ingresar, col_cancelar = st.columns(2)
            with col_ingresar:
                ingresar = st.form_submit_button(
                    "Ingresar",
                    type="primary",
                    width="stretch",
                )
            with col_cancelar:
                cancelar = st.form_submit_button(
                    "Cancelar",
                    width="stretch",
                )
            if ingresar:
                autenticada, mensaje = iniciar_sesion(password or "")
                if autenticada:
                    st.session_state["agora_login_abierto"] = False
                    st.session_state.pop("agora_admin_password", None)
                    st.rerun()
                else:
                    st.error(mensaje)
            if cancelar:
                st.session_state["agora_login_abierto"] = False
                st.session_state.pop("agora_admin_password", None)
                st.rerun()
        st.divider()


# ---------------------------------------------------------------------------
# Carga de planillas
# ---------------------------------------------------------------------------
def _letra_columna(indice: int) -> str:
    letra = ""
    numero_columna = indice + 1
    while numero_columna:
        numero_columna, resto = divmod(numero_columna - 1, 26)
        letra = chr(65 + resto) + letra
    return letra


def _vista_original(
    tabla: pd.DataFrame,
    fila_encabezado: Optional[int] = None,
    max_filas: int = 28,
) -> pd.DataFrame:
    """Vista de la planilla tal como fue leída, con filas de Excel."""
    if tabla is None or tabla.empty:
        return pd.DataFrame()

    inicio = 0
    if fila_encabezado and len(tabla) > max_filas:
        inicio = max(0, int(fila_encabezado) - 9)
    fin = min(len(tabla), inicio + max_filas)
    vista = tabla.iloc[inicio:fin].copy()
    vista.columns = [
        _letra_columna(indice) for indice in range(len(vista.columns))
    ]
    vista.insert(0, "Fila", range(inicio + 1, fin + 1))
    return vista.reset_index(drop=True)


def _resumen_linea(datos: pd.DataFrame) -> str:
    if datos is None or datos.empty:
        return "Sin filas reconocidas."
    anios = ", ".join(
        map(str, sorted(datos["anio"].dropna().unique().tolist()))
    )
    votos = datos["votos_positivos"].sum(min_count=1)
    votos_txt = f"{votos:,.0f}".replace(",", ".") if pd.notna(votos) else "s/d"
    return (
        f"<strong>{len(datos)}</strong> filas · "
        f"<strong>{datos['fuerza'].nunique()}</strong> agrupaciones · "
        f"<strong>{datos['circuito'].nunique()}</strong> circuitos · "
        f"<strong>{votos_txt}</strong> votos positivos · "
        f"años {escapar_html(anios)}"
    )


def _mostrar_vista_previa(
    datos: pd.DataFrame, inspeccion: Any, hoja: str
) -> None:
    if inspeccion is not None and hoja in getattr(inspeccion, "hojas", {}):
        bloque = (
            inspeccion.bloque_para(hoja)
            if hasattr(inspeccion, "bloque_para")
            else None
        )
        st.caption("Vista previa de la planilla original")
        st.dataframe(
            _vista_original(
                inspeccion.hojas[hoja],
                bloque.fila_encabezado if bloque else None,
            ),
            hide_index=True,
            width="stretch",
        )

    st.caption("Vista previa de los datos normalizados")
    st.dataframe(datos.head(12), hide_index=True, width="stretch")
    st.markdown(
        f'<div class="agora-callout">{_resumen_linea(datos)}</div>',
        unsafe_allow_html=True,
    )


def _render_editor_planilla(
    archivo: Any, firma: str
) -> tuple[
    Optional[dict[str, Any]],
    Optional[pd.DataFrame],
    Optional[Any],
    Optional[str],
    Optional[str],
]:
    """Inspecciona un archivo y permite corregir el mapeo antes de importar."""
    try:
        inspeccion_auto = inspeccionar_planilla(archivo)
    except Exception as error:  # noqa: BLE001 - se informa al usuario
        st.error(f"{archivo.name}: {error}")
        return None, None, None, None, str(error)

    hojas = list(inspeccion_auto.hojas)
    if not hojas:
        mensaje = "El archivo no contiene hojas legibles."
        st.error(f"{archivo.name}: {mensaje}")
        return None, None, inspeccion_auto, None, mensaje

    hojas_con_datos = inspeccion_auto.hojas_con_datos
    opciones_hoja = ["Todas las hojas con datos"] + hojas
    if hojas_con_datos and len(hojas_con_datos) > 1:
        indice_hoja = 0
    else:
        hoja_default = hojas_con_datos[0] if hojas_con_datos else hojas[0]
        indice_hoja = opciones_hoja.index(hoja_default)

    hoja_sel = st.selectbox(
        "Hoja de la planilla",
        opciones_hoja,
        index=indice_hoja,
        key=f"agora_hoja_{firma}",
        help=(
            "La app inspecciona todas las hojas. Elegí una hoja para ajustar "
            "columnas o procesá todas las que tengan datos."
        ),
    )

    if hoja_sel == "Todas las hojas con datos":
        opciones: dict[str, Any] = {"hoja": None}
        hoja_previa = (
            hojas_con_datos[0] if hojas_con_datos else hojas[0]
        )
        try:
            datos = normalizar_planilla(archivo, opciones=opciones)
        except Exception as error:  # noqa: BLE001
            st.error(f"{archivo.name}: {error}")
            if inspeccion_auto.errores:
                st.caption(" ".join(inspeccion_auto.errores[:4]))
            _mostrar_vista_previa(
                pd.DataFrame(), inspeccion_auto, hoja_previa
            )
            return opciones, None, inspeccion_auto, hoja_previa, str(error)

        aceptadas = sum(
            bloque.filas_utiles for bloque in inspeccion_auto.bloques
        )
        excluidas = sum(
            bloque.filas_excluidas for bloque in inspeccion_auto.bloques
        )
        st.markdown(
            '<div class="agora-callout"><strong>Archivo reconocido.</strong> '
            "Podés revisar la vista previa y confirmar la importación al "
            "final de la sección.</div>",
            unsafe_allow_html=True,
        )
        st.caption(
            f"Filas aceptadas: {aceptadas} · filas excluidas: {excluidas}."
        )
        _mostrar_vista_previa(datos, inspeccion_auto, hoja_previa)
        return opciones, datos, inspeccion_auto, hoja_previa, None

    tabla_cruda = inspeccion_auto.hojas[hoja_sel]
    bloque_auto = inspeccion_auto.bloque_para(hoja_sel)
    fila_default = bloque_auto.fila_encabezado if bloque_auto else 1
    fila_sel = st.number_input(
        "Fila de encabezado (1 = primera fila)",
        min_value=1,
        max_value=max(1, len(tabla_cruda)),
        value=min(max(1, int(fila_default)), max(1, len(tabla_cruda))),
        step=1,
        key=f"agora_fila_encabezado_{firma}_{hoja_sel}",
        help=(
            "Si la detección automática no coincide, indicá la fila donde "
            "están los nombres de las columnas."
        ),
    )
    opciones_base: dict[str, Any] = {
        "hoja": hoja_sel,
        "fila_encabezado": int(fila_sel),
    }

    try:
        inspeccion = inspeccionar_planilla(archivo, opciones=opciones_base)
    except Exception as error:  # noqa: BLE001
        st.error(f"{archivo.name}: {error}")
        return opciones_base, None, inspeccion_auto, hoja_sel, str(error)

    bloque = inspeccion.bloque_para(hoja_sel)
    if bloque is None:
        mensaje = (
            "No pude detectar un encabezado electoral en la fila elegida. "
            "Probá con otra fila."
        )
        st.error(f"{archivo.name}: {mensaje}")
        st.dataframe(
            _vista_original(tabla_cruda, int(fila_sel)),
            hide_index=True,
            width="stretch",
        )
        return opciones_base, None, inspeccion, hoja_sel, mensaje

    st.caption(
        f"Encabezado detectado en la fila {bloque.fila_encabezado}. "
        f"Año: {bloque.anio or 'sin detectar'} · "
        f"Cargo: {bloque.cargo or 'sin detectar'} · "
        f"Distrito: {bloque.distrito or 'sin detectar'}."
    )
    for aviso in bloque.advertencias[:3]:
        st.caption(aviso)
    for error_bloque in bloque.errores:
        st.warning(error_bloque)
    detalle_excluidas = getattr(bloque, "filas_excluidas_detalle", [])
    if detalle_excluidas:
        st.caption(
            "Filas aceptadas: "
            f"{bloque.filas_utiles} · filas excluidas: "
            f"{bloque.filas_excluidas}"
        )
        st.dataframe(
            pd.DataFrame(detalle_excluidas).rename(
                columns={
                    "fila": "Fila",
                    "fuerza": "Fuerza o concepto",
                    "motivo": "Motivo de exclusión",
                }
            ),
            hide_index=True,
            width="stretch",
        )

    tiene_datos = bool(
        bloque.mapeo.get("fuerza")
        and (
            bloque.mapeo.get("votos_positivos")
            or bloque.mapeo.get("porcentaje")
            or bloque.columnas_pct_anuales
        )
    )
    ajustar = st.checkbox(
        "Ajustar columnas y metadatos manualmente",
        value=bool(bloque.errores) or not tiene_datos,
        key=f"agora_ajustar_{firma}_{hoja_sel}_{fila_sel}",
        help=(
            "Usalo cuando la planilla tenga encabezados ambiguos, columnas "
            "con nombres poco habituales o metadatos incompletos."
        ),
    )

    if ajustar:
        opciones_columnas = ["(sin columna)"] + bloque.columnas

        def indice_columna(campo: str) -> int:
            columna = bloque.mapeo.get(campo)
            return (
                opciones_columnas.index(columna)
                if columna in opciones_columnas
                else 0
            )

        st.markdown("**Mapeo de columnas**")
        mapeo: dict[str, Optional[str]] = {}
        campos = (
            ("fuerza", "Agrupación política"),
            ("votos_positivos", "Votos positivos"),
            ("porcentaje", "Porcentaje de votos positivos"),
            ("anio", "Año"),
            ("cargo", "Categoría electoral"),
            ("distrito", "Distrito / departamento"),
            ("circuito", "Circuito"),
            ("mesa", "Mesa"),
        )
        for campo, etiqueta in campos:
            seleccion = st.selectbox(
                etiqueta,
                opciones_columnas,
                index=indice_columna(campo),
                key=f"agora_map_{firma}_{hoja_sel}_{fila_sel}_{campo}",
            )
            mapeo[campo] = (
                None if seleccion == "(sin columna)" else seleccion
            )

        st.markdown("**Metadatos de la planilla**")
        anio_txt = st.text_input(
            "Año electoral",
            value=str(bloque.anio or ""),
            key=f"agora_anio_{firma}_{hoja_sel}_{fila_sel}",
            help="Solo si no está en una columna o en los metadatos.",
        )
        cargo_txt = st.text_input(
            "Categoría electoral",
            value=bloque.cargo,
            key=f"agora_cargo_{firma}_{hoja_sel}_{fila_sel}",
        )
        distrito_txt = st.text_input(
            "Distrito / departamento",
            value=bloque.distrito,
            key=f"agora_distrito_{firma}_{hoja_sel}_{fila_sel}",
        )
        circuito_txt = st.text_input(
            "Circuito (si no está en una columna)",
            value=bloque.circuito,
            key=f"agora_circuito_{firma}_{hoja_sel}_{fila_sel}",
        )
        anio_manual: Optional[int] = None
        if anio_txt.strip():
            if re.fullmatch(r"\d{4}", anio_txt.strip()):
                anio_manual = int(anio_txt.strip())
            else:
                st.warning("El año electoral debe tener cuatro dígitos.")

        opciones = {
            **opciones_base,
            "mapeo": mapeo,
            "anio": anio_manual,
            "cargo": cargo_txt.strip() or None,
            "distrito": distrito_txt.strip() or None,
            "circuito": circuito_txt.strip() or None,
        }
    else:
        opciones = opciones_base

    try:
        datos = normalizar_planilla(archivo, opciones=opciones)
    except Exception as error:  # noqa: BLE001
        st.error(f"{archivo.name}: {error}")
        st.dataframe(
            _vista_original(tabla_cruda, bloque.fila_encabezado),
            hide_index=True,
            width="stretch",
        )
        return opciones, None, inspeccion, hoja_sel, str(error)

    _mostrar_vista_previa(datos, inspeccion, hoja_sel)
    return opciones, datos, inspeccion, hoja_sel, None


def _render_carga_planillas(hay_datos: bool) -> None:
    """Sección de ingesta visible, separada de los filtros de análisis."""
    if not es_admin():
        return
    mostrar_mensaje = bool(st.session_state.get("agora_mostrar_importacion"))
    with st.expander(
        "Carga de planillas · CSV, XLS y XLSX",
        expanded=(not hay_datos) or mostrar_mensaje,
    ):
        st.markdown(
            """
            <div class="agora-upload-intro">
              <strong>Ingesta de planillas electorales.</strong>
              Acepta XLS, XLSX, XLSM y CSV del CNE o de escrutinio propio.
              La app detecta el encabezado, el año y la categoría, excluye
              totales y votos no positivos, y conserva el archivo original
              para auditoría. Revisá la vista previa antes de confirmar.
            </div>
            """,
            unsafe_allow_html=True,
        )
        archivos_subidos = st.file_uploader(
            "Subí una o varias planillas",
            type=["csv", "xls", "xlsx", "xlsm"],
            accept_multiple_files=True,
            key="agora_upload_planillas",
            help=(
                "CSV, XLS, XLSX y XLSM. Se inspeccionan todas las hojas y se "
                "admiten columnas en español, planillas anchas y el CSV "
                "oficial del CNE."
            ),
        )

        col_plantilla, col_formatos = st.columns([1, 2])
        with col_plantilla:
            st.download_button(
                "Descargar plantilla CSV",
                data=PLANTILLA_CSV,
                file_name="plantilla_resultados_electorales.csv",
                mime="text/csv",
                width="stretch",
            )
        with col_formatos:
            st.caption(
                "Columnas recomendadas: anio, distrito, circuito, cargo, "
                "fuerza y votos_positivos. También acepta porcentaje, coma o "
                "punto y coma como separador."
            )

        resultados: list[dict[str, Any]] = []
        if archivos_subidos:
            st.markdown("**Archivos seleccionados**")
            for archivo in archivos_subidos:
                firma = hashlib.sha256(archivo.getvalue()).hexdigest()[:16]
                st.markdown(f"**{escapar_md(archivo.name)}**")
                opciones, datos, inspeccion, hoja, error = (
                    _render_editor_planilla(archivo, firma)
                )
                resultados.append(
                    {
                        "archivo": archivo,
                        "opciones": opciones,
                        "datos": datos,
                        "inspeccion": inspeccion,
                        "hoja": hoja,
                        "error": error,
                    }
                )
                st.divider()

            todos_ok = bool(resultados) and all(
                item["datos"] is not None and item["error"] is None
                for item in resultados
            )
            if not todos_ok:
                st.warning(
                    "Hay planillas con errores de lectura. Corregí el mapeo "
                    "o elegí otra hoja antes de importar."
                )
            if st.button(
                "Validar e importar planillas",
                type="primary",
                width="stretch",
                disabled=not todos_ok,
                key="agora_btn_importar_planillas",
            ):
                try:
                    with st.spinner("Guardando planillas y fuentes…"):
                        filas_guardadas = guardar_fuentes_en_db(
                            [item["archivo"] for item in resultados],
                            opciones=[item["opciones"] for item in resultados],
                        )
                    st.session_state["agora_firma_importada"] = tuple(
                        hashlib.sha256(
                            item["archivo"].getvalue()
                        ).hexdigest()[:16]
                        for item in resultados
                    )
                    st.session_state["agora_mensaje_importacion"] = (
                        f"Planillas procesadas: {filas_guardadas} filas de "
                        f"{len(resultados)} archivo(s). La fuente original "
                        "quedó guardada para auditoría."
                    )
                    st.session_state["agora_mostrar_importacion"] = True
                    st.rerun()
                except Exception as error:  # noqa: BLE001
                    st.error(f"No se pudo importar: {error}")
                    st.caption(
                        "No se guardaron datos. Corregí la planilla y volvé a "
                        "intentarlo."
                    )
        else:
            st.info(
                "Pulsá **Browse files / Examinar** y seleccioná una o varias "
                "planillas. La app muestra la vista previa original y la "
                "normalizada antes de guardar."
            )

        planillas_locales = listar_planillas_locales()
        if planillas_locales:
            st.divider()
            st.markdown(
                f"**Planillas locales disponibles en `datos/` "
                f"({len(planillas_locales)})**"
            )
            for ruta in planillas_locales:
                st.caption(f"• {os.path.basename(ruta)}")
            if st.button(
                "Importar planillas de datos/",
                width="stretch",
                key="agora_btn_importar_locales",
            ):
                try:
                    with st.spinner("Importando planillas locales…"):
                        filas = guardar_fuentes_en_db(planillas_locales)
                    st.session_state["agora_mensaje_importacion"] = (
                        f"Planillas locales procesadas: {filas} filas."
                    )
                    st.session_state["agora_mostrar_importacion"] = True
                    st.rerun()
                except Exception as error:
                    st.error(f"No se pudieron importar: {error}")


# ---------------------------------------------------------------------------
# Panel lateral: filtros y diagnóstico
# ---------------------------------------------------------------------------


def _render_diagnostico_tecnico(modelo_ollama: str) -> str:
    """Diagnóstico y recarga de caché, reservados a la administradora."""
    with st.expander("Diagnóstico técnico"):
        estado = estado_motores()
        st.caption(f"Ágora Electoral v{APP_VERSION}")
        st.caption(
            "Persistencia: SQLite. En hosting gratuito la base es "
            "efímera y se reconstruye desde `data/` al reiniciar."
        )
        st.caption(
            f"Entorno: {config('AGORA_ENTORNO', 'produccion') or 'produccion'}"
        )
        st.markdown("**Motor de dictamen**")
        st.caption(
            "Orden de intento: Groq → Gemini → Ollama local → reglas."
        )
        if estado["groq"]:
            st.success("Groq configurado como proveedor principal.")
        elif estado["gemini"]:
            st.warning(
                "Groq no está configurado. Gemini queda como respaldo."
            )
        else:
            st.warning(
                "Sin clave de nube: se usará el motor analítico de reglas."
            )
        if estado["ollama"]:
            modelo_ollama = st.text_input(
                "Modelo Ollama local",
                value=str(estado["modelo_ollama"]),
                key="agora_ollama_model",
                help="Timeout de conexión: 3 segundos.",
            )
        else:
            st.caption(
                "Ollama deshabilitado. Para uso local: "
                "`AGORA_ENTORNO=local`."
            )
        if st.button("Recargar datos", width="stretch"):
            st.cache_data.clear()
            st.rerun()
    return modelo_ollama


def _render_sidebar(
    datos: pd.DataFrame,
    datos_crudos: pd.DataFrame,
    homologacion: pd.DataFrame,
) -> dict[str, Any]:
    modelo_ollama = config("OLLAMA_MODEL", "gemma3:1b") or "gemma3:1b"
    cargo_sel: Optional[str] = None
    anio_actual: Optional[int] = None
    anio_base: Optional[int] = None
    distrito_sel = "Todos"
    ausencias_son_cero = True

    with st.sidebar:
        st.markdown(
            """
            <div class="agora-side-head">
              <span>Panel de control</span>
              <strong>Filtros del observatorio</strong>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if not datos.empty and {"cargo", "anio", "distrito"}.issubset(
            datos.columns
        ):
            cargos = sorted(
                datos["cargo"].dropna().astype(str).unique().tolist()
            )
            if cargos:
                cargo_sel = st.selectbox(
                    "Cargo a analizar",
                    cargos,
                    key="agora_cargo",
                )
                datos_cargo = datos[
                    datos["cargo"].astype(str).eq(cargo_sel)
                ]
                anios = sorted(
                    datos_cargo["anio"]
                    .dropna()
                    .astype(int)
                    .unique()
                    .tolist()
                )
                if anios:
                    anio_actual = st.selectbox(
                        "Elección de referencia",
                        anios,
                        index=len(anios) - 1,
                        key=f"agora_anio_actual::{cargo_sel}",
                    )
                    anios_base = [
                        anio for anio in anios if anio < anio_actual
                    ]
                    if anios_base:
                        anio_base = st.selectbox(
                            "Ciclo electoral de comparación",
                            anios_base,
                            index=len(anios_base) - 1,
                            key=f"agora_anio_base::{cargo_sel}::{anio_actual}",
                        )
                    else:
                        anio_base = None
                        st.caption(
                            "Se requiere una elección anterior del mismo "
                            "cargo para habilitar Pedersen, supervivencia y "
                            "prospectiva comparada."
                        )
                    distritos = ["Todos"] + sorted(
                        datos_cargo["distrito"]
                        .dropna()
                        .astype(str)
                        .unique()
                        .tolist()
                    )
                    distrito_sel = st.selectbox(
                        "Distrito / departamento",
                        distritos,
                        key=f"agora_distrito::{cargo_sel}::{anio_actual}",
                    )
                    ausencias_son_cero = st.checkbox(
                        "Mitigar sesgo de supervivencia (0,0% a fuerzas "
                        "nuevas o extintas)",
                        value=True,
                        key="agora_ausencias_cero",
                        help=(
                            "Asigna 0,0% a las fuerzas ausentes en uno de los "
                            "dos períodos. Es el criterio metodológico del "
                            "observatorio. Desactivalo solo si la ausencia "
                            "puede ser un dato faltante."
                        ),
                    )
        else:
            if es_admin():
                st.info("Cargá una planilla para habilitar los filtros.")
            else:
                st.info(
                    "Todavía no hay resultados públicos cargados para "
                    "habilitar los filtros."
                )

        st.divider()
        st.markdown(
            '<div class="agora-side-label">Calidad de datos</div>',
            unsafe_allow_html=True,
        )
        sin_mapear = fuerzas_sin_mapear(datos_crudos, homologacion)
        if sin_mapear:
            st.warning(
                f"{len(sin_mapear)} fuerza(s) sin regla de homologación."
            )
            with st.expander("Ver fuerzas pendientes"):
                for nombre in sin_mapear[:20]:
                    st.caption(f"• {nombre}")
                if len(sin_mapear) > 20:
                    st.caption(f"… y {len(sin_mapear) - 20} más.")
        else:
            st.caption(
                f"{len(homologacion)} reglas activas. Todas las fuerzas "
                "visibles tienen cobertura."
            )
        if es_admin():
            if st.button(
                "Editar homologación",
                width="stretch",
                key="agora_btn_abrir_homologacion",
            ):
                st.session_state["agora_homologacion_abierta"] = True
                st.rerun()
        st.download_button(
            "Descargar homologación CSV",
            data=csv_homologacion(homologacion),
            file_name="homologacion_partidos.csv",
            mime="text/csv",
            width="stretch",
        )

        if es_admin():
            st.divider()
            modelo_ollama = _render_diagnostico_tecnico(modelo_ollama)

    return {
        "datos": datos,
        "datos_crudos": datos_crudos,
        "homologacion": homologacion,
        "cargo": cargo_sel,
        "anio_actual": anio_actual,
        "anio_base": anio_base,
        "distrito": distrito_sel,
        "ausencias_son_cero": ausencias_son_cero,
        "modelo_ollama": modelo_ollama,
    }


# ---------------------------------------------------------------------------
# Homologación
# ---------------------------------------------------------------------------


@st.dialog("Homologación de partidos y alianzas", width="large")
def _dialogo_homologacion(
    datos_crudos: pd.DataFrame, homologacion: pd.DataFrame
) -> None:
    if not es_admin():
        st.error(
            "La edición de la homologación requiere una sesión de "
            "administradora."
        )
        return
    st.markdown(
        "Una fila por denominación de origen. Si una alianza cambió de "
        "nombre entre elecciones, apuntá ambas filas al mismo nombre "
        "canónico. Las reglas con año explícito tienen prioridad sobre las "
        "generales."
    )
    sin_mapear = fuerzas_sin_mapear(datos_crudos, homologacion)
    if sin_mapear:
        st.warning(
            f"Hay {len(sin_mapear)} fuerza(s) sin regla: "
            + ", ".join(sin_mapear[:8])
            + ("…" if len(sin_mapear) > 8 else "")
        )
    else:
        st.success("Todas las fuerzas cargadas tienen una regla de homologación.")

    editada = st.data_editor(
        homologacion,
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        key="agora_editor_homologacion",
        column_config={
            "fuerza_origen": st.column_config.TextColumn(
                "Fuerza en la planilla", required=True, width="medium"
            ),
            "fuerza_homologada": st.column_config.TextColumn(
                "Nombre canónico", required=True, width="medium"
            ),
            "tipo": st.column_config.SelectboxColumn(
                "Tipo",
                options=TIPOS_HOMOLOGACION,
                required=True,
                width="small",
            ),
            "anio_desde": st.column_config.NumberColumn(
                "Desde", min_value=1900, max_value=2100, step=1, format="%d"
            ),
            "anio_hasta": st.column_config.NumberColumn(
                "Hasta", min_value=1900, max_value=2100, step=1, format="%d"
            ),
            "notas": st.column_config.TextColumn("Notas", width="large"),
        },
    )
    st.caption(
        "Dejá vacíos 'Desde' y 'Hasta' para aplicar la regla a todos los "
        "años. La tabla se guarda en la base y se puede descargar como CSV."
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Guardar", type="primary", width="stretch"):
            try:
                cantidad = guardar_homologacion(editada)
                st.session_state["agora_homologacion_abierta"] = False
                st.success(f"Guardadas {cantidad} reglas.")
                st.rerun()
            except Exception as error:
                st.error(f"No se pudo guardar: {error}")
    with col2:
        if st.button("Agregar fuerzas faltantes", width="stretch"):
            try:
                cantidad = sembrar_identidades(datos_crudos, editada)
                st.success(f"Agregadas {cantidad} reglas identidad.")
                st.rerun()
            except Exception as error:
                st.error(f"No se pudieron agregar: {error}")
    with col3:
        if st.button("Cerrar", width="stretch"):
            st.session_state["agora_homologacion_abierta"] = False
            st.rerun()

    st.download_button(
        "Descargar tabla (.csv)",
        data=csv_homologacion(editada),
        file_name="homologacion_partidos.csv",
        mime="text/csv",
        width="stretch",
    )

    with st.expander("Importar tabla desde CSV"):
        archivo = st.file_uploader(
            "Reemplazar la tabla actual",
            type=["csv"],
            key="agora_upload_homologacion",
        )
        if archivo is not None and st.button("Reemplazar con CSV"):
            try:
                nueva = cargar_homologacion_desde_archivo(archivo)
                cantidad = guardar_homologacion(nueva)
                st.session_state["agora_homologacion_abierta"] = False
                st.success(f"Reemplazada por {cantidad} reglas.")
                st.rerun()
            except Exception as error:
                st.error(f"No se pudo importar: {error}")


def _render_homologacion_block(
    datos_crudos: pd.DataFrame,
    homologacion: pd.DataFrame,
    cargo: str,
    anio_base: int | None,
    anio_actual: int,
    distrito: str,
) -> None:
    st.markdown(
        "La homologación define qué denominaciones se consideran la misma "
        "fuerza. Sin esta armonización, un cambio de nombre o de alianza "
        "puede inflar artificialmente Pedersen y distorsionar la "
        "supervivencia partidaria."
    )
    sin_mapear = fuerzas_sin_mapear(datos_crudos, homologacion)
    if sin_mapear:
        st.warning("Fuerzas sin regla: " + ", ".join(sin_mapear[:10]))
    else:
        st.success(
            "Todas las fuerzas del conjunto analizado tienen una regla."
        )

    met1, met2 = st.columns([1, 3])
    with met1:
        st.metric("Reglas de homologación", str(len(homologacion)))
    with met2:
        st.caption(
            "Las reglas con año explícito tienen prioridad. Los datos crudos "
            "no se reescriben: la homologación se aplica en la consulta."
        )

    st.dataframe(
        homologacion,
        hide_index=True,
        width="stretch",
        column_config={
            "anio_desde": st.column_config.NumberColumn("Desde", format="%d"),
            "anio_hasta": st.column_config.NumberColumn("Hasta", format="%d"),
        },
    )

    if not datos_crudos.empty and "fuerza" in datos_crudos.columns:
        filtro = datos_crudos["anio"].isin(
            [int(anio_base) if anio_base is not None else int(anio_actual),
             int(anio_actual)]
        )
        if "cargo" in datos_crudos.columns:
            filtro &= datos_crudos["cargo"].astype(str).eq(str(cargo))
        if distrito != "Todos" and "distrito" in datos_crudos.columns:
            filtro &= datos_crudos["distrito"].astype(str).eq(str(distrito))
        base_correspondencias = (
            datos_crudos.loc[filtro, ["fuerza", "anio"]]
            .drop_duplicates()
        )
        base_correspondencias = aplicar_homologacion(
            base_correspondencias, homologacion
        )
        correspondencias = (
            base_correspondencias[["fuerza_origen", "fuerza"]]
            .drop_duplicates()
            .rename(
                columns={
                    "fuerza_origen": "Denominación en la planilla",
                    "fuerza": "Nombre homologado",
                }
            )
            .sort_values("Nombre homologado")
        )
        st.markdown("**Correspondencias usadas**")
        st.dataframe(correspondencias, hide_index=True, width="stretch")

    col_a, col_b = st.columns(2)
    with col_a:
        if es_admin():
            if st.button(
                "Editar tabla de homologación",
                type="primary",
                width="stretch",
                key="agora_btn_homologacion_tab",
            ):
                st.session_state["agora_homologacion_abierta"] = True
                st.rerun()
        else:
            st.caption(
                "La edición de la homologación está reservada a la "
                "administradora."
            )
    with col_b:
        st.download_button(
            "Descargar homologación (.csv)",
            data=csv_homologacion(homologacion),
            file_name="homologacion_partidos.csv",
            mime="text/csv",
            width="stretch",
        )


# ---------------------------------------------------------------------------
# Dictamen
# ---------------------------------------------------------------------------


def _render_dictamen(
    contexto: dict[str, Any],
    modelo_ollama: str,
    clave_resultado: str,
) -> None:
    """Botón, ejecución y presentación del dictamen híbrido."""
    with st.container(border=True):
        st.markdown(
            """
            <div class="agora-report-head">
              <span class="agora-eyebrow">INFORME POLITOLÓGICO</span>
              <h2>Generador de informes asistido por IA</h2>
              <p>Datos comprobados, indicadores calculados, interpretación,
              prospectiva y nota metodológica. No es un pronóstico electoral.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        col_btn, col_note = st.columns([2, 1])
        with col_btn:
            generar = st.button(
                "Generar dictamen politológico",
                type="primary",
                width="stretch",
                key=f"agora_boton_dictamen_{clave_resultado}",
            )
        with col_note:
            st.caption(
                "Proveedor principal: Groq. Respaldo: Gemini. Fallback "
                "determinista: motor de reglas."
            )

        if generar:
            with st.spinner(
                "Consultando Groq, proveedores de respaldo y motor de reglas…"
            ):
                resultado = generar_dictamen_hibrido(
                    contexto,
                    modelo_ollama=modelo_ollama,
                    usar_ollama=ollama_habilitado(),
                )
            st.session_state["agora_dictamen"] = resultado
            st.session_state["agora_dictamen_clave"] = clave_resultado

        resultado = st.session_state.get("agora_dictamen")
        if (
            not resultado
            or st.session_state.get("agora_dictamen_clave")
            != clave_resultado
        ):
            st.info(
                "Elegí una fuerza y generá el informe para el período "
                "seleccionado."
            )
            return

        st.markdown(
            '<div class="agora-report-meta">'
            + _chip(f"Fuerza: {texto(contexto.get('fuerza'))}", "accent")
            + _chip(
                f"Período: {contexto.get('anio_base') or 's/d'} → "
                f"{contexto.get('anio_actual')}"
            )
            + _chip(f"Motor: {texto(resultado.get('motor'))}", "ghost")
            + "</div>",
            unsafe_allow_html=True,
        )
        st.caption(
            f"Motor: **{escapar_md(resultado.get('motor', 's/d'))}** · "
            f"{resultado.get('segundos', 0):.2f} s"
        )
        with st.container(border=True):
            st.markdown(resultado.get("texto", ""))
        st.download_button(
            "Descargar dictamen (.md)",
            data=resultado.get("texto", ""),
            file_name=_nombre_archivo(
                [
                    "dictamen",
                    contexto.get("fuerza"),
                    contexto.get("anio_actual"),
                ],
                ".md",
            ),
            mime="text/markdown",
            width="stretch",
        )
        intentos = resultado.get("intentos") or []
        if intentos:
            with st.expander("Trazabilidad del motor"):
                for intento in intentos:
                    st.caption(f"• {intento}")


# ---------------------------------------------------------------------------
# Estado de datos y pestañas
# ---------------------------------------------------------------------------


def _construir_estado(filtros: dict[str, Any]) -> dict[str, Any]:
    datos = filtros["datos"]
    cargo = str(filtros["cargo"])
    anio_actual = int(filtros["anio_actual"])
    anio_base = (
        int(filtros["anio_base"]) if filtros["anio_base"] is not None else None
    )
    distrito = str(filtros["distrito"])
    ausencias = bool(filtros["ausencias_son_cero"])

    corte_actual = datos[
        datos["cargo"].astype(str).eq(cargo)
        & datos["anio"].eq(anio_actual)
    ].copy()
    if distrito != "Todos":
        corte_actual = corte_actual[
            corte_actual["distrito"].astype(str).eq(distrito)
        ]

    participacion = serie_agregada(
        corte_actual, "porcentaje", "votos_positivos"
    ).sort_values(ascending=False)
    votos_actuales = pd.to_numeric(
        corte_actual["votos_positivos"], errors="coerce"
    ).sum(min_count=1)
    nep_actual = calcular_nep(participacion)

    votos_por_fuerza = pd.to_numeric(
        corte_actual["votos_positivos"], errors="coerce"
    ).groupby(corte_actual["fuerza"]).sum(min_count=1)

    tabla_actual = pd.DataFrame(
        {
            "fuerza": participacion.index,
            "pct_base": pd.NA,
            "pct_actual": participacion.values,
            "votos_base": pd.NA,
            "votos_actual": [
                votos_por_fuerza.get(fuerza, pd.NA)
                for fuerza in participacion.index
            ],
            "diferencia": pd.NA,
        }
    )

    estado: dict[str, Any] = {
        "modo": "unica",
        "cargo": cargo,
        "anio_actual": anio_actual,
        "anio_base": anio_base,
        "distrito": distrito,
        "ausencias": ausencias,
        "corte_actual": corte_actual,
        "participacion": participacion,
        "votos_actuales": votos_actuales,
        "nep_actual": nep_actual,
        "tabla_fuerzas": tabla_actual,
        "comparacion": pd.DataFrame(),
        "pedersen": None,
        "detalle_pedersen": pd.DataFrame(),
        "resumen_supervivencia": {},
        "detalle_supervivencia": pd.DataFrame(),
        "historico": _serie_historica(datos, cargo, distrito),
    }

    if anio_base is not None:
        comparacion = construir_comparacion(
            datos,
            anio_base,
            anio_actual,
            cargo,
            distrito,
            ausencias_son_cero=ausencias,
        )
        if not comparacion.empty:
            tabla_fuerzas = agregar_por_fuerza(comparacion)
            if not tabla_fuerzas.empty:
                pedersen, detalle_pedersen = calcular_indice_pedersen(
                    tabla_fuerzas, mitigar_sesgo=ausencias
                )
                resumen_supervivencia, detalle_supervivencia = (
                    analizar_supervivencia(tabla_fuerzas)
                )
                estado.update(
                    {
                        "modo": "comparacion",
                        "comparacion": comparacion,
                        "tabla_fuerzas": tabla_fuerzas,
                        "pedersen": pedersen,
                        "detalle_pedersen": detalle_pedersen,
                        "resumen_supervivencia": resumen_supervivencia,
                        "detalle_supervivencia": detalle_supervivencia,
                    }
                )
    return estado


def _render_tab_inicio(
    filtros: dict[str, Any], estado: dict[str, Any]
) -> None:
    cargo = estado["cargo"]
    distrito = estado["distrito"]
    cobertura = _resumen_cobertura(filtros["datos"], cargo)
    anios = " · ".join(map(str, cobertura["anios"])) or "s/d"

    _hero(
        "OBSERVATORIO ELECTORAL · LA RIOJA",
        "Lectura rigurosa del poder político",
        (
            "Resultados, territorio, volatilidad y prospectiva en una misma "
            "lectura. Los indicadores se calculan sobre datos electorales "
            "cargados y homologados."
        ),
    )

    columnas = st.columns(4)
    columnas[0].metric("Elecciones cargadas", str(cobertura["elecciones"]))
    columnas[1].metric("Fuerzas en el corte", str(cobertura["fuerzas"]))
    columnas[2].metric("Circuitos", str(cobertura["circuitos"]))
    columnas[3].metric("Distritos", str(cobertura["distritos"]))

    if estado["modo"] == "comparacion":
        tabla = estado["tabla_fuerzas"]
        primera = tabla.iloc[0]
        segunda = tabla.iloc[1] if len(tabla) > 1 else None
        resumen = estado["resumen_supervivencia"]
        panorama = f"""
        <div class="agora-panorama">
          <div class="agora-panorama-item">
            <span>Fuerza líder · {estado['anio_actual']}</span>
            <strong>{escapar_html(texto(primera['fuerza']))}</strong>
            <em>{fmt_pct(primera['pct_actual'])}</em>
          </div>
          <div class="agora-panorama-item">
            <span>Segunda fuerza · {estado['anio_actual']}</span>
            <strong>{escapar_html(texto(segunda['fuerza']) if segunda is not None else 's/d')}</strong>
            <em>{fmt_pct(segunda['pct_actual']) if segunda is not None else 's/d'}</em>
          </div>
          <div class="agora-panorama-item">
            <span>Volatilidad de Pedersen</span>
            <strong>Desplazamiento neto agregado</strong>
            <em>{fmt_pct(estado['pedersen'])}</em>
          </div>
          <div class="agora-panorama-item">
            <span>Supervivencia partidaria</span>
            <strong>{resumen.get('fuerzas_supervivientes', 0)} de {resumen.get('fuerzas_iniciales', 0)} fuerzas</strong>
            <em>{fmt_pct(resumen.get('tasa_supervivencia', 0))}</em>
          </div>
        </div>
        """
        st.markdown(panorama, unsafe_allow_html=True)
        st.caption(
            f"Cobertura: {anios} · {cargo} · "
            f"{'Todos los distritos' if distrito == 'Todos' else distrito}."
        )
        st.markdown(
            """
            <div class="agora-callout">
              <strong>Recorrido sugerido.</strong>
              Empezá por <em>Resultados electorales</em>, revisá la
              distribución territorial y luego abrí <em>Volatilidad de
              Pedersen</em> para interpretar el cambio de bloque. La
              prospectiva y el informe asistido usan los mismos indicadores
              verificados.
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        participacion = estado["participacion"]
        primera = (
            texto(participacion.index[0]) if not participacion.empty else "s/d"
        )
        panorama = f"""
        <div class="agora-panorama">
          <div class="agora-panorama-item">
            <span>Fuerza líder · {estado['anio_actual']}</span>
            <strong>{escapar_html(primera)}</strong>
            <em>{fmt_pct(participacion.iloc[0]) if not participacion.empty else 's/d'}</em>
          </div>
          <div class="agora-panorama-item">
            <span>NEP actual</span>
            <strong>Partidos efectivos</strong>
            <em>{estado['nep_actual']:.2f}</em>
          </div>
          <div class="agora-panorama-item">
            <span>Votos positivos</span>
            <strong>Total del corte</strong>
            <em>{fmt_entero(estado['votos_actuales'])}</em>
          </div>
          <div class="agora-panorama-item">
            <span>Comparación</span>
            <strong>Se requiere otra elección del mismo cargo</strong>
            <em>Pendiente</em>
          </div>
        </div>
        """
        st.markdown(panorama, unsafe_allow_html=True)
        st.markdown(
            """
            <div class="agora-callout">
              <strong>El observatorio está listo para una lectura simple.</strong>
              Para habilitar volatilidad, supervivencia y prospectiva comparada,
              se requiere una elección anterior del mismo cargo y distrito.
            </div>
            """,
            unsafe_allow_html=True,
        )

    if es_admin():
        st.caption(
            "La sección de carga de planillas está disponible arriba, en "
            "«Carga de planillas · CSV, XLS y XLSX»."
        )


def _render_tab_resultados(
    filtros: dict[str, Any], estado: dict[str, Any]
) -> None:
    cargo = estado["cargo"]
    anio_actual = estado["anio_actual"]
    anio_base = estado["anio_base"]
    distrito = estado["distrito"]
    datos_crudos = filtros["datos_crudos"]
    homologacion = filtros["homologacion"]

    sin_mapear = fuerzas_sin_mapear(datos_crudos, homologacion)
    if sin_mapear:
        st.warning(
            "Hay fuerzas sin regla de homologación. Una variante de nombre "
            "podría computarse como fuerza distinta. Revisá la pestaña "
            "**Análisis territorial → Homologación**."
        )

    if estado["modo"] == "comparacion":
        tabla = estado["tabla_fuerzas"]
        primera = tabla.iloc[0]
        segunda = tabla.iloc[1] if len(tabla) > 1 else None
        _encabezado_seccion(
            f"{cargo} · {anio_base} → {anio_actual}",
            "Resultados electorales",
            (
                f"{'Todos los distritos' if distrito == 'Todos' else distrito} · "
                "participación agregada ponderada por votos cuando están "
                "disponibles. Nombres homologados."
            ),
        )

        columnas = st.columns(4)
        with columnas[0]:
            st.metric(
                f"Primera fuerza · {anio_actual}",
                _pct_metrica(primera["pct_actual"]),
                fmt_pts(primera["diferencia"]),
            )
            st.caption(texto(primera["fuerza"]))
        with columnas[1]:
            if segunda is not None:
                st.metric(
                    f"Segunda fuerza · {anio_actual}",
                    _pct_metrica(segunda["pct_actual"]),
                    fmt_pts(segunda["diferencia"]),
                )
                st.caption(texto(segunda["fuerza"]))
            else:
                st.metric(f"Segunda fuerza · {anio_actual}", "s/d")
        with columnas[2]:
            st.metric(
                "Volatilidad total (Pedersen)",
                _pct_metrica(estado["pedersen"]),
                "Con mitigación" if estado["ausencias"] else "Parcial",
            )
        with columnas[3]:
            st.metric(
                f"NEP · {anio_actual}",
                f"{estado['nep_actual']:.2f}",
                "Partidos efectivos",
            )

        st.divider()
        col_grafico, col_tabla = st.columns([3, 2])
        with col_grafico:
            st.subheader(f"Evolución electoral ({anio_base} vs. {anio_actual})")
            pivote = (
                tabla.set_index("fuerza")[["pct_base", "pct_actual"]]
                .rename(
                    columns={
                        "pct_base": str(anio_base),
                        "pct_actual": str(anio_actual),
                    }
                )
                .sort_values(str(anio_actual), ascending=False)
            )
            st.bar_chart(
                pivote,
                color=["#94a3b8", PALETA["azul"]],
                height=390,
                width="stretch",
            )
        with col_tabla:
            st.subheader("Distribución porcentual")
            tabla_evolucion = tabla[
                ["fuerza", "pct_base", "pct_actual", "diferencia"]
            ].rename(
                columns={
                    "fuerza": "Fuerza política",
                    "pct_base": f"% {anio_base}",
                    "pct_actual": f"% {anio_actual}",
                    "diferencia": "Variación",
                }
            )
            st.dataframe(
                tabla_evolucion,
                hide_index=True,
                width="stretch",
                column_config={
                    f"% {anio_actual}": st.column_config.ProgressColumn(
                        f"% {anio_actual}",
                        format="%.2f%%",
                        min_value=0,
                        max_value=100,
                    ),
                    f"% {anio_base}": st.column_config.NumberColumn(
                        f"% {anio_base}", format="%.2f%%"
                    ),
                    "Variación": st.column_config.NumberColumn(
                        "Variación", format="%+.2f pts"
                    ),
                },
            )

        historico = estado["historico"]
        if not historico.empty and historico["anio"].nunique() >= 2:
            top = tabla.head(6)["fuerza"].tolist()
            pivote_historico = (
                historico[historico["fuerza"].isin(top)]
                .pivot_table(
                    index="anio",
                    columns="fuerza",
                    values="porcentaje",
                    aggfunc="mean",
                )
                .sort_index()
            )
            pivote_historico.index.name = "Año"
            st.subheader("Serie histórica de las principales fuerzas")
            st.line_chart(
                pivote_historico,
                color=COLORES_SERIE[: len(pivote_historico.columns)],
                height=340,
                width="stretch",
            )
            st.caption(
                "La serie solo muestra años con datos cargados para este "
                "cargo y distrito. No se interpolan valores faltantes."
            )

        st.caption(
            "La participación agregada se pondera por votos positivos. Si "
            "faltan votos, se usa el promedio simple de los porcentajes de "
            "los circuitos."
        )
        st.download_button(
            "Descargar comparación (.csv)",
            data=csv_resultados(tabla),
            file_name=_nombre_archivo(
                ["comparacion", cargo, anio_base, anio_actual, distrito],
                ".csv",
            ),
            mime="text/csv",
        )
        return

    participacion = estado["participacion"]
    corte = estado["corte_actual"]
    if participacion.empty:
        st.warning(
            "No hay porcentajes ni votos suficientes para describir esta "
            "elección."
        )
        return

    _encabezado_seccion(
        f"{cargo} · {anio_actual}",
        "Resultados electorales",
        (
            f"{'Todos los distritos' if distrito == 'Todos' else distrito} · "
            "corte de una sola elección. Se requiere un ciclo anterior del mismo "
            "cargo para habilitar la comparación."
        ),
    )

    columnas = st.columns(4)
    columnas[0].metric(
        f"Primera fuerza · {anio_actual}",
        _pct_metrica(participacion.iloc[0]),
    )
    columnas[1].metric(
        "Número efectivo de partidos", f"{estado['nep_actual']:.2f}"
    )
    columnas[2].metric(
        "Circuitos incluidos", str(corte["circuito"].nunique())
    )
    columnas[3].metric("Votos positivos", fmt_entero(estado["votos_actuales"]))

    st.divider()
    col_grafico, col_tabla = st.columns([3, 2])
    with col_grafico:
        st.subheader("Participación por fuerza")
        st.bar_chart(
            participacion.sort_values(),
            horizontal=True,
            color=PALETA["azul"],
            height=390,
            width="stretch",
        )
    with col_tabla:
        st.subheader("Distribución porcentual")
        tabla = pd.DataFrame(
            {
                "Fuerza política": participacion.index,
                "Porcentaje": participacion.values.round(2),
            }
        )
        tabla["Votos positivos"] = (
            tabla["Fuerza política"].map(votos_por_fuerza(corte)).values
        )
        st.dataframe(
            tabla,
            hide_index=True,
            width="stretch",
            column_config={
                "Porcentaje": st.column_config.ProgressColumn(
                    "Porcentaje",
                    format="%.2f%%",
                    min_value=0,
                    max_value=100,
                ),
                "Votos positivos": st.column_config.NumberColumn(
                    "Votos positivos", format="%d"
                ),
            },
        )

    st.download_button(
        "Descargar resultados filtrados (.csv)",
        data=csv_resultados(corte),
        file_name=_nombre_archivo(
            ["resultados", cargo, anio_actual, distrito], ".csv"
        ),
        mime="text/csv",
    )
    st.markdown(
        """
        <div class="agora-callout">
          <strong>¿Qué falta para el análisis de volatilidad?</strong>
          Se requieren los resultados de otra elección del mismo cargo y distrito.
          Pedersen necesita dos ciclos comparables y asigna 0,0% a las fuerzas
          nuevas o extintas para no sobreestimar la estabilidad.
        </div>
        """,
        unsafe_allow_html=True,
    )


def votos_por_fuerza(corte: pd.DataFrame) -> pd.Series:
    """Serie auxiliar de votos por fuerza para la tabla de una elección."""
    return pd.to_numeric(
        corte["votos_positivos"], errors="coerce"
    ).groupby(corte["fuerza"]).sum(min_count=1)


def _render_tab_territorial(
    filtros: dict[str, Any], estado: dict[str, Any]
) -> None:
    cargo = estado["cargo"]
    anio_base = estado["anio_base"]
    anio_actual = estado["anio_actual"]
    distrito = estado["distrito"]
    corte_actual = estado["corte_actual"]

    _encabezado_seccion(
        f"{cargo} · {anio_actual}",
        "Análisis territorial",
        (
            "Desempeño por circuito y concentración de la competencia. "
            "El mapa se muestra solo cuando la planilla contiene coordenadas "
            "geográficas verificables."
        ),
    )

    geo = _columnas_geo(corte_actual)
    if geo is not None:
        lat, lon = geo
        mapa = corte_actual.rename(columns={lat: "lat", lon: "lon"})
        mapa = mapa[["lat", "lon"]].apply(pd.to_numeric, errors="coerce")
        mapa = mapa.dropna()
        if not mapa.empty:
            st.subheader("Mapa electoral territorial")
            st.map(mapa, size=30)
    else:
        st.caption(
            "Mapa electoral: no hay coordenadas geográficas verificables en "
            "esta carga. El análisis se presenta por circuito."
        )

    tabla_fuerzas = estado["tabla_fuerzas"]
    fuerzas_disponibles = tabla_fuerzas["fuerza"].astype(str).tolist()
    if not fuerzas_disponibles:
        st.warning("No hay fuerzas disponibles para el desglose territorial.")
        return
    fuerza_sel = st.selectbox(
        "Fuerza política para el desglose",
        fuerzas_disponibles,
        key="agora_fuerza_territorial",
    )

    if estado["modo"] == "comparacion":
        comparacion = estado["comparacion"]
        territorial = comparacion[
            comparacion["fuerza"].astype(str).eq(fuerza_sel)
        ].sort_values("diferencia", ascending=False)
        if territorial.empty:
            st.warning("No hay desagregado territorial para esa fuerza.")
        else:
            st.subheader(f"Desempeño territorial: {fuerza_sel}")
            col_tabla, col_resumen = st.columns([3, 2])
            with col_tabla:
                tabla_territorial = territorial[
                    ["circuito", "pct_base", "pct_actual", "diferencia"]
                ].rename(
                    columns={
                        "circuito": "Circuito / Zona",
                        "pct_base": f"% {anio_base}",
                        "pct_actual": f"% {anio_actual}",
                        "diferencia": "Variación",
                    }
                )
                st.dataframe(
                    tabla_territorial,
                    hide_index=True,
                    width="stretch",
                    column_config={
                        f"% {anio_actual}": st.column_config.ProgressColumn(
                            f"% {anio_actual}",
                            format="%.2f%%",
                            min_value=0,
                            max_value=100,
                        ),
                        f"% {anio_base}": st.column_config.NumberColumn(
                            f"% {anio_base}", format="%.2f%%"
                        ),
                        "Variación": st.column_config.NumberColumn(
                            "Variación", format="%+.2f pts"
                        ),
                    },
                )
            with col_resumen:
                mejor = territorial.iloc[0]
                peor = territorial.iloc[-1]
                st.success(
                    f"**Mayor crecimiento:** {mejor['circuito']}\n\n"
                    f"`{fmt_pts(mejor['diferencia'])}`"
                )
                st.warning(
                    f"**Mayor contracción:** {peor['circuito']}\n\n"
                    f"`{fmt_pts(peor['diferencia'])}`"
                )
            st.subheader("Variación por circuito")
            st.bar_chart(
                territorial.set_index("circuito")["diferencia"],
                horizontal=True,
                color=PALETA["azul"],
                height=320,
                width="stretch",
            )
            st.download_button(
                "Descargar desglose territorial (.csv)",
                data=csv_resultados(territorial),
                file_name=_nombre_archivo(
                    ["territorial", fuerza_sel, anio_actual], ".csv"
                ),
                mime="text/csv",
            )
    else:
        territorial = corte_actual[
            corte_actual["fuerza"].astype(str).eq(fuerza_sel)
        ].copy()
        if territorial.empty:
            st.warning("No hay desagregado territorial para esa fuerza.")
        else:
            st.subheader(f"Desempeño territorial: {fuerza_sel}")
            tabla_territorial = territorial[
                ["circuito", "porcentaje", "votos_positivos"]
            ].rename(
                columns={
                    "circuito": "Circuito / Zona",
                    "porcentaje": "Porcentaje",
                    "votos_positivos": "Votos positivos",
                }
            ).sort_values("Porcentaje", ascending=False)
            st.dataframe(
                tabla_territorial,
                hide_index=True,
                width="stretch",
                column_config={
                    "Porcentaje": st.column_config.ProgressColumn(
                        "Porcentaje",
                        format="%.2f%%",
                        min_value=0,
                        max_value=100,
                    ),
                    "Votos positivos": st.column_config.NumberColumn(
                        "Votos positivos", format="%d"
                    ),
                },
            )
            st.subheader("Participación por circuito")
            st.bar_chart(
                territorial.set_index("circuito")["porcentaje"],
                horizontal=True,
                color=PALETA["azul"],
                height=320,
                width="stretch",
            )
            st.download_button(
                "Descargar desglose territorial (.csv)",
                data=csv_resultados(territorial),
                file_name=_nombre_archivo(
                    ["territorial", fuerza_sel, anio_actual], ".csv"
                ),
                mime="text/csv",
            )

    st.divider()
    st.subheader("Número efectivo de partidos por circuito")
    tabla_nep = _nep_por_circuito(corte_actual)
    if tabla_nep.empty:
        st.caption("No hay circuitos con datos suficientes.")
    else:
        col_tabla, col_metrica = st.columns([3, 1])
        with col_tabla:
            st.dataframe(
                tabla_nep,
                hide_index=True,
                width="stretch",
                column_config={
                    "NEP": st.column_config.NumberColumn("NEP", format="%.2f"),
                },
            )
        with col_metrica:
            st.metric(
                "NEP promedio simple de los circuitos",
                f"{tabla_nep['NEP'].mean():.2f}",
            )
        st.caption(
            "El NEP por circuito describe la fragmentación local; no reemplaza "
            "el NEP agregado del distrito."
        )

    st.divider()
    with st.expander("Homologación de fuerzas aplicada"):
        _render_homologacion_block(
            filtros["datos_crudos"],
            filtros["homologacion"],
            cargo,
            anio_base,
            anio_actual,
            distrito,
        )


def _render_tab_pedersen(
    filtros: dict[str, Any], estado: dict[str, Any]
) -> None:
    anio_base = estado["anio_base"]
    anio_actual = estado["anio_actual"]
    if estado["modo"] != "comparacion":
        _encabezado_seccion(
            f"{estado['cargo']} · {anio_actual}",
            "Volatilidad de Pedersen",
            (
                "El índice requiere dos elecciones comparables del mismo "
                "cargo. Se requiere un ciclo anterior para calcularlo."
            ),
        )
        st.info(
            "Todavía no hay un ciclo base. Cuando cargues otra elección, esta "
            "pestaña mostrará el índice, el detalle por fuerza y la "
            "descomposición de supervivencia."
        )
        st.markdown(
            """
            <div class="agora-callout">
              <strong>Método.</strong>
              Pedersen mide el desplazamiento neto agregado:
              <code>VT = Σ |V(i,t) − V(i,t−1)| / 2</code>. La mitigación de
              supervivencia asigna 0,0% a las fuerzas nuevas o extintas.
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    pedersen = float(estado["pedersen"] or 0.0)
    detalle = estado["detalle_pedersen"]
    resumen = estado["resumen_supervivencia"]
    detalle_supervivencia = estado["detalle_supervivencia"]

    _encabezado_seccion(
        f"{estado['cargo']} · {anio_base} → {anio_actual}",
        "Volatilidad de Pedersen",
        (
            "Desplazamiento neto de la participación agregada y control del "
            "sesgo de supervivencia. El índice no prueba transferencias "
            "individuales de votos."
        ),
    )

    col_metrica, col_lectura = st.columns([1, 2])
    with col_metrica:
        st.metric("Índice de Pedersen", _pct_metrica(pedersen))
        st.progress(min(max(pedersen / 50.0, 0.0), 1.0))
        st.caption(
            "La barra referencia 50 puntos porcentuales como escala de "
            "lectura rápida; no es un umbral normativo."
        )
    with col_lectura:
        st.markdown(
            f"""
            <div class="agora-callout">
              <strong>Lectura del período.</strong>
              El índice observado es <strong>{fmt_pct(pedersen)}</strong>.
              La descomposición separa el cambio de las fuerzas que
              sobreviven del componente de nuevas fuerzas y fuerzas extintas.
              La mitigación está
              <strong>{'activada' if estado['ausencias'] else 'desactivada'}</strong>.
            </div>
            """,
            unsafe_allow_html=True,
        )

    if detalle.empty:
        st.warning("No hay detalle de Pedersen para mostrar.")
    else:
        if detalle["incompleto"].any():
            st.warning(
                "El índice es parcial: hay fuerzas sin datos en uno de los "
                "períodos. Activá la mitigación para computarlas con 0,0%."
            )
        st.subheader("Aporte de cada fuerza al índice")
        aporte = (
            detalle[["fuerza", "aporte_pedersen"]]
            .sort_values("aporte_pedersen", ascending=True)
        )
        st.bar_chart(
            aporte.set_index("fuerza")["aporte_pedersen"],
            horizontal=True,
            color=PALETA["azul"],
            height=max(260, 34 * len(aporte)),
            width="stretch",
        )
        tabla_pedersen = detalle[
            [
                "fuerza",
                "estado",
                "pct_base",
                "pct_actual",
                "diferencia_abs",
                "aporte_pedersen",
            ]
        ].rename(
            columns={
                "fuerza": "Fuerza política",
                "estado": "Estado",
                "pct_base": f"% {anio_base}",
                "pct_actual": f"% {anio_actual}",
                "diferencia_abs": "|Δ|",
                "aporte_pedersen": "Aporte a Pedersen",
            }
        )
        st.dataframe(
            tabla_pedersen,
            hide_index=True,
            width="stretch",
            column_config={
                "Aporte a Pedersen": st.column_config.ProgressColumn(
                    "Aporte a Pedersen",
                    format="%.2f%%",
                    min_value=0,
                    max_value=float(max(1.0, tabla_pedersen["Aporte a Pedersen"].max())),
                ),
                **{
                    columna: st.column_config.NumberColumn(
                        columna, format="%.2f%%"
                    )
                    for columna in [
                        f"% {anio_base}",
                        f"% {anio_actual}",
                        "|Δ|",
                    ]
                },
            },
        )
        st.download_button(
            "Descargar detalle Pedersen (.csv)",
            data=csv_resultados(detalle),
            file_name=_nombre_archivo(
                ["pedersen", estado["cargo"], anio_base, anio_actual], ".csv"
            ),
            mime="text/csv",
        )

    st.divider()
    st.subheader("Supervivencia de las fuerzas políticas")
    st.markdown(
        "Se considera **sobreviviente** a una fuerza con participación mayor "
        "que cero en ambos períodos. Las fuerzas nuevas, las extintas y los "
        "datos incompletos se identifican por separado."
    )
    if not resumen:
        st.warning("No hay datos comparables para calcular la supervivencia.")
        return

    met1, met2, met3 = st.columns(3)
    met1.metric(
        "Tasa de supervivencia",
        f"{resumen['tasa_supervivencia']:.1f}%",
        (
            f"{resumen['fuerzas_supervivientes']} de "
            f"{resumen['fuerzas_iniciales']} fuerzas de {anio_base}"
        ),
    )
    met2.metric(
        f"Peso de sobrevivientes en {anio_base}",
        f"{resumen['peso_base_supervivientes']:.1f}%",
        f"del voto de {anio_base}",
    )
    met3.metric(
        "Componente nuevas/extintas",
        f"{resumen['pedersen_nuevas_y_extintas']:.2f}%",
        f"sobre {resumen['pedersen_total']:.2f}% total",
    )
    st.markdown(
        "**Descomposición descriptiva de Pedersen**  \n"
        f"Fuerzas presentes en ambos períodos: "
        f"{resumen['pedersen_supervivientes']:.2f} puntos. "
        f"Fuerzas nuevas o extintas: "
        f"{resumen['pedersen_nuevas_y_extintas']:.2f} puntos."
    )
    st.caption(
        "La segunda cifra muestra cuánto de la variación absoluta corresponde "
        "a entradas y salidas de fuerzas; no demuestra transferencia directa "
        "de votos entre partidos."
    )
    if resumen["filas_incompletas"]:
        st.warning(
            f"Hay {resumen['filas_incompletas']} fuerzas con datos faltantes "
            "en algún período. Se clasifican como incompletas hasta completar "
            "esos datos."
        )
    if not detalle_supervivencia.empty:
        st.dataframe(
            detalle_supervivencia.rename(
                columns={
                    "fuerza": "Fuerza política",
                    "estado": "Estado",
                    "pct_base": f"% {anio_base}",
                    "pct_actual": f"% {anio_actual}",
                    "variacion": "Variación (puntos)",
                }
            ),
            hide_index=True,
            width="stretch",
            column_config={
                **{
                    columna: st.column_config.NumberColumn(
                        columna, format="%.2f%%"
                    )
                    for columna in [f"% {anio_base}", f"% {anio_actual}"]
                },
                "Variación (puntos)": st.column_config.NumberColumn(
                    "Variación (puntos)", format="%+.2f pts"
                ),
            },
        )
    st.info(
        "**Límite metodológico:** la tasa depende de que los nombres y las "
        "alianzas estén armonizados entre elecciones y de que cada fuerza "
        "ausente tenga un 0 explícito o se aplique la mitigación. Un registro "
        "omitido no equivale por sí solo a una fuerza extinta."
    )


def _render_tab_sistema_partidos(
    filtros: dict[str, Any], estado: dict[str, Any]
) -> None:
    _encabezado_seccion(
        f"{estado['cargo']} · {estado['anio_actual']}",
        "Sistema de partidos",
        (
            "Fragmentación, concentración y número efectivo de partidos. "
            "Los indicadores describen la distribución observada; no explican "
            "por sí solos las causas del voto."
        ),
    )

    participacion = estado["participacion"]
    if participacion.empty:
        st.warning(
            "No hay participaciones disponibles para calcular el sistema de "
            "partidos con la selección actual."
        )
        return

    total = float(participacion.sum())
    hhi = (
        float(((participacion / total) ** 2).sum()) if total > 0 else 0.0
    )
    fragmentacion = max(0.0, min(100.0, (1.0 - hhi) * 100.0))
    mayor = float(participacion.max())

    columnas = st.columns(4)
    columnas[0].metric("NEP general", f"{estado['nep_actual']:.2f}")
    columnas[1].metric("Agrupaciones observadas", str(len(participacion)))
    columnas[2].metric("Mayor participación", _pct_metrica(mayor))
    columnas[3].metric(
        "Fragmentación (1 − HHI)", _pct_metrica(fragmentacion)
    )

    st.markdown(
        f"""
        <div class="agora-callout">
          <strong>Lectura del sistema.</strong>
          El NEP de Laakso y Taagepera es
          <strong>{estado['nep_actual']:.2f}</strong>: equivale a ese número
          de partidos de igual tamaño. La fuerza principal reúne
          <strong>{fmt_pct(mayor)}</strong> de la participación y el índice
          de fragmentación es <strong>{fmt_pct(fragmentacion)}</strong>.
          Un NEP alto indica dispersión del voto; no implica por sí mismo
          inestabilidad futura.
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.subheader("NEP por circuito")
    filas: list[dict[str, Any]] = []
    for circuito, grupo in estado["corte_actual"].groupby(
        "circuito", dropna=False
    ):
        serie = serie_agregada(grupo, "porcentaje", "votos_positivos")
        if serie.empty:
            continue
        votos = pd.to_numeric(
            grupo["votos_positivos"], errors="coerce"
        ).sum(min_count=1)
        filas.append(
            {
                "Circuito": texto(circuito) or "General",
                "NEP": round(calcular_nep(serie), 4),
                "Fuerzas": len(serie),
                "Votos positivos": votos,
            }
        )
    tabla_circuitos = pd.DataFrame(filas)
    if tabla_circuitos.empty:
        st.info("No hay circuitos con participación suficiente para el desglose.")
    else:
        tabla_circuitos = tabla_circuitos.sort_values(
            "NEP", ascending=False
        )
        if len(tabla_circuitos) > 1:
            st.bar_chart(
                tabla_circuitos.set_index("Circuito")["NEP"],
                horizontal=True,
                color=PALETA["azul"],
                height=max(220, 34 * len(tabla_circuitos)),
                width="stretch",
            )
        st.dataframe(
            tabla_circuitos,
            hide_index=True,
            width="stretch",
            column_config={
                "NEP": st.column_config.NumberColumn("NEP", format="%.2f"),
                "Votos positivos": st.column_config.NumberColumn(
                    "Votos positivos", format="%d"
                ),
            },
        )

    if estado["modo"] == "comparacion":
        comparacion = estado["comparacion"]
        base = serie_agregada(comparacion, "pct_base", "votos_base")
        actual = serie_agregada(comparacion, "pct_actual", "votos_actual")
        nep_base = calcular_nep(base)
        nep_actual = calcular_nep(actual)
        st.subheader("Cambio entre elecciones comparables")
        columnas = st.columns(2)
        columnas[0].metric(
            f"NEP · {estado['anio_base']}",
            f"{nep_base:.2f}",
            f"{nep_actual - nep_base:+.2f} vs. {estado['anio_actual']}",
        )
        columnas[1].metric(
            f"NEP · {estado['anio_actual']}",
            f"{nep_actual:.2f}",
            f"{len(actual)} agrupaciones",
        )
        st.caption(
            "Ambos NEP se calculan con la misma categoría y territorio. Las "
            "fuerzas ausentes se incorporan según la opción de mitigación."
        )

    st.markdown(
        """
        **Fórmula.** `NEP = 1 / Σ(pᵢ²)`, donde `pᵢ` es la proporción de votos
        positivos de cada agrupación. La fragmentación se informa como
        `1 − HHI`, con `HHI = Σ(pᵢ²)`.
        """
    )


def _render_tab_metodologia(
    filtros: dict[str, Any], estado: dict[str, Any]
) -> None:
    _encabezado_seccion(
        "Trazabilidad académica",
        "Metodología y fuentes",
        (
            "Reglas de lectura, normalización, homologación e indicadores. "
            "La interpretación se mantiene separada de los datos comprobados."
        ),
    )

    st.markdown(
        """
        ### 1. Datos y trazabilidad
        Cada planilla se guarda completa en la base SQLite antes de exponer
        los resultados normalizados. La app conserva el archivo original, su
        huella digital y un resumen de la hoja, el encabezado detectado y el
        mapeo de columnas. Los totales, subtotales y votos nulos, blancos,
        recurridos o impugnados se excluyen de los votos positivos, pero
        permanecen en la fuente original para auditoría.

        ### 2. Normalización
        Los encabezados se reconocen sin distinguir mayúsculas, tildes ni
        espacios. Se aceptan variantes como *Agrupación Política*, *Partido*,
        *Alianza*, *Lista* o *Fuerza Política*, y columnas de circuito, mesa,
        departamento, categoría y año. Cuando la detección automática no
        alcanza, la interfaz permite elegir la fila de encabezado y mapear
        cada campo manualmente antes de importar.

        ### 3. Homologación
        Dos denominaciones distintas no se fusionan automáticamente. Las
        equivalencias se aplican mediante reglas explícitas y auditables,
        editables desde la barra lateral y descargables como CSV.
        """
    )

    st.markdown(
        r"""
        ### 4. Indicadores
        **Volatilidad total de Pedersen**

        $$VT = \frac{1}{2}\sum_i \left|V_{i,t} - V_{i,t-1}\right|$$

        **Número efectivo de partidos (Laakso y Taagepera)**

        $$NEP = \frac{1}{\sum_i p_i^2}$$

        Pedersen mide desplazamiento neto agregado, no transferencias
        individuales de votos. El NEP describe la concentración del sistema
        partidario. Ambos requieren comparar la misma categoría, territorio e
        instancia electoral.
        """
    )

    st.markdown(
        """
        ### 5. Limitaciones
        - Los porcentajes de una planilla pueden usar denominadores distintos.
          Cuando hay votos positivos, la app recalcula la participación sobre
          esa base y evita mezclar nulos o blancos.
        - La cobertura territorial depende de la granularidad de la fuente.
        - La homologación introduce decisiones metodológicas que deben
          documentarse junto con los resultados.
        - Los escenarios 2027 son condicionales y no probabilísticos.
        - Los informes con IA interpretan indicadores calculados; no crean
          cifras ni resultados.
        """
    )

    st.subheader("Fuentes incorporadas")
    fuentes = listar_fuentes_db()
    if fuentes.empty:
        st.info(
            "Todavía no hay planillas originales registradas en esta base. "
            "La semilla analítica, cuando está activa, se reconstruye desde "
            "`data/seed_resultados.csv`."
        )
    else:
        vista = fuentes.copy()
        vista["tamano"] = vista["tamano"].map(
            lambda valor: f"{float(valor) / 1024:,.1f} KB"
            if pd.notna(valor)
            else "s/d"
        )
        vista = vista.rename(
            columns={
                "archivo": "Archivo",
                "formato": "Formato",
                "tamano": "Tamaño",
                "importado_en": "Importado",
                "huella": "Huella SHA-256",
            }
        )
        st.dataframe(
            vista[
                [
                    "Archivo",
                    "Formato",
                    "Tamaño",
                    "Importado",
                    "Huella SHA-256",
                ]
            ],
            hide_index=True,
            width="stretch",
        )

    st.subheader("Descargas metodológicas")
    col_resultados, col_homologacion = st.columns(2)
    with col_resultados:
        st.download_button(
            "Descargar resultados normalizados",
            data=csv_resultados(filtros["datos"]),
            file_name="agora_resultados_normalizados.csv",
            mime="text/csv",
            width="stretch",
            key="agora_descarga_metodologia_resultados",
        )
    with col_homologacion:
        st.download_button(
            "Descargar tabla de homologación",
            data=filtros["homologacion"].to_csv(index=False).encode("utf-8-sig"),
            file_name="agora_homologacion.csv",
            mime="text/csv",
            width="stretch",
            key="agora_descarga_metodologia_homologacion",
        )

    st.caption(
        "El motor analítico de reglas permanece disponible si Groq no "
        "responde. Los informes indican explícitamente qué motor los generó."
    )


def _render_tab_prospectiva(
    filtros: dict[str, Any], estado: dict[str, Any]
) -> None:
    anio_actual = estado["anio_actual"]
    _encabezado_seccion(
        f"{estado['cargo']} · {anio_actual}",
        "Prospectiva 2027",
        (
            "Marco de escenarios construido con los indicadores observados. "
            "No se asignan probabilidades ni se proyectan votos."
        ),
    )

    if estado["modo"] != "comparacion":
        st.info(
            "La prospectiva comparada requiere al menos dos elecciones del "
            "mismo cargo. Se requiere un ciclo anterior para habilitar el marco de "
            "escenarios."
        )
        columnas = st.columns(3)
        columnas[0].metric(
            "NEP actual", f"{estado['nep_actual']:.2f}"
        )
        columnas[1].metric(
            "Fuerzas en el corte", str(len(estado["participacion"]))
        )
        columnas[2].metric(
            "Circuitos", str(estado["corte_actual"]["circuito"].nunique())
        )
        st.markdown(
            """
            <div class="agora-callout">
              <strong>Punto de partida.</strong>
              La línea de base está disponible. La prospectiva se activa
              cuando exista un período de comparación y, por lo tanto, un
              desplazamiento neto que interpretar.
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    resumen = estado["resumen_supervivencia"]
    detalle = estado["detalle_pedersen"]
    nuevas = int(
        (detalle["estado"] == "Nueva").sum() if not detalle.empty else 0
    )
    extintas = int(
        (detalle["estado"] == "Extinta").sum() if not detalle.empty else 0
    )

    columnas = st.columns(4)
    columnas[0].metric(
        "Volatilidad observada", _pct_metrica(estado["pedersen"])
    )
    columnas[1].metric("NEP actual", f"{estado['nep_actual']:.2f}")
    columnas[2].metric("Fuerzas nuevas", str(nuevas))
    columnas[3].metric("Fuerzas extintas", str(extintas))

    col_escenario, col_factores = st.columns([3, 2])
    with col_escenario:
        st.subheader("Lectura del escenario")
        st.markdown(
            f"""
            <div class="agora-panorama agora-panorama--stack">
              <div class="agora-panorama-item">
                <span>Desplazamiento neto</span>
                <strong>Pedersen observado</strong>
                <em>{fmt_pct(estado['pedersen'])}</em>
              </div>
              <div class="agora-panorama-item">
                <span>Continuidad</span>
                <strong>{resumen.get('fuerzas_supervivientes', 0)} de {resumen.get('fuerzas_iniciales', 0)} fuerzas sobreviven</strong>
                <em>{fmt_pct(resumen.get('tasa_supervivencia', 0))}</em>
              </div>
              <div class="agora-panorama-item">
                <span>Renovación</span>
                <strong>{nuevas} nuevas · {extintas} extintas</strong>
                <em>{fmt_pct(resumen.get('pedersen_nuevas_y_extintas', 0))}</em>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(
            "La comparación permite leer si el cambio proviene de fuerzas "
            "que continúan, de entradas nuevas o de salidas del sistema de "
            "partidos. Esas tres dimensiones no son intercambiables."
        )
    with col_factores:
        st.subheader("Factores a monitorear")
        st.markdown(
            """
            - Composición y alcance de las alianzas entre elecciones.
            - Consolidación territorial de las fuerzas emergentes.
            - Continuidad de las fuerzas que sobreviven.
            - Calidad y cobertura de los datos por circuito.
            - Reglas de homologación aplicadas a cada año.
            """
        )
        st.subheader("Preguntas para 2027")
        st.markdown(
            """
            1. ¿Las fuerzas nuevas sostienen su participación o dependen de
               una coyuntura?
            2. ¿El desplazamiento se concentra en pocos circuitos?
            3. ¿Las alianzas cambian la identidad de las fuerzas comparadas?
            4. ¿La cobertura de datos permite repetir la misma metodología?
            """
        )

    st.markdown(
        """
        <div class="agora-callout">
          <strong>Alcance.</strong>
          Esta pestaña no es un pronóstico. Presenta un marco de escenarios
          condicionado por los indicadores observados y por la calidad de la
          homologación. No se asignan probabilidades ni se extrapolan votos.
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_tab_informes(
    filtros: dict[str, Any], estado: dict[str, Any]
) -> None:
    _encabezado_seccion(
        f"{estado['cargo']} · {estado['anio_actual']}",
        "Informes IA",
        (
            "Generación de un dictamen politológico con separación explícita "
            "entre datos, indicadores e interpretación."
        ),
    )

    tabla_fuerzas = estado["tabla_fuerzas"]
    fuerzas_disponibles = tabla_fuerzas["fuerza"].astype(str).tolist()
    if not fuerzas_disponibles:
        st.warning("No hay fuerzas disponibles para generar un informe.")
        return
    fuerza_dictamen = st.selectbox(
        "Fuerza para el dictamen",
        fuerzas_disponibles,
        key="agora_fuerza_dictamen",
    )

    if estado["modo"] == "comparacion":
        contexto = construir_contexto_dictamen(
            fuerza_dictamen,
            tabla_fuerzas,
            estado["comparacion"],
            int(estado["anio_base"]),
            int(estado["anio_actual"]),
            estado["pedersen"],
            estado["resumen_supervivencia"],
            cargo=str(estado["cargo"]),
            distrito=estado["distrito"],
            n_registros=len(estado["comparacion"]),
        )
        clave = (
            f"comparacion|{fuerza_dictamen}|{estado['anio_base']}|"
            f"{estado['anio_actual']}|{estado['distrito']}|"
            f"{estado['cargo']}|{len(estado['comparacion'])}|"
            f"{float(estado['pedersen'] or 0):.4f}|"
            f"{_firma_homologacion(filtros['homologacion'])}"
        )
    else:
        corte = estado["corte_actual"]
        tabla_territorial = corte[
            ["circuito", "fuerza", "porcentaje"]
        ].rename(columns={"porcentaje": "pct_actual"})
        contexto = construir_contexto_dictamen(
            fuerza_dictamen,
            tabla_fuerzas,
            tabla_territorial,
            None,
            int(estado["anio_actual"]),
            None,
            None,
            cargo=str(estado["cargo"]),
            distrito=estado["distrito"],
            n_registros=len(corte),
        )
        clave = (
            f"una|{fuerza_dictamen}|{estado['anio_actual']}|"
            f"{estado['distrito']}|{estado['cargo']}|{len(corte)}|"
            f"{_firma_homologacion(filtros['homologacion'])}"
        )

    _render_dictamen(
        contexto,
        filtros["modelo_ollama"],
        clave,
    )
    st.caption(
        "El informe se genera con los datos de la selección actual. Si "
        "cambia la fuerza, el período o la homologación, la app solicita una "
        "nueva generación."
    )


# ---------------------------------------------------------------------------
# Estados sin datos y composición principal
# ---------------------------------------------------------------------------


def _render_onboarding() -> None:
    if not es_admin():
        _hero(
            "OBSERVATORIO ELECTORAL · LA RIOJA",
            "Consulta pública del observatorio",
            (
                "La base pública todavía no tiene resultados cargados. "
                "Cuando la administradora importe las planillas, los "
                "indicadores y las descargas estarán disponibles acá."
            ),
        )
        st.info(
            "No hace falta registrarse para consultar. La carga y la "
            "homologación de datos están reservadas a la administradora."
        )
        return

    _hero(
        "OBSERVATORIO ELECTORAL · LA RIOJA",
        "Lectura rigurosa del poder político",
        (
            "Ingesta de escrutinios, análisis territorial, volatilidad de "
            "Pedersen y prospectiva 2027. Empezá cargando una planilla."
        ),
    )
    st.markdown(
        """
        <div class="agora-onboard">
          <div class="agora-card">
            <span class="num">01 · INGESTA</span>
            <h3>Cargá una planilla</h3>
            <p>CSV, XLS o XLSX. Se aceptan formatos propios y el CSV oficial
            del CNE. La app normaliza, valida y conserva los datos crudos.</p>
          </div>
          <div class="agora-card">
            <span class="num">02 · MÉTODO</span>
            <h3>Calculá la volatilidad</h3>
            <p>Pedersen incorpora fuerzas nuevas y extintas con 0,0% para
            corregir el sesgo de supervivencia.</p>
          </div>
          <div class="agora-card">
            <span class="num">03 · DICTAMEN</span>
            <h3>Generá el análisis</h3>
            <p>Groq como proveedor principal, Gemini como respaldo, Ollama
            solo local y un motor de reglas siempre disponible.</p>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.info(
        "Todavía no hay resultados cargados. Abrí la sección **Carga de "
        "planillas** que está arriba, descargá la plantilla si la necesitás y "
        "subí una o varias planillas. Revisá la vista previa y confirmá la "
        "importación."
    )


def _render_tabs_vacio() -> None:
    tabs = st.tabs(PESTANAS)
    with tabs[0]:
        _render_onboarding()
    for tab in tabs[1:]:
        with tab:
            if es_admin():
                st.info(
                    "Cargá una planilla para habilitar esta sección del "
                    "observatorio."
                )
            else:
                st.info(
                    "Esta sección se habilitará cuando haya resultados "
                    "públicos cargados."
                )


def _render_dashboard(filtros: dict[str, Any]) -> None:
    estado = _construir_estado(filtros)
    tabs = st.tabs(PESTANAS)
    with tabs[0]:
        _render_tab_inicio(filtros, estado)
    with tabs[1]:
        _render_tab_resultados(filtros, estado)
    with tabs[2]:
        _render_tab_territorial(filtros, estado)
    with tabs[3]:
        _render_tab_pedersen(filtros, estado)
    with tabs[4]:
        _render_tab_sistema_partidos(filtros, estado)
    with tabs[5]:
        _render_tab_prospectiva(filtros, estado)
    with tabs[6]:
        _render_tab_informes(filtros, estado)
    with tabs[7]:
        _render_tab_metodologia(filtros, estado)

    st.markdown(
        """
        <div class="agora-footer">
          <strong>Ágora Electoral</strong> · Observatorio Electoral de La
          Rioja. Indicadores descriptivos: Pedersen mide desplazamiento neto
          de votos, no transferencias individuales. La prospectiva 2027 es un
          escenario de trabajo, no un pronóstico.
        </div>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    render_header()
    _render_acceso_admin()

    error_semilla: Exception | None = None
    try:
        asegurar_db_sembrada()
        asegurar_homologacion_sembrada()
    except Exception as error:  # noqa: BLE001 - se informa en la interfaz
        error_semilla = error

    datos_crudos = cargar_datos_db()
    homologacion = cargar_homologacion()
    datos = aplicar_homologacion(datos_crudos, homologacion)

    if es_admin():
        _render_carga_planillas(not datos.empty)
        if st.session_state.pop("agora_mostrar_importacion", False):
            st.success(
                st.session_state.get(
                    "agora_mensaje_importacion", "Planillas procesadas."
                )
            )

    filtros = _render_sidebar(datos, datos_crudos, homologacion)

    if error_semilla is not None:
        st.warning(
            "No se pudo preparar la base inicial. La consulta pública sigue "
            "disponible."
        )
        if es_admin():
            with st.expander("Detalle técnico"):
                st.code(str(error_semilla))

    if es_admin() and st.session_state.get("agora_homologacion_abierta"):
        _dialogo_homologacion(datos_crudos, homologacion)

    if filtros["datos"].empty:
        _render_tabs_vacio()
        return
    if filtros["cargo"] is None or filtros["anio_actual"] is None:
        st.warning(
            "No se pudieron determinar el cargo y la elección. Revisá las "
            "planillas cargadas."
        )
        _render_tabs_vacio()
        return
    _render_dashboard(filtros)
