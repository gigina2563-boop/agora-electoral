"""Lectura y normalización de CSV electorales para el observatorio."""

from __future__ import annotations

import re
import unicodedata
from io import StringIO

import pandas as pd

COLUMNAS_SALIDA = [
    "anio",
    "distrito",
    "circuito",
    "cargo",
    "fuerza",
    "votos_positivos",
    "porcentaje",
]
CLAVE = ["anio", "distrito", "circuito", "cargo", "fuerza"]


def _normalizar_nombre(valor: object) -> str:
    texto = unicodedata.normalize("NFKD", str(valor))
    texto = "".join(caracter for caracter in texto if not unicodedata.combining(caracter))
    texto = texto.strip().lower().replace("%", " pct ")
    return re.sub(r"[^a-z0-9]+", "_", texto).strip("_")


def _numero(columna: pd.Series, *, entero: bool = False) -> pd.Series:
    """Lee números con formato argentino (1.234,56) o internacional (1,234.56)."""
    def convertir(valor: object) -> float:
        if pd.isna(valor):
            return float("nan")
        texto = str(valor).strip().replace("%", "").replace(" ", "")
        if not texto:
            return float("nan")
        if "," in texto and "." in texto:
            if texto.rfind(",") > texto.rfind("."):
                texto = texto.replace(".", "").replace(",", ".")
            else:
                texto = texto.replace(",", "")
        elif "," in texto:
            texto = texto.replace(",", ".")
        elif entero and re.fullmatch(r"-?\d{1,3}(\.\d{3})+", texto):
            texto = texto.replace(".", "")
        return float(texto)

    return columna.map(convertir)


def _leer_csv(archivo: object) -> pd.DataFrame:
    if hasattr(archivo, "getvalue"):
        contenido = archivo.getvalue()
    elif isinstance(archivo, bytes):
        contenido = archivo
    elif hasattr(archivo, "read"):
        contenido = archivo.read()
    else:
        raise ValueError("No pude leer el archivo CSV subido.")

    if isinstance(contenido, str):
        texto = contenido
    else:
        try:
            texto = contenido.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                texto = contenido.decode("latin-1")
            except UnicodeDecodeError as error:
                raise ValueError("El CSV debe estar codificado en UTF-8 o Latin-1.") from error

    if not texto.strip():
        raise ValueError(
            "El archivo CSV está completamente vacío. Completa la plantilla "
            "con filas de resultados antes de subirla."
        )

    try:
        return pd.read_csv(StringIO(texto), sep=None, engine="python", dtype=str)
    except Exception as error:
        raise ValueError(
            "No pude interpretar el CSV. Comprueba que tenga una fila de encabezados "
            "y use coma o punto y coma como separador."
        ) from error


