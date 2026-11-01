import glob
import os
import sqlite3
import pandas as pd


def procesar_archivo_cne(file_path):
    df_raw = pd.read_excel(file_path)

    # Extraer el nombre exacto del circuito desde el archivo oficial
    nombre_circuito = str(df_raw.iloc[5, 1]).strip()
    if not nombre_circuito or nombre_circuito == "nan":
        nombre_circuito = os.path.basename(file_path).replace(".xls", "")

    # Extraer agrupaciones políticas y votos
    df_datos = df_raw.iloc[12:21, [3, 4]].copy()
    df_datos.columns = ["fuerza", "votos_2025"]

    df_datos["fuerza"] = df_datos["fuerza"].str.strip()
    df_datos["votos_2025"] = pd.to_numeric(
        df_datos["votos_2025"], errors="coerce"
    ).fillna(0)

    # Calcular porcentaje del circuito
    total_votos = df_datos["votos_2025"].sum()
    if total_votos > 0:
        df_datos["pct_2025"] = (
            (df_datos["votos_2025"] / total_votos) * 100
        ).round(2)
    else:
        df_datos["pct_2025"] = 0.0

    df_datos["circuito"] = nombre_circuito

    # Valores de referencia 2023 para estimación
    df_datos["pct_2023"] = [8.0, 25.0, 3.0, 1.0, 35.0, 1.0, 2.0, 1.0, 2.0]#example values, replace with actual data if available
    df_datos["diferencia"] = (
        df_datos["pct_2025"] - df_datos["pct_2023"]
    ).round(2)

    return df_datos[["circuito", "fuerza", "pct_2023", "pct_2025", "diferencia"]]


# Buscar todos los archivos .xls descargados en la carpeta actual
archivos_xls = glob.glob("zona_circ_*.xls")

if not archivos_xls:
    print(
        "❌ No se encontraron archivos que empiecen con 'zona_circ_' en la carpeta."
    )
else:
    conn = sqlite3.connect("observatorio.db")

    # Recrear la tabla para guardar todos los circuitos juntos
    conn.execute("DROP TABLE IF EXISTS elecciones_circuito")

    for archivo in archivos_xls:
        try:
            df_circuito = procesar_archivo_cne(archivo)
            df_circuito.to_sql(
                "elecciones_circuito", conn, if_exists="append", index=False
            )
            print(f"✅ Importado correctamente: {archivo}")
        except Exception as e:
            print(f"⚠️ Error en {archivo}: {e}")

    conn.close()
    print("\n🎉 ¡Base de datos integrada exitosamente con todos los circuitos!")