"""
Módulo de análisis de datos y cálculo de métricas politológicas.
"""

import pandas as pd


def calcular_indice_pedersen(df_datos):
    """
    Calcula la Volatilidad Electoral Total (Índice de Pedersen).
    Controla el sesgo de supervivencia asignando 0% a fuerzas ausentes o extintas.
    """
    if df_datos.empty or not {"fuerza", "pct_2023", "pct_2025"}.issubset(
        df_datos.columns
    ):
        return 0.0, pd.DataFrame()

    df_agrupado = (
        df_datos.groupby("fuerza")[["pct_2023", "pct_2025"]]
        .mean()
        .reset_index()
    )
    df_agrupado["pct_2023"] = df_agrupado["pct_2023"].fillna(0.0)
    df_agrupado["pct_2025"] = df_agrupado["pct_2025"].fillna(0.0)
    df_agrupado["diferencia_abs"] = (
        df_agrupado["pct_2025"] - df_agrupado["pct_2023"]
    ).abs()

    volatilidad_total = round(df_agrupado["diferencia_abs"].sum() / 2.0, 2)
    return volatilidad_total, df_agrupado


def analizar_supervivencia(df_datos):
    """Resume continuidad partidaria y su componente de volatilidad.

    Los porcentajes iguales a cero deben representar una ausencia confirmada.
    Los valores vacíos se consideran desconocidos y no se imputan como cero.
    """
    columnas = {"fuerza", "pct_2023", "pct_2025"}
    if df_datos.empty or not columnas.issubset(df_datos.columns):
        return {}, pd.DataFrame()

    datos = df_datos[["fuerza", "pct_2023", "pct_2025"]].copy()
    datos["pct_2023"] = pd.to_numeric(datos["pct_2023"], errors="coerce")
    datos["pct_2025"] = pd.to_numeric(datos["pct_2025"], errors="coerce")
    por_fuerza = datos.groupby("fuerza", as_index=False).agg(
        pct_2023=("pct_2023", "mean"),
        pct_2025=("pct_2025", "mean"),
        datos_2023=("pct_2023", "count"),
        datos_2025=("pct_2025", "count"),
    )

    completo = por_fuerza["datos_2023"].gt(0) & por_fuerza["datos_2025"].gt(0)
    tiene_2023 = por_fuerza["pct_2023"].gt(0)
    tiene_2025 = por_fuerza["pct_2025"].gt(0)
    por_fuerza["estado"] = "Ausente en ambos períodos"
    por_fuerza.loc[~completo, "estado"] = "Datos incompletos"
    por_fuerza.loc[completo & tiene_2023 & tiene_2025, "estado"] = "Sobreviviente"
    por_fuerza.loc[completo & tiene_2023 & ~tiene_2025, "estado"] = "No sobrevive"
    por_fuerza.loc[completo & ~tiene_2023 & tiene_2025, "estado"] = "Nueva"

    comparable = por_fuerza[completo].copy()
    sobrevivientes = comparable[comparable["estado"] == "Sobreviviente"]
    fuerzas_base = comparable[comparable["pct_2023"] > 0]
    fuerzas_supervivientes = len(sobrevivientes)
    fuerzas_iniciales = len(fuerzas_base)
    voto_base = fuerzas_base["pct_2023"].sum()
    voto_supervivientes = sobrevivientes["pct_2023"].sum()

    comparable["contribucion_pedersen"] = (
        comparable["pct_2025"] - comparable["pct_2023"]
    ).abs() / 2
    pedersen_total = comparable["contribucion_pedersen"].sum()
    pedersen_supervivientes = comparable.loc[
        comparable["estado"] == "Sobreviviente", "contribucion_pedersen"
    ].sum()
    pedersen_nuevas_y_extintas = comparable.loc[
        comparable["estado"].isin(["Nueva", "No sobrevive"]),
        "contribucion_pedersen",
    ].sum()

    por_fuerza["variacion"] = por_fuerza["pct_2025"] - por_fuerza["pct_2023"]
    detalle = por_fuerza[
        ["fuerza", "estado", "pct_2023", "pct_2025", "variacion"]
    ].sort_values(["estado", "fuerza"])
    resumen = {
        "fuerzas_iniciales": fuerzas_iniciales,
        "fuerzas_supervivientes": fuerzas_supervivientes,
        "fuerzas_extintas": int((comparable["estado"] == "No sobrevive").sum()),
        "fuerzas_nuevas": int((comparable["estado"] == "Nueva").sum()),
        "filas_incompletas": int((~completo).sum()),
        "tasa_supervivencia": (
            fuerzas_supervivientes / fuerzas_iniciales * 100
            if fuerzas_iniciales
            else 0.0
        ),
        "peso_2023_supervivientes": (
            voto_supervivientes / voto_base * 100 if voto_base else 0.0
        ),
        "pedersen_total": pedersen_total,
        "pedersen_supervivientes": pedersen_supervivientes,
        "pedersen_nuevas_y_extintas": pedersen_nuevas_y_extintas,
    }
    return resumen, detalle