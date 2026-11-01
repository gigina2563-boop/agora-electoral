"""Motor híbrido de dictamen: Groq → Gemini → Ollama local → reglas.

Groq es el proveedor principal de producción. Ollama solo se intenta en
entornos locales (`AGORA_ENTORNO=local`) o con `OLLAMA_ENABLED=true`. El motor
de reglas es determinista y no hace llamadas de red.
"""

from __future__ import annotations

import time
from typing import Any, Optional

import pandas as pd
import requests

from .config import (
    APP_VERSION,
    api_timeout,
    config,
    ollama_habilitado,
)
from .metrics import calcular_nep
from .utils import (
    clave_fuerza,
    escapar_md,
    fmt_numero,
    fmt_pct,
    fmt_pts,
    texto,
)


def construir_contexto_dictamen(
    fuerza: str,
    tabla_fuerzas: pd.DataFrame,
    tabla_territorial: pd.DataFrame,
    anio_base: int | None,
    anio_actual: int,
    pedersen: float | None,
    resumen_supervivencia: dict[str, Any] | None = None,
    *,
    cargo: str | None = None,
    distrito: str | None = None,
    n_registros: int | None = None,
) -> dict[str, Any]:
    """Reúne métricas verificadas para el prompt y el motor de reglas."""
    fila = (
        tabla_fuerzas[tabla_fuerzas["fuerza"].eq(fuerza)]
        if tabla_fuerzas is not None and not tabla_fuerzas.empty
        else pd.DataFrame()
    )
    pct_base = (
        float(fila.iloc[0]["pct_base"])
        if not fila.empty and pd.notna(fila.iloc[0]["pct_base"])
        else None
    )
    pct_actual = (
        float(fila.iloc[0]["pct_actual"])
        if not fila.empty and pd.notna(fila.iloc[0]["pct_actual"])
        else None
    )
    variacion = (
        pct_actual - pct_base
        if pct_base is not None and pct_actual is not None
        else None
    )
    nep_actual = (
        calcular_nep(tabla_fuerzas["pct_actual"])
        if tabla_fuerzas is not None and not tabla_fuerzas.empty
        else 0.0
    )

    territorial = (
        tabla_territorial[tabla_territorial["fuerza"].eq(fuerza)].copy()
        if tabla_territorial is not None and not tabla_territorial.empty
        else pd.DataFrame()
    )
    mejor_circuito = peor_circuito = None
    mejor_valor = peor_valor = None
    dispersion = None
    criterio = "participación"
    if not territorial.empty:
        if (
            "diferencia" in territorial.columns
            and territorial["diferencia"].notna().any()
        ):
            criterio = "variación"
            idx_mejor = territorial["diferencia"].idxmax()
            idx_peor = territorial["diferencia"].idxmin()
            mejor_circuito = texto(territorial.loc[idx_mejor, "circuito"])
            peor_circuito = texto(territorial.loc[idx_peor, "circuito"])
            mejor_valor = float(territorial.loc[idx_mejor, "diferencia"])
            peor_valor = float(territorial.loc[idx_peor, "diferencia"])
            dispersion = float(territorial["diferencia"].std(ddof=0))
        elif "pct_actual" in territorial.columns:
            idx_mejor = territorial["pct_actual"].idxmax()
            idx_peor = territorial["pct_actual"].idxmin()
            mejor_circuito = texto(territorial.loc[idx_mejor, "circuito"])
            peor_circuito = texto(territorial.loc[idx_peor, "circuito"])
            mejor_valor = float(territorial.loc[idx_mejor, "pct_actual"])
            peor_valor = float(territorial.loc[idx_peor, "pct_actual"])

    n_circuitos = (
        int(territorial["circuito"].nunique())
        if "circuito" in territorial.columns
        else 0
    )
    return {
        "fuerza": texto(fuerza),
        "anio_base": anio_base,
        "anio_actual": int(anio_actual),
        "cargo": texto(cargo) or "No especificado",
        "distrito": texto(distrito) or "Todos",
        "n_registros": int(n_registros or 0),
        "pct_base": pct_base,
        "pct_actual": pct_actual,
        "variacion": variacion,
        "pedersen": float(pedersen) if pedersen is not None else None,
        "nep_actual": nep_actual,
        "mejor_circuito": mejor_circuito,
        "peor_circuito": peor_circuito,
        "mejor_valor": mejor_valor,
        "peor_valor": peor_valor,
        "dispersion": dispersion,
        "n_circuitos": n_circuitos,
        "criterio_circuito": criterio,
        "resumen_supervivencia": resumen_supervivencia or {},
        "alcance": (
            "comparación entre elecciones"
            if anio_base is not None
            else "una sola elección"
        ),
    }