def normalizar_csv_electoral(archivo: object) -> pd.DataFrame:
    """Convierte CSV electoral ancho o largo a una fila por fuerza y elección.

    Formato largo recomendado: anio, distrito, circuito, cargo, fuerza,
    votos_positivos y/o porcentaje. También acepta columnas porcentaje_YYYY.
    """
    original = _leer_csv(archivo)
    if original.empty:
        raise ValueError(
            "El CSV tiene encabezados, pero ninguna fila de resultados. "
            "La plantilla descargada está vacía a propósito: agrega una fila "
            "por fuerza, distrito, circuito, cargo y año antes de subirla."
        )
    if len(original.columns) < 2:
        raise ValueError(
            "El CSV no contiene columnas suficientes. Descarga la plantilla, "
            "complétala con los resultados electorales y vuelve a subirla."
        )

    df = original.copy()
    df.columns = [_normalizar_nombre(columna) for columna in df.columns]
    if len(set(df.columns)) != len(df.columns):
        raise ValueError("Hay encabezados duplicados después de normalizar sus nombres.")

    alias = {
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
        "diferencia": "diferencia",
    }
    df = df.rename(columns=alias)

    formato_cne = "votos_tipo" in df.columns and "votos_positivos" in df.columns
    if formato_cne:
        # El CSV de resultados CNE tiene una fila por mesa y tipo de voto.
        # Para comparar fuerzas, conservar solo votos positivos y sumar mesas
        # dentro de cada circuito antes de calcular porcentajes.
        df = df[df["votos_tipo"].astype(str).str.strip().str.upper() == "POSITIVO"].copy()
        if df.empty:
            raise ValueError(
                "El CSV CNE no contiene filas con votos_tipo=POSITIVO."
            )
        if "eleccion_tipo" in df.columns:
            tipo_eleccion = df["eleccion_tipo"].fillna("").astype(str).str.strip()
            df["cargo"] = df["cargo"].astype(str).str.strip() + " — " + tipo_eleccion
        if "circuito_id" in df.columns:
            codigo = df["circuito_id"].fillna("").astype(str).str.strip()
            nombre = (
                df["circuito_nombre"].fillna("").astype(str).str.strip()
                if "circuito_nombre" in df.columns
                else pd.Series("", index=df.index)
            )
            df["circuito"] = codigo.where(codigo.ne(""), nombre)

    if "anio" not in df.columns:
        nombre_archivo = str(getattr(archivo, "name", ""))
        anios_en_nombre = sorted(set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", nombre_archivo)))
        if len(anios_en_nombre) == 1:
            df["anio"] = anios_en_nombre[0]
        elif len(anios_en_nombre) > 1:
            raise ValueError(
                f"{nombre_archivo}: el nombre contiene varios años. Agrega una "
                "columna 'anio' para identificar cada registro."
            )

    # Admite encabezados como pct_2023, porcentaje_2023 y "porcentaje 2023".
    columnas_anuales = {
        columna: re.fullmatch(r"(?:pct|porcentaje)_?(\d{4})", columna)
        for columna in df.columns
    }
    columnas_anuales = {
        columna: coincidencia.group(1)
        for columna, coincidencia in columnas_anuales.items()
        if coincidencia
    }
    if columnas_anuales and "anio" not in df.columns:
        if "fuerza" not in df.columns:
            raise ValueError("Falta la columna de fuerza/agrupación política.")
        id_vars = [
            columna
            for columna in df.columns
            if columna not in columnas_anuales
            and columna not in {"diferencia"}
        ]
        df = df.melt(
            id_vars=id_vars,
            value_vars=list(columnas_anuales),
            var_name="columna_periodo",
            value_name="porcentaje",
        )
        df["anio"] = df["columna_periodo"].map(
            {columna: anio for columna, anio in columnas_anuales.items()}
        )
        df = df.drop(columns="columna_periodo")

    if "anio" not in df.columns or "fuerza" not in df.columns:
        raise ValueError(
            "Faltan columnas obligatorias. Debe haber 'anio' y 'fuerza' "
            "(también se aceptan 'año', 'agrupación política' o 'partido'). "
            "Si el archivo contiene un solo año, también se puede indicar en "
            "el nombre, por ejemplo resultados_2023.csv."
        )
    if not ({"porcentaje", "votos_positivos"} & set(df.columns)):
        raise ValueError(
            "Falta 'porcentaje' o 'votos_positivos'. Se puede calcular el porcentaje "
            "a partir de los votos positivos de cada elección y circuito."
        )

    if "distrito" not in df.columns:
        df["distrito"] = "Departamento no especificado"
    if "circuito" not in df.columns:
        df["circuito"] = "General"
    if "cargo" not in df.columns:
        df["cargo"] = "No especificado"
    if "votos_positivos" not in df.columns:
        df["votos_positivos"] = pd.NA
    if "porcentaje" not in df.columns:
        df["porcentaje"] = pd.NA

    df["anio"] = _numero(df["anio"], entero=True)
    if df["anio"].isna().any() or ((df["anio"] % 1) != 0).any():
        raise ValueError("La columna 'anio' debe contener años enteros, por ejemplo 2019 o 2023.")
    df["anio"] = df["anio"].astype(int)
    df["votos_positivos"] = _numero(df["votos_positivos"], entero=True)
    df["porcentaje"] = _numero(df["porcentaje"])

    for columna in ("distrito", "circuito", "cargo", "fuerza"):
        df[columna] = df[columna].fillna("").astype(str).str.strip()
    vacias = df["fuerza"].eq("")
    if vacias.any():
        raise ValueError(f"Hay {int(vacias.sum())} filas sin nombre de fuerza política.")

    if (df["votos_positivos"].dropna() < 0).any():
        raise ValueError("'votos_positivos' no puede contener valores negativos.")
    porcentajes_incorrectos = df["porcentaje"].notna() & ~df["porcentaje"].between(0, 100)
    if porcentajes_incorrectos.any():
        raise ValueError("Los porcentajes deben estar entre 0 y 100 (sin incluir el signo %).")

    if formato_cne:
        df = df.groupby(CLAVE, as_index=False, dropna=False).agg(
            votos_positivos=("votos_positivos", "sum"),
            porcentaje=("porcentaje", "first"),
        )

    por_calcular = df["porcentaje"].isna()
    if por_calcular.any():
        if df.loc[por_calcular, "votos_positivos"].isna().any():
            raise ValueError(
                "Hay filas sin porcentaje y sin votos positivos; completa uno de esos datos."
            )
        grupos = ["anio", "distrito", "circuito", "cargo"]
        totales = df.groupby(grupos)["votos_positivos"].transform("sum")
        if formato_cne:
            # Algunas mesas informadas por CNE tienen cero votos en todos los
            # renglones; no tienen distribución porcentual calculable.
            df = df.loc[totales > 0].copy()
            por_calcular = df["porcentaje"].isna()
            totales = df.groupby(grupos)["votos_positivos"].transform("sum")
        elif (totales[por_calcular] <= 0).any():
            raise ValueError("No se puede calcular el porcentaje: el total de votos positivos es 0.")
        calculado = (df["votos_positivos"] / totales * 100).round(4)
        df.loc[por_calcular, "porcentaje"] = calculado[por_calcular]

    duplicadas = df.duplicated(CLAVE, keep=False)
    if duplicadas.any():
        ejemplos = df.loc[duplicadas, CLAVE].head(3).to_dict("records")
        raise ValueError(
            "Hay filas duplicadas para el mismo año, distrito, circuito, cargo y fuerza. "
            f"Ejemplos: {ejemplos}"
        )

    return df[COLUMNAS_SALIDA].sort_values(CLAVE).reset_index(drop=True)


def construir_comparacion(
    resultados: pd.DataFrame,
    anio_base: int,
    anio_actual: int,
    cargo: str,
    distrito: str | None = None,
    ausencias_son_cero: bool = False,
) -> pd.DataFrame:
    """Pivota dos elecciones a las columnas internas usadas por el dashboard."""
    seleccion = resultados[
        resultados["cargo"].eq(cargo)
        & resultados["anio"].isin([int(anio_base), int(anio_actual)])
    ].copy()
    if distrito and distrito != "Todos":
        seleccion = seleccion[seleccion["distrito"].eq(distrito)]
    if seleccion.empty:
        return pd.DataFrame()

    indices = ["distrito", "circuito", "fuerza"]
    ancho = seleccion.pivot_table(
        index=indices,
        columns="anio",
        values="porcentaje",
        aggfunc="mean",
    ).reindex(columns=[int(anio_base), int(anio_actual)])
    if ausencias_son_cero:
        ancho = ancho.fillna(0.0)
    else:
        ancho = ancho.dropna(how="all")

    comparacion = ancho.reset_index().rename(
        columns={int(anio_base): "pct_2023", int(anio_actual): "pct_2025"}
    )
    comparacion["circuito"] = comparacion.apply(
        lambda fila: (
            f"{fila['distrito']} / {fila['circuito']}"
            if fila["circuito"] != "General"
            else fila["distrito"]
        ),
        axis=1,
    )
    comparacion["diferencia"] = (
        comparacion["pct_2025"] - comparacion["pct_2023"]
    ).round(4)
    return comparacion[["circuito", "fuerza", "pct_2023", "pct_2025", "diferencia"]]
