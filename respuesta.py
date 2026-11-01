"""
Módulo de generación de dictámenes y respuestas para el Observatorio Electoral.
"""

def generar_dictamen_politologico(fuerza, pedersen, df_fuerza):
    var_promedio = df_fuerza["diferencia"].mean() if not df_fuerza.empty else 0.0
    pct_actual = df_fuerza["pct_2025"].mean() if not df_fuerza.empty else 0.0
    circuito_destacado = df_fuerza.sort_values(by="diferencia", ascending=False).iloc[0]["circuito"] if not df_fuerza.empty else "General"

    if var_promedio > 5.0:
        tendencia = f"un marcado crecimiento y consolidación territorial (pico en {circuito_destacado})"
    elif var_promedio >= 0.0:
        tendencia = f"una retención estable de sus bases en {circuito_destacado}"
    else:
        tendencia = "una contracción neta en la adhesión en los circuitos relevados"

    if pedersen > 30.0:
        dinamica = "alta volatilidad y reconfiguración profunda del sistema de partidos"
    elif pedersen > 15.0:
        dinamica = "fluctuación moderada con transferencias intermedias de votos"
    else:
        dinamica = "estabilidad electoral y baja dispersión de preferencias"

    return f"""
* **Desempeño de {fuerza}:** Promedio de **{pct_actual:.2f}%** con una variación de **{var_promedio:+.2f}%**, reflejando {tendencia}.
* **Dinámica Electoral (Pedersen = {pedersen}%):** Evidencia una **{dinamica}**, reflejando la absorción y migración de votos desde fuerzas no supervivientes.
* **Proyección Prospectiva (2027):** La captación de sellos extintos y la fidelización del electorado en la Capital definirán la conformación de mayorías o escenarios de tercios a nivel nacional.
"""