def construir_prompt(contexto: dict[str, Any]) -> str:
    """Prompt acotado, con separación explícita entre dato e interpretación."""
    c = contexto
    base = (
        f"{c['pct_base']:.2f}% en {c['anio_base']}"
        if c["pct_base"] is not None
        else "sin ciclo base comparable"
    )
    actual = (
        f"{c['pct_actual']:.2f}% en {c['anio_actual']}"
        if c["pct_actual"] is not None
        else "sin dato actual"
    )
    variacion = (
        f"{c['variacion']:+.2f} puntos porcentuales"
        if c["variacion"] is not None
        else "no calculable"
    )
    pedersen = (
        f"{c['pedersen']:.2f}%"
        if c["pedersen"] is not None
        else "no calculable (falta el ciclo base)"
    )
    mejor = (
        f"{c['mejor_circuito']} ({c['criterio_circuito']}: "
        f"{c['mejor_valor']:+.2f})"
        if c["mejor_circuito"] and c["mejor_valor"] is not None
        else "sin desagregado territorial"
    )
    peor = (
        f"{c['peor_circuito']} ({c['criterio_circuito']}: "
        f"{c['peor_valor']:+.2f})"
        if c["peor_circuito"] and c["peor_valor"] is not None
        else "sin desagregado territorial"
    )
    supervivencia = c.get("resumen_supervivencia") or {}
    extra_supervivencia = ""
    if supervivencia:
        extra_supervivencia = (
            f"\n- Tasa de supervivencia partidaria: "
            f"{supervivencia.get('tasa_supervivencia', 0):.2f}% "
            f"({supervivencia.get('fuerzas_supervivientes', 0)} de "
            f"{supervivencia.get('fuerzas_iniciales', 0)} fuerzas)."
            f"\n- Componente de Pedersen por entradas/salidas: "
            f"{supervivencia.get('pedersen_nuevas_y_extintas', 0):.2f} "
            "puntos."
        )

    return f"""Actuá como analista politológico senior del Observatorio Electoral de La Rioja (Ágora Electoral).

Redactá un dictamen en español rioplatense, tono técnico, sobrio y no partidario. Usá únicamente los datos verificados de este mensaje. No inventes causas, encuestas, resultados, alianzas ni tendencias que no estén respaldadas por los datos. La tabla de homologación ya fue aplicada: no cambies los nombres de las fuerzas.

Datos comprobados (fuente: resultados electorales cargados y homologados):
- Fuerza política: {c['fuerza']}
- Cargo: {c['cargo']}
- Distrito: {c['distrito']}
- Alcance: {c['alcance']}
- Registros comparados: {c['n_registros']}
- Circuitos comparados: {c['n_circuitos']}
- Años: {c['anio_base'] if c['anio_base'] is not None else 'sin base'} → {c['anio_actual']}

Indicadores calculados (no son datos de encuesta):
- Participación agregada: {base}; {actual}
- Variación: {variacion}
- Índice de Volatilidad Total de Pedersen del conjunto: {pedersen}
- Número efectivo de partidos (NEP) en {c['anio_actual']}: {c['nep_actual']:.2f}
- Circuito con mejor registro: {mejor}
- Circuito con peor registro: {peor}{extra_supervivencia}

Estructura obligatoria del informe, con estos encabezados exactos:
1. "## Datos comprobados": solo años, cargo, distrito, fuerza, circuitos y cantidad de registros.
2. "## Indicadores calculados": participación, variación, Pedersen, NEP y supervivencia. Aclarar que son indicadores derivados.
3. "## Interpretación politológica": lectura del desempeño y del territorio. Separar explícitamente lo descriptivo de la inferencia.
4. "## Prospectiva 2027 (escenario)": escenarios condicionales, nunca pronósticos ni certezas.
5. "## Nota metodológica": una o dos oraciones sobre límites, homologación y sesgo de supervivencia.

Reglas adicionales:
- No uses tablas.
- No atribuyas transferencias individuales de votos: Pedersen mide desplazamiento neto agregado.
- Las fuerzas nuevas o extintas se computan con 0,0% para mitigar el sesgo de supervivencia.
- Si falta el ciclo base, no inventes una variación interanual.
- Extensión máxima: 500 palabras.
"""


