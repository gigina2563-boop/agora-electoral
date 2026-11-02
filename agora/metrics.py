"""Indicadores electorales: comparación, Pedersen, supervivencia y NEP.

Todas las funciones trabajan con el esquema genérico ``pct_base``/``pct_actual``
para no depender de años fijos. Desde la mejora de homologación, la comparación
distingue tres estados por fuerza y período:

- ``presente``: hay datos;
- ``ausencia_verificada``: la fuerza no se presentó y la cobertura de la
  elección permite afirmarlo;
- ``sin_dato``: falta información y no se completa con cero.

El indicador de Pedersen solo incorpora un cero cuando la ausencia está
verificada. Si hay filas sin dato, el resultado es parcial y queda advertido.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

import pandas as pd


ESTADOS_DATO = ["presente", "ausencia_verificada", "sin_dato"]


def _texto(valor: Any) -> str:
    if valor is None or pd.isna(valor):
        return ""
    return str(valor).strip()


def _clave_territorio(valor: Any) -> str:
    return (
        _texto(valor)
        .casefold()
        .replace("_", " ")
        .replace("-", " ")
        .strip()
    )


def _clave_circuito(valor: Any) -> str:
    return _texto(valor).casefold().strip()


def _cobertura(
    resultados: pd.DataFrame, anio: int, cargo: str, distrito: Optional[str]
) -> set[tuple[str, str]]:
    seleccion = resultados[
        resultados["anio"].eq(int(anio)) & resultados["cargo"].eq(cargo)
    ]
    if distrito and distrito != "Todos":
        seleccion = seleccion[
            seleccion["distrito"].map(_clave_territorio)
            == _clave_territorio(distrito)
        ]
    return {
        (_clave_territorio(fila["distrito"]), _clave_circuito(fila["circuito"]))
        for _, fila in seleccion.iterrows()
    }


def _normalizar_explicitas(
    ausencias_verificadas: Optional[Iterable[Any]],
) -> tuple[set[tuple[int, str]], set[tuple[int, str, str, str]]]:
    por_anio: set[tuple[int, str]] = set()
    por_contexto: set[tuple[int, str, str, str]] = set()
    for elemento in ausencias_verificadas or []:
        if not isinstance(elemento, (tuple, list)):
            continue
        valores = tuple(_texto(valor) for valor in elemento)
        if len(valores) == 2:
            try:
                por_anio.add((int(valores[0]), valores[1]))
            except (TypeError, ValueError):
                continue
        elif len(valores) == 4:
            try:
                por_contexto.add(
                    (
                        int(valores[0]),
                        valores[1],
                        _clave_territorio(valores[2]),
                        valores[3],
                    )
                )
            except (TypeError, ValueError):
                continue
    return por_anio, por_contexto


def verificar_ausencias(
    resultados: pd.DataFrame,
    anio_base: int,
    anio_actual: int,
    cargo: str,
    distrito: Optional[str] = None,
    ausencias_verificadas: Optional[Iterable[Any]] = None,
) -> tuple[set[str], set[str], dict[str, Any]]:
    """Devuelve las fuerzas ausentes verificadas en cada período.

    Una ausencia se considera verificada si:

    - existe un registro explícito en ``ausencias_verificadas``; o
    - la fuerza no aparece en ningún circuito del período y la cobertura de
      circuitos/distritos de ambos períodos es la misma. Es una verificación
      a nivel de elección, nunca un relleno circuito por circuito.
    """
    if resultados.empty:
        return set(), set(), {
            "cobertura_base": set(),
            "cobertura_actual": set(),
            "cobertura_comparable": False,
        }
    base = resultados[
        resultados["anio"].eq(int(anio_base)) & resultados["cargo"].eq(cargo)
    ]
    actual = resultados[
        resultados["anio"].eq(int(anio_actual)) & resultados["cargo"].eq(cargo)
    ]
    if distrito and distrito != "Todos":
        clave = _clave_territorio(distrito)
        base = base[base["distrito"].map(_clave_territorio) == clave]
        actual = actual[actual["distrito"].map(_clave_territorio) == clave]
    cobertura_base = _cobertura(resultados, anio_base, cargo, distrito)
    cobertura_actual = _cobertura(resultados, anio_actual, cargo, distrito)
    comparable = bool(cobertura_base) and cobertura_base == cobertura_actual

    fuerzas_base = set(base["fuerza"].map(_texto)) - {""}
    fuerzas_actual = set(actual["fuerza"].map(_texto)) - {""}
    explicitas_anio, explicitas_contexto = _normalizar_explicitas(
        ausencias_verificadas
    )
    contexto_base = (
        int(anio_base),
        _clave_territorio(cargo),
        _clave_territorio(distrito or "Todos"),
    )
    contexto_actual = (
        int(anio_actual),
        _clave_territorio(cargo),
        _clave_territorio(distrito or "Todos"),
    )

    verificadas_base: set[str] = set()
    verificadas_actual: set[str] = set()
    for fuerza in fuerzas_actual - fuerzas_base:
        explicita = (
            (int(anio_base), _texto(fuerza)) in explicitas_anio
            or (contexto_base[0], contexto_base[1], contexto_base[2], _texto(fuerza))
            in explicitas_contexto
        )
        if explicita or comparable:
            verificadas_base.add(_texto(fuerza))
    for fuerza in fuerzas_base - fuerzas_actual:
        explicita = (
            (int(anio_actual), _texto(fuerza)) in explicitas_anio
            or (
                contexto_actual[0],
                contexto_actual[1],
                contexto_actual[2],
                _texto(fuerza),
            )
            in explicitas_contexto
        )
        if explicita or comparable:
            verificadas_actual.add(_texto(fuerza))
    return verificadas_base, verificadas_actual, {
        "cobertura_base": cobertura_base,
        "cobertura_actual": cobertura_actual,
        "cobertura_comparable": comparable,
        "fuerzas_base": len(fuerzas_base),
        "fuerzas_actual": len(fuerzas_actual),
    }


def serie_agregada(
    tabla: pd.DataFrame, columna_pct: str, columna_votos: str
) -> pd.Series:
    """Calcula la participación agregada por fuerza.

    Pondera por votos positivos cuando están disponibles; si faltan, usa el
    promedio simple de los porcentajes de los circuitos.
    """
    if tabla.empty or "fuerza" not in tabla.columns:
        return pd.Series(dtype=float)
    columnas = ["fuerza", columna_pct]
    if columna_votos in tabla.columns:
        columnas.append(columna_votos)
    datos = tabla[columnas].copy()
    datos["fuerza"] = datos["fuerza"].fillna("").astype(str).str.strip()
    datos[columna_pct] = pd.to_numeric(
        datos[columna_pct], errors="coerce"
    )
    if columna_votos in datos.columns:
        datos[columna_votos] = pd.to_numeric(
            datos[columna_votos], errors="coerce"
        )
    datos = datos[
        datos["fuerza"].ne("") & datos[columna_pct].notna()
    ]
    if datos.empty:
        return pd.Series(dtype=float)

    if columna_votos in datos.columns and datos[columna_votos].notna().all():
        votos = datos.groupby("fuerza")[columna_votos].sum()
        if votos.sum() > 0:
            return (votos / votos.sum() * 100).round(6)

    return datos.groupby("fuerza")[columna_pct].mean().round(6)


def _columnas_vacias_comparacion() -> list[str]:
    return [
        "circuito",
        "fuerza",
        "pct_base",
        "pct_actual",
        "votos_base",
        "votos_actual",
        "diferencia",
        "estado_base",
        "estado_actual",
    ]


def _agregar_estado(serie: pd.Series) -> str:
    valores = set(serie.map(_texto))
    if "presente" in valores:
        return "presente"
    if "ausencia_verificada" in valores:
        return "ausencia_verificada"
    return "sin_dato"


def construir_comparacion(
    resultados: pd.DataFrame,
    anio_base: int,
    anio_actual: int,
    cargo: str,
    distrito: str | None = None,
    ausencias_son_cero: bool = True,
    ausencias_verificadas: Optional[Iterable[Any]] = None,
) -> pd.DataFrame:
    """Pivota dos elecciones y conserva votos y estados de dato.

    ``ausencias_son_cero`` solo completa con 0 las ausencias verificadas. Las
    fuerzas con ``sin_dato`` permanecen en NaN y se informan como parciales.
    """
    if resultados.empty:
        return pd.DataFrame(columns=_columnas_vacias_comparacion())

    seleccion = resultados[
        resultados["cargo"].eq(cargo)
        & resultados["anio"].isin([int(anio_base), int(anio_actual)])
    ].copy()
    if distrito and distrito != "Todos":
        clave = _clave_territorio(distrito)
        seleccion = seleccion[
            seleccion["distrito"].map(_clave_territorio) == clave
        ]
    if seleccion.empty:
        return pd.DataFrame(columns=_columnas_vacias_comparacion())

    indices = ["distrito", "circuito", "fuerza"]
    porcentajes = seleccion.pivot_table(
        index=indices,
        columns="anio",
        values="porcentaje",
        aggfunc="mean",
    ).reindex(columns=[int(anio_base), int(anio_actual)])
    votos = seleccion.pivot_table(
        index=indices,
        columns="anio",
        values="votos_positivos",
        aggfunc=lambda serie: serie.sum(min_count=1),
    ).reindex(columns=[int(anio_base), int(anio_actual)])

    porcentajes = porcentajes.rename(
        columns={
            int(anio_base): "pct_base",
            int(anio_actual): "pct_actual",
        }
    )
    votos = votos.rename(
        columns={
            int(anio_base): "votos_base",
            int(anio_actual): "votos_actual",
        }
    )
    combinado = porcentajes.join(votos, how="outer").reset_index()

    verificadas_base, verificadas_actual, _ = verificar_ausencias(
        resultados,
        anio_base,
        anio_actual,
        cargo,
        distrito,
        ausencias_verificadas,
    )
    combinado["estado_base"] = [
        "presente"
        if pd.notna(pct)
        else (
            "ausencia_verificada"
            if _texto(fuerza) in verificadas_base
            else "sin_dato"
        )
        for pct, fuerza in zip(combinado["pct_base"], combinado["fuerza"])
    ]
    combinado["estado_actual"] = [
        "presente"
        if pd.notna(pct)
        else (
            "ausencia_verificada"
            if _texto(fuerza) in verificadas_actual
            else "sin_dato"
        )
        for pct, fuerza in zip(combinado["pct_actual"], combinado["fuerza"])
    ]

    if ausencias_son_cero:
        rellenar_base = combinado["estado_base"].eq("ausencia_verificada")
        rellenar_actual = combinado["estado_actual"].eq("ausencia_verificada")
        combinado.loc[rellenar_base, "pct_base"] = 0.0
        combinado.loc[rellenar_actual, "pct_actual"] = 0.0
        combinado.loc[rellenar_base, "votos_base"] = combinado.loc[
            rellenar_base, "votos_base"
        ].fillna(0.0)
        combinado.loc[rellenar_actual, "votos_actual"] = combinado.loc[
            rellenar_actual, "votos_actual"
        ].fillna(0.0)

    combinado = combinado[
        ~(
            combinado["pct_base"].isna()
            & combinado["pct_actual"].isna()
            & combinado["estado_base"].eq("sin_dato")
            & combinado["estado_actual"].eq("sin_dato")
        )
    ].reset_index(drop=True)
    if combinado.empty:
        return pd.DataFrame(columns=_columnas_vacias_comparacion())

    distrito_txt = combinado["distrito"].fillna("").astype(str).str.strip()
    circuito_txt = combinado["circuito"].fillna("").astype(str).str.strip()
    combinado["circuito"] = [
        (
            f"{dep} / {circ}"
            if dep and circ and circ.lower() != "general"
            else (circ or dep or "General")
        )
        for dep, circ in zip(distrito_txt, circuito_txt)
    ]
    combinado["diferencia"] = (
        combinado["pct_actual"] - combinado["pct_base"]
    ).round(4)
    return combinado[_columnas_vacias_comparacion()].reset_index(drop=True)


def agregar_por_fuerza(tabla: pd.DataFrame) -> pd.DataFrame:
    """Resume la comparación a una fila por fuerza con participación ponderada."""
    columnas_salida = [
        "fuerza",
        "pct_base",
        "pct_actual",
        "votos_base",
        "votos_actual",
        "diferencia",
        "estado_base",
        "estado_actual",
    ]
    if tabla is None or tabla.empty:
        return pd.DataFrame(columns=columnas_salida)

    base = serie_agregada(tabla, "pct_base", "votos_base")
    actual = serie_agregada(tabla, "pct_actual", "votos_actual")
    fuerzas = sorted(set(base.index) | set(actual.index))
    if not fuerzas:
        return pd.DataFrame(columns=columnas_salida)

    resumen = pd.DataFrame({"fuerza": fuerzas})
    resumen["pct_base"] = resumen["fuerza"].map(base)
    resumen["pct_actual"] = resumen["fuerza"].map(actual)
    resumen["diferencia"] = (
        resumen["pct_actual"] - resumen["pct_base"]
    ).round(4)

    for columna in ("votos_base", "votos_actual"):
        if columna in tabla.columns:
            resumen[columna] = resumen["fuerza"].map(
                pd.to_numeric(tabla[columna], errors="coerce")
                .groupby(tabla["fuerza"])
                .sum(min_count=1)
            )
        else:
            resumen[columna] = pd.NA

    for columna in ("estado_base", "estado_actual"):
        if columna in tabla.columns:
            estados = (
                tabla.groupby("fuerza")[columna]
                .agg(_agregar_estado)
                .to_dict()
            )
            resumen[columna] = resumen["fuerza"].map(estados).fillna("sin_dato")
        else:
            resumen[columna] = [
                "presente" if pd.notna(valor) else "sin_dato"
                for valor in resumen[
                    "pct_base" if columna == "estado_base" else "pct_actual"
                ]
            ]

    return resumen.sort_values(
        ["pct_actual", "pct_base"],
        ascending=False,
        na_position="last",
    ).reset_index(drop=True)[columnas_salida]


def calcular_indice_pedersen(
    tabla_fuerzas: pd.DataFrame, mitigar_sesgo: bool = True
) -> tuple[float, pd.DataFrame]:
    """Calcula Pedersen: ``suma(|Δ participación|) / 2``.

    ``mitigar_sesgo`` incorpora el 0,0% únicamente cuando la ausencia está
    verificada. Las ausencias sin dato no se rellenan: el índice queda parcial.
    Si la tabla no trae estados (uso directo de la función), se mantiene la
    compatibilidad previa y se rellenan los faltantes con cero.
    """
    columnas = {"fuerza", "pct_base", "pct_actual"}
    if (
        tabla_fuerzas is None
        or tabla_fuerzas.empty
        or not columnas.issubset(tabla_fuerzas.columns)
    ):
        return 0.0, pd.DataFrame()

    columnas_presentes = ["fuerza", "pct_base", "pct_actual"]
    for estado in ("estado_base", "estado_actual"):
        if estado in tabla_fuerzas.columns:
            columnas_presentes.append(estado)
    datos = tabla_fuerzas[columnas_presentes].copy()
    datos["fuerza"] = datos["fuerza"].fillna("").astype(str).str.strip()
    datos["pct_base"] = pd.to_numeric(datos["pct_base"], errors="coerce")
    datos["pct_actual"] = pd.to_numeric(datos["pct_actual"], errors="coerce")
    datos = datos[datos["fuerza"].ne("")]

    agregados: dict[str, str] = {}
    for estado in ("estado_base", "estado_actual"):
        if estado in datos.columns:
            agregados[estado] = _agregar_estado
    if agregados:
        estados = datos.groupby("fuerza", as_index=False).agg(agregados)
        porcentajes = datos.groupby("fuerza", as_index=False)[
            ["pct_base", "pct_actual"]
        ].mean()
        datos = porcentajes.merge(estados, on="fuerza", how="left")
    else:
        datos = datos.groupby("fuerza", as_index=False)[
            ["pct_base", "pct_actual"]
        ].mean()

    tiene_estados = {"estado_base", "estado_actual"}.issubset(datos.columns)
    faltante_base = datos["pct_base"].isna()
    faltante_actual = datos["pct_actual"].isna()
    rellenar_base = faltante_base.copy()
    rellenar_actual = faltante_actual.copy()
    if tiene_estados:
        rellenar_base &= datos["estado_base"].eq("ausencia_verificada")
        rellenar_actual &= datos["estado_actual"].eq("ausencia_verificada")

    if mitigar_sesgo:
        datos.loc[rellenar_base, "pct_base"] = 0.0
        datos.loc[rellenar_actual, "pct_actual"] = 0.0

    incompleto = datos["pct_base"].isna() | datos["pct_actual"].isna()
    datos["diferencia_abs"] = (
        datos["pct_actual"] - datos["pct_base"]
    ).abs()
    datos["aporte_pedersen"] = datos["diferencia_abs"] / 2.0
    datos["incompleto"] = incompleto

    base_positiva = datos["pct_base"].fillna(0.0).gt(0)
    actual_positiva = datos["pct_actual"].fillna(0.0).gt(0)
    datos["estado"] = "Sin votos"
    datos.loc[incompleto, "estado"] = "Datos incompletos"
    datos.loc[
        ~incompleto & base_positiva & actual_positiva, "estado"
    ] = "Sobreviviente"
    datos.loc[
        ~incompleto & base_positiva & ~actual_positiva, "estado"
    ] = "Extinta"
    datos.loc[
        ~incompleto & ~base_positiva & actual_positiva, "estado"
    ] = "Nueva"

    total = round(float(datos["diferencia_abs"].sum(skipna=True) / 2.0), 2)
    datos.attrs["parcial"] = bool(incompleto.any())
    datos.attrs["filas_incompletas"] = int(incompleto.sum())
    return total, datos.sort_values(
        "diferencia_abs", ascending=False, na_position="last"
    ).reset_index(drop=True)


def analizar_supervivencia(
    tabla_fuerzas: pd.DataFrame,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Resume continuidad partidaria y su componente de Pedersen.

    ``No sobrevive`` y ``Nueva`` solo se informan cuando la ausencia está
    verificada. Si falta un dato, la fuerza se clasifica como
    ``Datos incompletos``.
    """
    columnas = {"fuerza", "pct_base", "pct_actual"}
    if (
        tabla_fuerzas is None
        or tabla_fuerzas.empty
        or not columnas.issubset(tabla_fuerzas.columns)
    ):
        return {}, pd.DataFrame()

    columnas_presentes = ["fuerza", "pct_base", "pct_actual"]
    for estado in ("estado_base", "estado_actual"):
        if estado in tabla_fuerzas.columns:
            columnas_presentes.append(estado)
    datos = tabla_fuerzas[columnas_presentes].copy()
    datos["fuerza"] = datos["fuerza"].fillna("").astype(str).str.strip()
    datos["pct_base"] = pd.to_numeric(datos["pct_base"], errors="coerce")
    datos["pct_actual"] = pd.to_numeric(datos["pct_actual"], errors="coerce")

    agregados: dict[str, Any] = {
        "pct_base": ("pct_base", "mean"),
        "pct_actual": ("pct_actual", "mean"),
        "datos_base": ("pct_base", "count"),
        "datos_actual": ("pct_actual", "count"),
    }
    for estado in ("estado_base", "estado_actual"):
        if estado in datos.columns:
            agregados[estado] = (estado, _agregar_estado)
    por_fuerza = datos.groupby("fuerza", as_index=False).agg(**agregados)

    if {"estado_base", "estado_actual"}.issubset(por_fuerza.columns):
        completo = ~por_fuerza["estado_base"].eq("sin_dato") & ~por_fuerza[
            "estado_actual"
        ].eq("sin_dato")
    else:
        completo = por_fuerza["datos_base"].gt(0) & por_fuerza[
            "datos_actual"
        ].gt(0)

    tiene_base = por_fuerza["pct_base"].fillna(0).gt(0)
    tiene_actual = por_fuerza["pct_actual"].fillna(0).gt(0)

    por_fuerza["estado"] = "Sin votos"
    por_fuerza.loc[~completo, "estado"] = "Datos incompletos"
    por_fuerza.loc[
        completo & tiene_base & tiene_actual, "estado"
    ] = "Sobreviviente"
    por_fuerza.loc[
        completo & tiene_base & ~tiene_actual, "estado"
    ] = "No sobrevive"
    por_fuerza.loc[
        completo & ~tiene_base & tiene_actual, "estado"
    ] = "Nueva"

    comparable = por_fuerza[completo].copy()
    sobrevivientes = comparable[comparable["estado"] == "Sobreviviente"]
    fuerzas_base = comparable[comparable["pct_base"] > 0]
    fuerzas_supervivientes = len(sobrevivientes)
    fuerzas_iniciales = len(fuerzas_base)
    voto_base = fuerzas_base["pct_base"].sum()
    voto_supervivientes = sobrevivientes["pct_base"].sum()

    comparable["contribucion_pedersen"] = (
        comparable["pct_actual"] - comparable["pct_base"]
    ).abs() / 2.0
    pedersen_total = float(comparable["contribucion_pedersen"].sum())
    pedersen_supervivientes = float(
        comparable.loc[
            comparable["estado"] == "Sobreviviente",
            "contribucion_pedersen",
        ].sum()
    )
    pedersen_nuevas_y_extintas = float(
        comparable.loc[
            comparable["estado"].isin(["Nueva", "No sobrevive"]),
            "contribucion_pedersen",
        ].sum()
    )

    por_fuerza["variacion"] = (
        por_fuerza["pct_actual"] - por_fuerza["pct_base"]
    )
    for estado in ("estado_base", "estado_actual"):
        if estado not in por_fuerza.columns:
            por_fuerza[estado] = "presente"
    detalle = por_fuerza[
        [
            "fuerza",
            "estado",
            "pct_base",
            "pct_actual",
            "variacion",
            "estado_base",
            "estado_actual",
        ]
    ].sort_values(["estado", "fuerza"])

    resumen: dict[str, Any] = {
        "fuerzas_iniciales": fuerzas_iniciales,
        "fuerzas_supervivientes": fuerzas_supervivientes,
        "fuerzas_extintas": int(
            (comparable["estado"] == "No sobrevive").sum()
        ),
        "fuerzas_nuevas": int((comparable["estado"] == "Nueva").sum()),
        "filas_incompletas": int((~completo).sum()),
        "tasa_supervivencia": (
            fuerzas_supervivientes / fuerzas_iniciales * 100
            if fuerzas_iniciales
            else 0.0
        ),
        "peso_base_supervivientes": (
            voto_supervivientes / voto_base * 100 if voto_base else 0.0
        ),
        "pedersen_total": round(pedersen_total, 2),
        "pedersen_supervivientes": round(pedersen_supervivientes, 2),
        "pedersen_nuevas_y_extintas": round(
            pedersen_nuevas_y_extintas, 2
        ),
        "pedersen_parcial": bool((~completo).any()),
    }
    return resumen, detalle.reset_index(drop=True)


