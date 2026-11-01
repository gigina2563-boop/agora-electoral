"""Indicadores electorales: comparación, Pedersen, supervivencia y NEP.

Todas las funciones trabajan con el esquema genérico `pct_base`/`pct_actual`
para no depender de años fijos.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def serie_agregada(
    tabla: pd.DataFrame, columna_pct: str, columna_votos: str
) -> pd.Series:
    """Calcula la participación agregada por fuerza.

    Pondera por votos positivos cuando están disponibles; si faltan, usa el
    promedio simple de los porcentajes de los circuitos.
    """
    if tabla.empty or "fuerza" not in tabla.columns:
        return pd.Series(dtype=float)

    datos = tabla[["fuerza", columna_pct, columna_votos]].copy()
    datos["fuerza"] = datos["fuerza"].fillna("").astype(str).str.strip()
    datos[columna_pct] = pd.to_numeric(
        datos[columna_pct], errors="coerce"
    )
    datos[columna_votos] = pd.to_numeric(
        datos[columna_votos], errors="coerce"
    )
    datos = datos[
        datos["fuerza"].ne("") & datos[columna_pct].notna()
    ]
    if datos.empty:
        return pd.Series(dtype=float)

    if datos[columna_votos].notna().all():
        votos = datos.groupby("fuerza")[columna_votos].sum()
        if votos.sum() > 0:
            return (votos / votos.sum() * 100).round(6)

    return datos.groupby("fuerza")[columna_pct].mean().round(6)


def construir_comparacion(
    resultados: pd.DataFrame,
    anio_base: int,
    anio_actual: int,
    cargo: str,
    distrito: str | None = None,
    ausencias_son_cero: bool = True,
) -> pd.DataFrame:
    """Pivota dos elecciones y conserva votos para poder ponderar."""
    if resultados.empty:
        return pd.DataFrame(
            columns=[
                "circuito",
                "fuerza",
                "pct_base",
                "pct_actual",
                "votos_base",
                "votos_actual",
                "diferencia",
            ]
        )

    seleccion = resultados[
        resultados["cargo"].eq(cargo)
        & resultados["anio"].isin([int(anio_base), int(anio_actual)])
    ].copy()
    if distrito and distrito != "Todos":
        seleccion = seleccion[seleccion["distrito"].eq(distrito)]
    if seleccion.empty:
        return pd.DataFrame(
            columns=[
                "circuito",
                "fuerza",
                "pct_base",
                "pct_actual",
                "votos_base",
                "votos_actual",
                "diferencia",
            ]
        )

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
    combinado = porcentajes.join(votos, how="outer")

    if ausencias_son_cero:
        faltante_base = combinado["pct_base"].isna()
        faltante_actual = combinado["pct_actual"].isna()
        combinado.loc[faltante_base, "pct_base"] = 0.0
        combinado.loc[faltante_actual, "pct_actual"] = 0.0
        combinado.loc[faltante_base, "votos_base"] = combinado.loc[
            faltante_base, "votos_base"
        ].fillna(0.0)
        combinado.loc[faltante_actual, "votos_actual"] = combinado.loc[
            faltante_actual, "votos_actual"
        ].fillna(0.0)

    combinado = combinado.dropna(
        subset=["pct_base", "pct_actual"], how="all"
    ).reset_index()
    if combinado.empty:
        return pd.DataFrame(
            columns=[
                "circuito",
                "fuerza",
                "pct_base",
                "pct_actual",
                "votos_base",
                "votos_actual",
                "diferencia",
            ]
        )

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
    return combinado[
        [
            "circuito",
            "fuerza",
            "pct_base",
            "pct_actual",
            "votos_base",
            "votos_actual",
            "diferencia",
        ]
    ].reset_index(drop=True)


def agregar_por_fuerza(tabla: pd.DataFrame) -> pd.DataFrame:
    """Resume la comparación a una fila por fuerza con participación ponderada."""
    if tabla is None or tabla.empty:
        return pd.DataFrame(
            columns=[
                "fuerza",
                "pct_base",
                "pct_actual",
                "votos_base",
                "votos_actual",
                "diferencia",
            ]
        )

    base = serie_agregada(tabla, "pct_base", "votos_base")
    actual = serie_agregada(tabla, "pct_actual", "votos_actual")
    fuerzas = sorted(set(base.index) | set(actual.index))
    if not fuerzas:
        return pd.DataFrame(
            columns=[
                "fuerza",
                "pct_base",
                "pct_actual",
                "votos_base",
                "votos_actual",
                "diferencia",
            ]
        )

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

    return resumen.sort_values(
        ["pct_actual", "pct_base"],
        ascending=False,
        na_position="last",
    ).reset_index(drop=True)


def calcular_indice_pedersen(
    tabla_fuerzas: pd.DataFrame, mitigar_sesgo: bool = True
) -> tuple[float, pd.DataFrame]:
    """Calcula Pedersen: suma(|Δ participación|) / 2.

    Si `mitigar_sesgo` es True, las fuerzas nuevas o extintas reciben 0.0% en
    el período en que no compiten. Esto evita el sesgo de supervivencia y
    contabiliza entradas y salidas del sistema de partidos.
    """
    columnas = {"fuerza", "pct_base", "pct_actual"}
    if (
        tabla_fuerzas is None
        or tabla_fuerzas.empty
        or not columnas.issubset(tabla_fuerzas.columns)
    ):
        return 0.0, pd.DataFrame()

    datos = tabla_fuerzas[
        ["fuerza", "pct_base", "pct_actual"]
    ].copy()
    datos["fuerza"] = datos["fuerza"].fillna("").astype(str).str.strip()
    datos["pct_base"] = pd.to_numeric(
        datos["pct_base"], errors="coerce"
    )
    datos["pct_actual"] = pd.to_numeric(
        datos["pct_actual"], errors="coerce"
    )
    datos = datos[datos["fuerza"].ne("")]
    datos = (
        datos.groupby("fuerza", as_index=False)[
            ["pct_base", "pct_actual"]
        ]
        .mean()
        .reset_index(drop=True)
    )

    faltantes = datos[["pct_base", "pct_actual"]].isna().any(axis=1)
    if mitigar_sesgo:
        # Una fuerza presente en un solo período se computa con 0,0% en el
        # otro: es nueva o extinta, no un dato faltante.
        incompleto = datos[["pct_base", "pct_actual"]].isna().all(axis=1)
        datos[["pct_base", "pct_actual"]] = datos[
            ["pct_base", "pct_actual"]
        ].fillna(0.0)
    else:
        incompleto = faltantes

    datos["diferencia_abs"] = (
        datos["pct_actual"] - datos["pct_base"]
    ).abs()
    datos["aporte_pedersen"] = datos["diferencia_abs"] / 2.0
    datos["incompleto"] = incompleto

    base_positiva = datos["pct_base"].fillna(0.0).gt(0)
    actual_positiva = datos["pct_actual"].fillna(0.0).gt(0)
    datos["estado"] = "Sin votos"
    datos.loc[
        incompleto, "estado"
    ] = "Datos incompletos"
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
    return total, datos.sort_values(
        "diferencia_abs", ascending=False, na_position="last"
    ).reset_index(drop=True)


def analizar_supervivencia(
    tabla_fuerzas: pd.DataFrame,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Resume continuidad partidaria y su componente de Pedersen."""
    columnas = {"fuerza", "pct_base", "pct_actual"}
    if (
        tabla_fuerzas is None
        or tabla_fuerzas.empty
        or not columnas.issubset(tabla_fuerzas.columns)
    ):
        return {}, pd.DataFrame()

    datos = tabla_fuerzas[
        ["fuerza", "pct_base", "pct_actual"]
    ].copy()
    datos["fuerza"] = datos["fuerza"].fillna("").astype(str).str.strip()
    datos["pct_base"] = pd.to_numeric(
        datos["pct_base"], errors="coerce"
    )
    datos["pct_actual"] = pd.to_numeric(
        datos["pct_actual"], errors="coerce"
    )
    por_fuerza = datos.groupby("fuerza", as_index=False).agg(
        pct_base=("pct_base", "mean"),
        pct_actual=("pct_actual", "mean"),
        datos_base=("pct_base", "count"),
        datos_actual=("pct_actual", "count"),
    )

    completo = por_fuerza["datos_base"].gt(0) & por_fuerza[
        "datos_actual"
    ].gt(0)
    tiene_base = por_fuerza["pct_base"].gt(0)
    tiene_actual = por_fuerza["pct_actual"].gt(0)

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
    sobrevivientes = comparable[
        comparable["estado"] == "Sobreviviente"
    ]
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
    detalle = por_fuerza[
        ["fuerza", "estado", "pct_base", "pct_actual", "variacion"]
    ].sort_values(["estado", "fuerza"])

    resumen: dict[str, Any] = {
        "fuerzas_iniciales": fuerzas_iniciales,
        "fuerzas_supervivientes": fuerzas_supervivientes,
        "fuerzas_extintas": int(
            (comparable["estado"] == "No sobrevive").sum()
        ),
        "fuerzas_nuevas": int(
            (comparable["estado"] == "Nueva").sum()
        ),
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
        "pedersen_supervivientes": round(
            pedersen_supervivientes, 2
        ),
        "pedersen_nuevas_y_extintas": round(
            pedersen_nuevas_y_extintas, 2
        ),
    }
    return resumen, detalle.reset_index(drop=True)


def calcular_nep(participaciones: pd.Series) -> float:
    """Número efectivo de partidos (Laakso-Taagepera)."""
    valores = pd.to_numeric(participaciones, errors="coerce").dropna()
    valores = valores[valores >= 0]
    if valores.empty or valores.sum() <= 0:
        return 0.0
    proporciones = valores / valores.sum()
    return float(1.0 / proporciones.pow(2).sum())