def _detalle_error(error: Exception) -> str:
    """Devuelve un diagnóstico sin exponer claves ni URLs sensibles."""
    respuesta = getattr(error, "response", None)
    estado = getattr(respuesta, "status_code", None)
    if estado:
        return f"{type(error).__name__} · HTTP {estado}"
    return type(error).__name__


def consultar_groq(prompt: str) -> tuple[Optional[str], str]:
    clave = config("GROQ_API_KEY")
    if not clave:
        return None, "Groq: sin GROQ_API_KEY"
    modelo = config("GROQ_MODEL", "openai/gpt-oss-120b")
    base = config("GROQ_API_BASE", "https://api.groq.com/openai/v1")
    try:
        respuesta = requests.post(
            f"{base.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {clave}",
                "Content-Type": "application/json",
                "User-Agent": f"Agora-Electoral/{APP_VERSION}",
            },
            json={
                "model": modelo,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Sos un analista politológico riguroso. "
                            "No inventás datos y separás datos de "
                            "interpretaciones."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                # Los modelos con razonamiento consumen tokens antes de la
                # respuesta final; 700 puede truncar las secciones exigidas.
                "max_tokens": 1600,
            },
            timeout=api_timeout("GROQ_TIMEOUT", 18),
        )
        respuesta.raise_for_status()
        datos = respuesta.json()
        contenido = (
            datos.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        if contenido:
            return contenido, f"Groq · {modelo}"
        return None, "Groq: respuesta vacía"
    except Exception as error:  # noqa: BLE001 - se informa y se degrada
        return None, f"Groq: {_detalle_error(error)}"


def consultar_gemini(prompt: str) -> tuple[Optional[str], str]:
    clave = config("GEMINI_API_KEY") or config("GOOGLE_API_KEY")
    if not clave:
        return None, "Gemini: sin GEMINI_API_KEY/GOOGLE_API_KEY"
    modelo = config("GEMINI_MODEL", "gemini-2.5-flash")
    base = config(
        "GEMINI_API_BASE",
        "https://generativelanguage.googleapis.com/v1beta",
    )
    try:
        respuesta = requests.post(
            f"{base.rstrip('/')}/models/{modelo}:generateContent",
            headers={
                "x-goog-api-key": clave,
                "Content-Type": "application/json",
                "User-Agent": f"Agora-Electoral/{APP_VERSION}",
            },
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": 700,
                },
            },
            timeout=api_timeout("GEMINI_TIMEOUT", 18),
        )
        respuesta.raise_for_status()
        datos = respuesta.json()
        partes = (
            datos.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [])
        )
        contenido = " ".join(
            texto(parte.get("text"))
            for parte in partes
            if isinstance(parte, dict)
        ).strip()
        if contenido:
            return contenido, f"Gemini · {modelo}"
        return None, "Gemini: respuesta vacía"
    except Exception as error:  # noqa: BLE001
        return None, f"Gemini: {_detalle_error(error)}"


def consultar_ollama(
    prompt: str, modelo: str = "gemma3:1b"
) -> tuple[Optional[str], str]:
    host = config("OLLAMA_HOST", "http://localhost:11434")
    url = f"{host.rstrip('/')}/api/generate"
    modelo = texto(modelo) or "gemma3:1b"
    try:
        respuesta = requests.post(
            url,
            json={
                "model": modelo,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "num_predict": 600,
                    "temperature": 0.2,
                    "num_ctx": 2048,
                },
            },
            timeout=api_timeout("OLLAMA_TIMEOUT", 3),
        )
        respuesta.raise_for_status()
        datos = respuesta.json()
        contenido = texto(datos.get("response"))
        if not contenido:
            contenido = texto(
                datos.get("message", {}).get("content")
                if isinstance(datos.get("message"), dict)
                else ""
            )
        if contenido:
            return contenido, f"Ollama local · {modelo}"
        return None, f"Ollama: respuesta vacía ({modelo})"
    except Exception as error:  # noqa: BLE001
        return None, f"Ollama: {_detalle_error(error)}"