def validar_comparacion(
    resultados: pd.DataFrame,
    anio_base: int,
    anio_actual: int,
    cargo: str,
    distrito: Optional[str] = None,
) -> dict[str, Any]:
    """Comprueba que dos elecciones sean comparables en cargo y territorio."""
    advertencias: list[str] = []
    if resultados is None or resultados.empty:
        return {
            "comparable": False,
            "advertencias": ["No hay resultados cargados."],
            "cobertura_base": set(),
            "cobertura_actual": set(),
        }
    base = resultados[
        resultados["anio"].eq(int(anio_base)) & resultados["cargo"].eq(cargo)
    ]
    actual = resultados[
        resultados["anio"].eq(int(anio_actual)) & resultados["cargo"].eq(cargo)
    ]
    if distrito and distrito != "Todos":
        clave = _clave_territorio(distrito)
        base = base[base["distrito"].map(_clave_territorio) == clave]
        actual = actual[actual["distrito"].map(_clave_territorio) == clave]
    if base.empty:
        advertencias.append(
            f"No hay datos de {cargo} en {anio_base} para el territorio elegido."
        )
    if actual.empty:
        advertencias.append(
            f"No hay datos de {cargo} en {anio_actual} para el territorio elegido."
        )
    tipos_base: set[str] = set()
    tipos_actual: set[str] = set()
    misma_instancia = True
    if "tipo_eleccion" in resultados.columns:
        tipos_base = set(base["tipo_eleccion"].map(_texto)) - {""}
        tipos_actual = set(actual["tipo_eleccion"].map(_texto)) - {""}
        misma_instancia = not (
            tipos_base and tipos_actual and tipos_base != tipos_actual
        )
        if not misma_instancia:
            advertencias.append(
                "Los períodos comparados no son de la misma instancia electoral "
                f"({', '.join(sorted(tipos_base))} vs. "
                f"{', '.join(sorted(tipos_actual))})."
            )
    niveles_base: set[str] = set()
    niveles_actual: set[str] = set()
    mismo_nivel = True
    if "nivel_analisis" in resultados.columns:
        niveles_base = set(base["nivel_analisis"].map(_texto)) - {""}
        niveles_actual = set(actual["nivel_analisis"].map(_texto)) - {""}
        mismo_nivel = not (
            niveles_base and niveles_actual and niveles_base != niveles_actual
        )
        if not mismo_nivel:
            advertencias.append(
                "Los períodos comparados mezclan niveles de análisis "
                f"({', '.join(sorted(niveles_base))} vs. "
                f"{', '.join(sorted(niveles_actual))}); conviene usar el mismo "
                "escenario de homologación."
            )
    cobertura_base = _cobertura(resultados, anio_base, cargo, distrito)
    cobertura_actual = _cobertura(resultados, anio_actual, cargo, distrito)
    if cobertura_base != cobertura_actual:
        advertencias.append(
            "La cobertura territorial no es idéntica entre los dos períodos; "
            "las ausencias no verificadas no se completan con cero."
        )
    if not advertencias:
        advertencias.append(
            "Comparación válida: mismo cargo, misma instancia y cobertura "
            "territorial consistente."
        )
    return {
        "comparable": bool(
            not base.empty
            and not actual.empty
            and cobertura_base == cobertura_actual
            and misma_instancia
            and mismo_nivel
        ),
        "misma_instancia": misma_instancia,
        "mismo_nivel": mismo_nivel,
        "advertencias": advertencias,
        "cobertura_base": cobertura_base,
        "cobertura_actual": cobertura_actual,
        "tipos_base": sorted(
            set(base["tipo_eleccion"].map(_texto)) - {""}
        )
        if "tipo_eleccion" in base.columns
        else [],
        "tipos_actual": sorted(
            set(actual["tipo_eleccion"].map(_texto)) - {""}
        )
        if "tipo_eleccion" in actual.columns
        else [],
    }


def calcular_nep(participaciones: pd.Series) -> float:
    """Número efectivo de partidos (Laakso-Taagepera)."""
    valores = pd.to_numeric(participaciones, errors="coerce").dropna()
    valores = valores[valores >= 0]
    if valores.empty or valores.sum() <= 0:
        return 0.0
    proporciones = valores / valores.sum()
    return float(1.0 / proporciones.pow(2).sum())