def _texto_territorio(contexto: dict[str, Any]) -> str:
    c = contexto
    if not c.get("mejor_circuito") or not c.get("peor_circuito"):
        return (
            "No hay desagregado territorial suficiente para comparar "
            "circuitos."
        )
    return (
        f"El mejor registro territorial se observa en "
        f"**{escapar_md(c.get('mejor_circuito'))}** "
        f"({fmt_pts(c.get('mejor_valor'))} de "
        f"{c.get('criterio_circuito', 'variación')}), mientras que el más "
        f"débil aparece en **{escapar_md(c.get('peor_circuito'))}** "
        f"({fmt_pts(c.get('peor_valor'))})."
    )


def generar_dictamen_reglas(contexto: dict[str, Any]) -> str:
    """Motor analítico determinista: no hace red y responde de inmediato."""
    c = contexto
    fuerza = escapar_md(c.get("fuerza", "La fuerza"))
    anio_actual = c.get("anio_actual", "")
    anio_base = c.get("anio_base")
    pct_actual = c.get("pct_actual")
    pct_base = c.get("pct_base")
    variacion = c.get("variacion")
    pedersen = c.get("pedersen")

    datos = (
        f"- Fuerza: **{fuerza}**.\n"
        f"- Cargo: {escapar_md(c.get('cargo'))}.\n"
        f"- Distrito: {escapar_md(c.get('distrito'))}.\n"
        f"- Años comparados: {anio_base if anio_base is not None else 'sin base'} → {anio_actual}.\n"
        f"- Circuitos: {c.get('n_circuitos', 0)}.\n"
        f"- Registros: {c.get('n_registros', 0)}."
    )

    if pct_actual is None:
        indicadores = "- No hay una participación agregada calculable con los filtros elegidos."
    else:
        indicadores = (
            f"- Participación en {anio_actual}: **{fmt_pct(pct_actual)}**.\n"
            f"- Participación en el ciclo base: "
            f"**{fmt_pct(pct_base) if pct_base is not None else 's/d'}**."
        )
    if variacion is not None:
        indicadores += f"\n- Variación: **{fmt_pts(variacion)}**."
    else:
        indicadores += (
            "\n- Variación interanual: no calculable por falta de ciclo base."
        )
    indicadores += (
        f"\n- Índice de Pedersen del conjunto: "
        f"**{fmt_pct(pedersen) if pedersen is not None else 'no calculable'}**."
        f"\n- NEP en {anio_actual}: **{fmt_numero(c.get('nep_actual', 0))}**."
    )

    resumen = c.get("resumen_supervivencia") or {}
    if resumen:
        indicadores += (
            f"\n- Tasa de supervivencia partidaria: "
            f"**{fmt_pct(resumen.get('tasa_supervivencia', 0), 1)}** "
            f"({resumen.get('fuerzas_supervivientes', 0)} de "
            f"{resumen.get('fuerzas_iniciales', 0)} fuerzas)."
            f"\n- Componente de Pedersen por nuevas/extintas: "
            f"**{fmt_numero(resumen.get('pedersen_nuevas_y_extintas', 0))} "
            "puntos**."
        )

    if variacion is None:
        trayectoria = (
            "No hay un ciclo base comparable, por lo que no corresponde "
            "afirmar crecimiento, retención ni contracción interanual."
        )
    elif variacion >= 5:
        trayectoria = (
            f"Registra una expansión marcada de **{fmt_pts(variacion)}** "
            f"respecto de {anio_base}."
        )
    elif variacion >= 1:
        trayectoria = (
            f"Muestra un crecimiento moderado de **{fmt_pts(variacion)}** "
            f"frente a {anio_base}."
        )
    elif variacion > -1:
        trayectoria = (
            f"Mantiene una posición prácticamente estable "
            f"(**{fmt_pts(variacion)}**) frente a {anio_base}."
        )
    elif variacion > -5:
        trayectoria = (
            f"Presenta una contracción moderada de **{fmt_pts(variacion)}** "
            f"frente a {anio_base}."
        )
    else:
        trayectoria = (
            f"Acusa una caída pronunciada de **{fmt_pts(variacion)}** "
            f"frente a {anio_base}."
        )

    if pedersen is None:
        volatilidad = (
            "El Índice de Volatilidad Total de Pedersen no es calculable "
            "porque falta el ciclo base."
        )
    elif pedersen > 30:
        volatilidad = (
            f"El Índice de Pedersen es **{fmt_pct(pedersen)}**: alta "
            "volatilidad y reconfiguración profunda del sistema de partidos."
        )
    elif pedersen > 15:
        volatilidad = (
            f"El Índice de Pedersen es **{fmt_pct(pedersen)}**: "
            "fluctuación moderada con desplazamientos intermedios de votos."
        )
    else:
        volatilidad = (
            f"El Índice de Pedersen es **{fmt_pct(pedersen)}**: "
            "estabilidad relativa y baja dispersión de preferencias."
        )

    interpretacion = (
        f"{trayectoria} {volatilidad} {_texto_territorio(c)} "
        "La lectura es descriptiva: no demuestra transferencias "
        "individuales de votos ni intención de voto."
    )

    prospectiva = (
        "Hacia 2027, estos datos permiten construir escenarios de "
        "competencia, no un pronóstico. La captación de sellos extintos, la "
        "fidelización del electorado y la estructura territorial definirán si "
        "el sistema evoluciona hacia mayorías, bipartidismo o un escenario de "
        "tercios. Cualquier escenario debe verificarse con nuevas elecciones y "
        "con la homologación de alianzas vigente."
    )

    return (
        f"### Dictamen politológico · {fuerza}\n\n"
        f"## Datos comprobados\n{datos}\n\n"
        f"## Indicadores calculados\n{indicadores}\n\n"
        f"## Interpretación politológica\n{interpretacion}\n\n"
        f"## Prospectiva 2027 (escenario)\n{prospectiva}\n\n"
        "## Nota metodológica\n"
        "Pedersen mide desplazamiento neto agregado de votos, no "
        "transferencias individuales demostradas. Las fuerzas nuevas o "
        "extintas se computan con 0,0% para mitigar el sesgo de "
        "supervivencia; la homologación de nombres y alianzas es un insumo "
        "metodológico revisable."
    )


def _estructura_valida(informe: str) -> bool:
    """Comprueba que el proveedor respete la separación dato/indicador/lectura."""
    normalizado = clave_fuerza(informe)
    return all(
        fragmento in normalizado
        for fragmento in (
            "datos comprobados",
            "indicadores calculados",
            "interpretacion",
        )
    )


def estado_motores() -> dict[str, Any]:
    return {
        "groq": bool(config("GROQ_API_KEY")),
        "gemini": bool(
            config("GEMINI_API_KEY") or config("GOOGLE_API_KEY")
        ),
        "ollama": ollama_habilitado(),
        "modelo_ollama": config("OLLAMA_MODEL", "gemma3:1b"),
    }


def generar_dictamen_hibrido(
    contexto: dict[str, Any],
    modelo_ollama: str = "gemma3:1b",
    usar_ollama: bool | None = None,
) -> dict[str, Any]:
    """Groq → Gemini → Ollama local → reglas, con trazabilidad del motor."""
    inicio = time.perf_counter()
    prompt = construir_prompt(contexto)
    intentos: list[str] = []
    if usar_ollama is None:
        usar_ollama = ollama_habilitado()

    if config("GROQ_API_KEY"):
        contenido, detalle = consultar_groq(prompt)
        if contenido and _estructura_valida(contenido):
            return {
                "texto": contenido,
                "motor": detalle,
                "segundos": time.perf_counter() - inicio,
                "intentos": intentos,
                "prompt": prompt,
            }
        intentos.append(
            f"{detalle}: respuesta sin las secciones obligatorias"
            if contenido
            else detalle
        )

    if config("GEMINI_API_KEY") or config("GOOGLE_API_KEY"):
        contenido, detalle = consultar_gemini(prompt)
        if contenido and _estructura_valida(contenido):
            return {
                "texto": contenido,
                "motor": detalle,
                "segundos": time.perf_counter() - inicio,
                "intentos": intentos,
                "prompt": prompt,
            }
        intentos.append(
            f"{detalle}: respuesta sin las secciones obligatorias"
            if contenido
            else detalle
        )

    if usar_ollama:
        contenido, detalle = consultar_ollama(prompt, modelo_ollama)
        if contenido and _estructura_valida(contenido):
            return {
                "texto": contenido,
                "motor": detalle,
                "segundos": time.perf_counter() - inicio,
                "intentos": intentos,
                "prompt": prompt,
            }
        intentos.append(
            f"{detalle}: respuesta sin las secciones obligatorias"
            if contenido
            else detalle
        )
    else:
        intentos.append("Ollama: deshabilitado en este entorno")

    return {
        "texto": generar_dictamen_reglas(contexto),
        "motor": "Motor analítico de reglas",
        "segundos": time.perf_counter() - inicio,
        "intentos": intentos,
        "prompt": prompt,
    }
