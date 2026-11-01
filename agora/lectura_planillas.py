"""Lectura y normalización de planillas electorales oficiales.

El motor está pensado para archivos reales de escrutinio, donde los datos no
siempre empiezan en la primera fila ni tienen encabezados perfectos. Detecta
metadatos, bloques de encabezado, columnas equivalentes y filas de totales sin
modificar el esquema interno del observatorio.

La salida pública sigue siendo la misma tabla normalizada de siempre:
`anio`, `distrito`, `circuito`, `cargo`, `fuerza`, `votos_positivos` y
`porcentaje`.
"""

from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from .config import CLAVE_ELECTORAL, COLUMNAS_SALIDA
from .utils import (
    clave_fuerza,
    decodificar,
    leer_bytes,
    nombre_fuente,
    normalizar_nombre,
    numero,
    texto,
)

CAMPOS = (
    "fuerza",
    "votos_positivos",
    "porcentaje",
    "anio",
    "cargo",
    "distrito",
    "circuito",
    "mesa",
)

ETIQUETAS_CAMPO = {
    "fuerza": "agrupación política",
    "votos_positivos": "votos positivos",
    "porcentaje": "porcentaje",
    "anio": "año electoral",
    "cargo": "categoría electoral",
    "distrito": "distrito",
    "circuito": "circuito",
    "mesa": "mesa",
}

PESOS_ENCABEZADO = {
    "fuerza": 5,
    "votos_positivos": 5,
    "porcentaje": 4,
    "anio": 2,
    "cargo": 2,
    "distrito": 2,
    "circuito": 2,
    "mesa": 1,
}

_ALIAS_FUERZA = {
    "fuerza",
    "fuerza_politica",
    "agrupacion",
    "agrupacion_politica",
    "agrupacion_nombre",
    "nombre_agrupacion",
    "partido",
    "partido_politico",
    "nombre_partido",
    "alianza",
    "alianza_politica",
    "coalicion",
    "coalicion_politica",
    "lista",
    "lista_partido",
    "nombre_lista",
    "candidato",
    "candidatura",
    "frente",
    "frente_electoral",
    "espacio_politico",
    "organizacion_politica",
}

_ALIAS_ANIO = {
    "anio",
    "ano",
    "year",
    "election_year",
    "periodo",
    "periodo_electoral",
    "anio_electoral",
    "ano_electoral",
    "comicio",
}

_ALIAS_CARGO = {
    "cargo",
    "cargo_electoral",
    "cargo_nombre",
    "categoria",
    "categoria_electoral",
    "tipo_cargo",
    "puesto",
    "office",
}

_ALIAS_DISTRITO = {
    "distrito",
    "distrito_electoral",
    "departamento",
    "provincia",
    "localidad",
    "municipio",
    "seccion_nombre",
    "region",
}

_ALIAS_CIRCUITO = {
    "circuito",
    "circuito_id",
    "circuito_electoral",
    "nombre_circuito",
    "circuito_nombre",
    "circuito_zona",
    "zona",
    "seccion",
    "seccion_electoral",
}

_ALIAS_MESA = {
    "mesa",
    "mesas",
    "mesa_id",
    "numero_mesa",
    "nro_mesa",
    "n_mesa",
    "mesa_electoral",
}

_TOKEN_TOTAL = re.compile(
    r"^(?:total|totales|subtotal|subtotales|suma|gran total|"
    r"total general|total de votos|total votos)\b"
)
_TOKEN_NO_POSITIVO = re.compile(
    r"\b(?:nulo|nula|nulos|nulas|blanco|blancos|impugnad[oa]s?|"
    r"recurrid[oa]s?|anulad[oa]s?|sobre|sobres|no valido|no valida|"
    r"votos validos|votos afirmativos|inscript[oa]s?|padron|electores|"
    r"mesas?|participacion)\b"
)
_ANIO_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_PCT_ANUAL_RE = re.compile(r"(?:pct|porcentaje)_?(\d{4})")


@dataclass
class BloqueDetectado:
    """Un bloque de datos con su encabezado y metadatos contextuales."""

    hoja: str
    fila_encabezado: int
    filas_encabezado: list[int]
    fila_inicio_datos: int
    fila_fin_datos: int
    columnas: list[str]
    mapeo: dict[str, str]
    candidatos: dict[str, list[dict[str, Any]]]
    columnas_pct_anuales: list[str]
    anio: Optional[int]
    cargo: str
    distrito: str
    circuito: str
    metadatos: dict[str, str]
    datos: pd.DataFrame
    advertencias: list[str] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)
    filas_brutas: int = 0
    filas_utiles: int = 0
    filas_excluidas: int = 0
    filas_excluidas_detalle: list[dict[str, Any]] = field(
        default_factory=list
    )

    @property
    def confianza(self) -> str:
        if self.errores:
            return "baja"
        if self.mapeo.get("fuerza") and (
            self.mapeo.get("votos_positivos")
            or self.mapeo.get("porcentaje")
            or self.columnas_pct_anuales
        ):
            return "alta" if self.anio and self.cargo != "No especificado" else "media"
        return "baja"


@dataclass
class InspeccionPlanilla:
    """Resultado de inspeccionar todas las hojas de una planilla."""

    nombre: str
    formato: str
    hojas: dict[str, pd.DataFrame]
    bloques: list[BloqueDetectado]
    advertencias: list[str] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)

    @property
    def hojas_con_datos(self) -> list[str]:
        return list(dict.fromkeys(bloque.hoja for bloque in self.bloques))

    def bloques_de(self, hoja: str) -> list[BloqueDetectado]:
        return [bloque for bloque in self.bloques if bloque.hoja == hoja]

    def bloque_para(self, hoja: str) -> Optional[BloqueDetectado]:
        bloques = self.bloques_de(hoja)
        return bloques[0] if bloques else None

    @property
    def bloque_principal(self) -> Optional[BloqueDetectado]:
        if not self.bloques:
            return None
        return sorted(
            self.bloques,
            key=lambda bloque: (
                bool(bloque.errores),
                bloque.confianza != "alta",
                -bloque.filas_utiles,
            ),
        )[0]


# ---------------------------------------------------------------------------
# Lectura de bytes y hojas
# ---------------------------------------------------------------------------
def _formato(fuente: object) -> str:
    nombre = nombre_fuente(fuente)
    extension = os.path.splitext(nombre)[1].lower()
    if extension in {".xls", ".xlsx", ".xlsm"}:
        return "excel"
    if extension in {".csv", ".txt"}:
        return "csv"
    return ""


def _leer_csv_crudo(contenido: bytes) -> pd.DataFrame:
    cadena = decodificar(contenido)
    if not cadena.strip():
        raise ValueError(
            "El archivo CSV está vacío. Completá la planilla antes de subirla."
        )

    ultimo_error: Optional[Exception] = None
    for opciones in (
        {"sep": None, "engine": "python"},
        {"sep": ",", "engine": "python"},
        {"sep": ";", "engine": "python"},
        {"sep": "\t", "engine": "python"},
    ):
        try:
            tabla = pd.read_csv(
                io.StringIO(cadena),
                header=None,
                dtype=str,
                keep_default_na=False,
                **opciones,
            )
            if len(tabla.columns) >= 2:
                return tabla
        except Exception as error:  # pragma: no cover - depende del archivo
            ultimo_error = error

    raise ValueError(
        "No pude interpretar el CSV. Debe tener al menos dos columnas y usar "
        "coma, punto y coma o tabulación como separador."
    ) from ultimo_error


def _leer_excel_crudo(contenido: bytes) -> dict[str, pd.DataFrame]:
    try:
        libro = pd.read_excel(
            io.BytesIO(contenido),
            sheet_name=None,
            header=None,
            dtype=str,
            keep_default_na=False,
        )
    except Exception as error:
        raise ValueError(
            "No pude abrir la planilla Excel. Verificá que sea un archivo "
            ".xls/.xlsx válido y que no esté protegido."
        ) from error

    return {
        str(nombre): tabla
        for nombre, tabla in libro.items()
        if tabla is not None
    }


def _leer_crudo(fuente: object) -> tuple[dict[str, pd.DataFrame], str]:
    contenido = leer_bytes(fuente)
    formato = _formato(fuente)
    if formato == "excel":
        return _leer_excel_crudo(contenido), "excel"
    if formato == "csv":
        return {"CSV": _leer_csv_crudo(contenido)}, "csv"

    try:
        return {"CSV": _leer_csv_crudo(contenido)}, "csv"
    except Exception as error_csv:
        try:
            return _leer_excel_crudo(contenido), "excel"
        except Exception as error_excel:
            raise ValueError(
                "No pude interpretar la planilla. Probá con un archivo .csv, "
                ".xls o .xlsx con encabezados visibles."
            ) from error_excel


# ---------------------------------------------------------------------------
# Detección de encabezados y metadatos
# ---------------------------------------------------------------------------
def _texto_celda(valor: Any) -> str:
    return texto(valor).replace("\n", " ").strip()


def _fila_vacia(tabla: pd.DataFrame, fila: int) -> bool:
    return not any(_texto_celda(valor) for valor in tabla.iloc[fila].tolist())


def _parece_fila_encabezado(tabla: pd.DataFrame, fila: int) -> bool:
    valores = [
        _texto_celda(valor)
        for valor in tabla.iloc[fila].tolist()
        if _texto_celda(valor)
    ]
    if not valores:
        return False
    numericos = sum(
        1
        for valor in valores
        if re.fullmatch(r"[-+]?\d+(?:[.,]\d+)?\s*%?", valor)
    )
    return numericos / len(valores) <= 0.35


def _columnas_encabezado(tabla: pd.DataFrame, filas: list[int]) -> list[str]:
    etiquetas: list[str] = []
    for columna in range(tabla.shape[1]):
        partes: list[str] = []
        for fila in filas:
            valor = _texto_celda(tabla.iat[fila, columna])
            if valor and valor.casefold() not in {"nan", "none", "null"}:
                partes.append(valor)
        etiqueta = " ".join(dict.fromkeys(partes)).strip()
        if not etiqueta:
            etiqueta = f"col_{columna + 1}"
        etiquetas.append(etiqueta)

    # Los nombres de columna deben ser únicos para poder mapearlos a mano.
    usados: dict[str, int] = {}
    resultado: list[str] = []
    for etiqueta in etiquetas:
        base = etiqueta
        usados[base] = usados.get(base, 0) + 1
        if usados[base] > 1:
            etiqueta = f"{base} __{usados[base]}"
        resultado.append(etiqueta)
    return resultado


def _candidatos_campo(valor: Any) -> list[dict[str, Any]]:
    clave = normalizar_nombre(valor)
    if not clave or clave.startswith("col_"):
        return []
    texto_original = _texto_celda(valor)
    candidatos: list[dict[str, Any]] = []

    def agregar(campo: str, prioridad: int, motivo: str) -> None:
        candidatos.append(
            {"campo": campo, "prioridad": prioridad, "motivo": motivo}
        )

    # Cualquier columna porcentual se trata como porcentaje, nunca como votos.
    if (
        "pct" in clave
        or "porcentaje" in clave
        or "participacion" in clave
        or "%" in texto_original
    ):
        prioridad = 50
        if "afirmativo" in clave or "positivo" in clave:
            prioridad = 10
        elif "valido" in clave:
            prioridad = 60
        elif "general" in clave or "total" in clave:
            prioridad = 80
        agregar("porcentaje", prioridad, "columna porcentual")
        return candidatos

    if "voto" in clave or "sufragio" in clave:
        if any(
            token in clave
            for token in (
                "nulo",
                "blanco",
                "impugn",
                "recurrid",
                "anulad",
                "sobre",
                "total",
                "suma",
                "tipo",
                "mesa",
                "inscript",
                "padron",
            )
        ):
            return []
        if "afirmativo" in clave or "positivo" in clave:
            prioridad = 20
        elif "valido" in clave:
            prioridad = 60
        elif "general" in clave:
            prioridad = 70
        elif "cantidad" in clave or "obtenido" in clave:
            prioridad = 30
        else:
            prioridad = 10
        agregar("votos_positivos", prioridad, "columna de votos")
        return candidatos

    if clave in _ALIAS_FUERZA or any(
        token in clave
        for token in (
            "agrupacion",
            "partido",
            "alianza",
            "coalicion",
            "lista",
            "candidat",
            "fuerza",
            "espacio_politico",
            "frente_electoral",
        )
    ):
        agregar("fuerza", 10, "columna de agrupación")
        return candidatos

    if clave in _ALIAS_ANIO or any(
        token in clave for token in ("anio", "ano", "year", "periodo")
    ):
        agregar("anio", 10, "columna de año")
        return candidatos

    if clave in _ALIAS_CARGO or "cargo" in clave or "categoria" in clave:
        agregar("cargo", 10, "columna de cargo")
        return candidatos

    if clave in _ALIAS_DISTRITO or any(
        token in clave
        for token in (
            "distrito",
            "departamento",
            "provincia",
            "localidad",
            "municipio",
        )
    ):
        agregar("distrito", 10, "columna de distrito")
        return candidatos

    if clave in _ALIAS_CIRCUITO or any(
        token in clave for token in ("circuito", "zona", "seccion")
    ):
        agregar("circuito", 10, "columna de circuito")
        return candidatos

    if clave in _ALIAS_MESA or "mesa" in clave:
        agregar("mesa", 10, "columna de mesa")
        return candidatos

    return []


def _evaluar_encabezado(columnas: list[str]) -> dict[str, Any]:
    candidatos: dict[str, list[dict[str, Any]]] = {
        campo: [] for campo in CAMPOS
    }
    for indice, columna in enumerate(columnas):
        for candidato in _candidatos_campo(columna):
            candidato = dict(candidato)
            candidato["columna"] = columna
            candidato["indice"] = indice
            candidatos[candidato["campo"]].append(candidato)

    mapeo: dict[str, str] = {}
    for campo, lista in candidatos.items():
        if campo == "porcentaje":
            # Las columnas pct_2023/pct_2025 se resuelven después con el
            # mecanismo de tabla ancha; mapearlas como porcentaje único
            # rompería el melt.
            lista = [
                item
                for item in lista
                if not _PCT_ANUAL_RE.fullmatch(
                    normalizar_nombre(item["columna"])
                )
            ]
        if lista:
            lista.sort(
                key=lambda item: (
                    item["prioridad"],
                    item["indice"],
                )
            )
            mapeo[campo] = lista[0]["columna"]

    columnas_pct_anuales = [
        columna
        for columna in columnas
        if _PCT_ANUAL_RE.fullmatch(normalizar_nombre(columna))
    ]
    score = sum(PESOS_ENCABEZADO[campo] for campo in mapeo)
    if columnas_pct_anuales and "porcentaje" not in mapeo:
        score += 3
    valido = bool(
        "fuerza" in mapeo
        and (
            "votos_positivos" in mapeo
            or "porcentaje" in mapeo
            or columnas_pct_anuales
        )
    )
    return {
        "mapeo": mapeo,
        "candidatos": candidatos,
        "columnas_pct_anuales": columnas_pct_anuales,
        "score": score,
        "valido": valido,
    }


def _mejor_encabezado_desde(
    tabla: pd.DataFrame, inicio: int, max_largo: int = 3
) -> Optional[dict[str, Any]]:
    if inicio >= len(tabla) or not _parece_fila_encabezado(tabla, inicio):
        return None

    simple: Optional[dict[str, Any]] = None
    combinados: list[dict[str, Any]] = []
    evaluacion_inicial = _evaluar_encabezado(
        _columnas_encabezado(tabla, [inicio])
    )
    for largo in range(1, max_largo + 1):
        fin = inicio + largo
        if fin > len(tabla):
            break
        filas = list(range(inicio, fin))
        if any(not _parece_fila_encabezado(tabla, fila) for fila in filas):
            break
        if largo > 1 and evaluacion_inicial["score"] < 4:
            # No absorbemos filas de metadatos (por ejemplo "Cargo: ...")
            # dentro del encabezado de datos.
            break
        columnas = _columnas_encabezado(tabla, filas)
        evaluacion = _evaluar_encabezado(columnas)
        if not evaluacion["valido"]:
            continue
        ajustado = (
            evaluacion["score"]
            - 0.25 * (largo - 1)
            - 0.01 * inicio
        )
        propuesta = {
            "inicio": inicio,
            "filas": filas,
            "fin": fin - 1,
            "columnas": columnas,
            "evaluacion": evaluacion,
            "score_ajustado": ajustado,
        }
        if largo == 1:
            simple = propuesta
        else:
            combinados.append(propuesta)

    if simple is not None:
        mejor = simple
        for propuesta in combinados:
            mejora = (
                propuesta["evaluacion"]["score"]
                - simple["evaluacion"]["score"]
            )
            if mejora >= 2 and propuesta["score_ajustado"] > mejor["score_ajustado"]:
                mejor = propuesta
        return mejor

    if combinados:
        return max(combinados, key=lambda item: item["score_ajustado"])
    return None


def _siguiente_encabezado(tabla: pd.DataFrame, desde: int) -> Optional[int]:
    for fila in range(max(0, desde), len(tabla)):
        if not _parece_fila_encabezado(tabla, fila):
            continue
        if _mejor_encabezado_desde(tabla, fila):
            return fila
    return None


def _buscar_etiqueta(
    filas: pd.DataFrame, etiquetas: tuple[str, ...]
) -> str:
    etiquetas_norm = tuple(
        sorted(
            (normalizar_nombre(etiqueta).replace("_", " ") for etiqueta in etiquetas),
            key=len,
            reverse=True,
        )
    )
    encontrado = ""
    for _, fila in filas.iterrows():
        valores = [_texto_celda(valor) for valor in fila.tolist()]
        for indice, valor in enumerate(valores):
            if not valor:
                continue
            norm = normalizar_nombre(valor).replace("_", " ")
            for etiqueta in etiquetas_norm:
                if norm == etiqueta:
                    for siguiente in valores[indice + 1 :]:
                        if siguiente:
                            encontrado = siguiente
                            break
                    break
                if norm.startswith(etiqueta):
                    resto = valor[len(etiqueta) :] if valor else ""
                    if norm[len(etiqueta) :].strip().startswith(
                        (":", "-", "=")
                    ) or resto.strip().startswith((":", "-", "=")):
                        candidato = re.split(
                            r"[:=\-]", valor, maxsplit=1
                        )[-1].strip()
                        if candidato and candidato != valor:
                            encontrado = candidato
                        elif indice + 1 < len(valores) and valores[indice + 1]:
                            encontrado = valores[indice + 1]
                        break
    return encontrado


def _detectar_anio(
    filas: pd.DataFrame,
    nombre_hoja: str,
    nombre_archivo: str,
) -> tuple[Optional[int], list[str]]:
    candidatos: list[tuple[int, int, int]] = []
    advertencias: list[str] = []

    for indice, (_, fila) in enumerate(filas.iterrows()):
        for valor in fila.tolist():
            cadena = _texto_celda(valor)
            if not cadena:
                continue
            anios = _ANIO_RE.findall(cadena)
            if not anios:
                continue
            norm = clave_fuerza(cadena)
            prioridad = 2
            if any(
                token in norm
                for token in (
                    "fecha",
                    "eleccion",
                    "anio",
                    "ano",
                    "periodo",
                    "comicio",
                )
            ):
                prioridad = 1
            for anio in anios:
                candidatos.append((prioridad, indice, int(anio)))

    fuentes: list[tuple[int, int, int]] = list(candidatos)
    for prioridad, nombre in ((3, nombre_hoja), (4, nombre_archivo)):
        anios = _ANIO_RE.findall(texto(nombre))
        for anio in anios:
            fuentes.append((prioridad, 10_000, int(anio)))

    if not fuentes:
        return None, advertencias

    fuentes.sort(key=lambda item: (item[0], -item[1]))
    mejor_prioridad = fuentes[0][0]
    mejores = [item for item in fuentes if item[0] == mejor_prioridad]
    anios_unicos = sorted({item[2] for item in mejores})
    if len(anios_unicos) > 1:
        advertencias.append(
            "Hay más de un año candidato en los metadatos "
            f"({', '.join(map(str, anios_unicos))}); se usó {mejores[0][2]}."
        )
    return mejores[0][2], advertencias


def _detectar_tipo_eleccion(filas: pd.DataFrame) -> str:
    for _, fila in filas.iterrows():
        for valor in fila.tolist():
            norm = clave_fuerza(valor)
            if not norm:
                continue
            if "paso" in norm or "primarias" in norm:
                return "PASO"
            if "generales" in norm or "general" in norm:
                return "GENERALES"
            if "provincial" in norm:
                return "PROVINCIALES"
            if "municipal" in norm:
                return "MUNICIPALES"
    return ""


def _cargo_con_tipo(cargo: str, tipo: str) -> str:
    cargo = texto(cargo)
    tipo = texto(tipo)
    if not cargo:
        return ""
    if not tipo:
        return cargo
    if tipo.casefold() in cargo.casefold():
        return cargo
    return f"{cargo} — {tipo}"


def _metadatos_bloque(
    tabla: pd.DataFrame,
    inicio_encabezado: int,
    nombre_hoja: str,
    nombre_archivo: str,
) -> tuple[dict[str, Any], list[str]]:
    desde = max(0, inicio_encabezado - 40)
    filas_antes = tabla.iloc[desde:inicio_encabezado]
    advertencias: list[str] = []

    anio, avisos_anio = _detectar_anio(
        filas_antes, nombre_hoja, nombre_archivo
    )
    advertencias.extend(avisos_anio)

    cargo = _buscar_etiqueta(
        filas_antes,
        (
            "cargo",
            "cargo electoral",
            "categoria",
            "categoria electoral",
            "tipo de cargo",
        ),
    )
    distrito = _buscar_etiqueta(
        filas_antes,
        (
            "distrito",
            "distrito electoral",
            "departamento",
            "provincia",
            "localidad",
            "municipio",
        ),
    )
    circuito = _buscar_etiqueta(
        filas_antes,
        ("circuito", "zona", "seccion electoral"),
    )
    tipo = _detectar_tipo_eleccion(filas_antes)
    cargo = _cargo_con_tipo(cargo, tipo)

    metadatos = {
        "anio": texto(anio),
        "cargo": cargo,
        "distrito": distrito,
        "circuito": circuito,
        "tipo_eleccion": tipo,
    }
    return metadatos, advertencias


# ---------------------------------------------------------------------------
# Construcción y preparación de bloques
# ---------------------------------------------------------------------------
def _construir_bloque(
    tabla: pd.DataFrame,
    hoja: str,
    propuesta: dict[str, Any],
    fin: int,
    opciones: Optional[dict[str, Any]],
    nombre_archivo: str,
) -> BloqueDetectado:
    opciones = opciones or {}
    inicio = propuesta["inicio"]
    fin_encabezado = propuesta["fin"]
    columnas = propuesta["columnas"]
    evaluacion = propuesta["evaluacion"]

    datos = tabla.iloc[fin_encabezado + 1 : fin, :].copy()
    datos.columns = columnas
    datos = datos.reset_index(drop=True)
    datos.insert(0, "_fila_origen", range(fin_encabezado + 2, fin + 1))

    metadatos, advertencias = _metadatos_bloque(
        tabla, inicio, hoja, nombre_archivo
    )
    anio = opciones.get("anio") or (
        int(metadatos["anio"]) if metadatos.get("anio") else None
    )
    cargo = texto(opciones.get("cargo") or metadatos.get("cargo"))
    distrito = texto(opciones.get("distrito") or metadatos.get("distrito"))
    circuito = texto(opciones.get("circuito") or metadatos.get("circuito"))

    mapeo = dict(evaluacion["mapeo"])
    for campo, columna in (opciones.get("mapeo") or {}).items():
        if campo not in CAMPOS:
            continue
        if columna in (None, "", "(sin columna)"):
            mapeo.pop(campo, None)
        else:
            mapeo[campo] = columna

    bloque = BloqueDetectado(
        hoja=hoja,
        fila_encabezado=inicio + 1,
        filas_encabezado=[fila + 1 for fila in propuesta["filas"]],
        fila_inicio_datos=fin_encabezado + 2,
        fila_fin_datos=fin,
        columnas=columnas,
        mapeo=mapeo,
        candidatos=evaluacion["candidatos"],
        columnas_pct_anuales=evaluacion["columnas_pct_anuales"],
        anio=anio,
        cargo=cargo,
        distrito=distrito,
        circuito=circuito,
        metadatos=metadatos,
        datos=datos,
        advertencias=advertencias,
        filas_brutas=len(datos),
    )

    try:
        preparado, excluidas, avisos, detalle = _preparar_bloque(
            bloque, opciones
        )
        bloque.filas_utiles = len(preparado)
        bloque.filas_excluidas = excluidas
        bloque.filas_excluidas_detalle = detalle
        bloque.advertencias.extend(avisos)
        if excluidas:
            bloque.advertencias.append(
                f"Se excluyeron {excluidas} filas de totales, subtotales o "
                "votos no positivos."
            )
        if not bloque.anio and "anio" not in preparado.columns:
            bloque.errores.append(
                "No pude detectar el año electoral. Ingresalo manualmente o "
                "agregá una columna de año."
            )
    except ValueError as error:
        bloque.errores.append(str(error))

    return bloque


def _detectar_bloques(
    tabla: pd.DataFrame,
    hoja: str,
    opciones: Optional[dict[str, Any]],
    nombre_archivo: str,
) -> list[BloqueDetectado]:
    opciones = opciones or {}
    filas_forzadas = opciones.get("filas_encabezado")
    fila_forzada = opciones.get("fila_encabezado")

    if filas_forzadas:
        filas = sorted({int(fila) - 1 for fila in filas_forzadas})
        if not filas or min(filas) < 0 or max(filas) >= len(tabla):
            return []
        columnas = _columnas_encabezado(tabla, filas)
        evaluacion = _evaluar_encabezado(columnas)
        propuesta = {
            "inicio": min(filas),
            "filas": filas,
            "fin": max(filas),
            "columnas": columnas,
            "evaluacion": evaluacion,
        }
        fin = _siguiente_encabezado(tabla, max(filas) + 1) or len(tabla)
        return [
            _construir_bloque(
                tabla,
                hoja,
                propuesta,
                fin,
                opciones,
                nombre_archivo,
            )
        ]

    if fila_forzada:
        inicio = int(fila_forzada) - 1
        if inicio < 0 or inicio >= len(tabla):
            return []
        propuesta = _mejor_encabezado_desde(tabla, inicio)
        if propuesta is None:
            filas = [inicio]
            columnas = _columnas_encabezado(tabla, filas)
            propuesta = {
                "inicio": inicio,
                "filas": filas,
                "fin": inicio,
                "columnas": columnas,
                "evaluacion": _evaluar_encabezado(columnas),
            }
        fin = _siguiente_encabezado(tabla, propuesta["fin"] + 1) or len(tabla)
        return [
            _construir_bloque(
                tabla,
                hoja,
                propuesta,
                fin,
                opciones,
                nombre_archivo,
            )
        ]

    bloques: list[BloqueDetectado] = []
    fila = 0
    while fila < len(tabla):
        propuesta = _mejor_encabezado_desde(tabla, fila)
        if propuesta is None:
            fila += 1
            continue
        fin = _siguiente_encabezado(tabla, propuesta["fin"] + 1) or len(tabla)
        bloques.append(
            _construir_bloque(
                tabla,
                hoja,
                propuesta,
                fin,
                opciones,
                nombre_archivo,
            )
        )
        fila = fin
    return bloques


def _es_total(valor: Any) -> bool:
    return bool(_TOKEN_TOTAL.search(clave_fuerza(valor)))


def _es_no_positivo(valor: Any) -> bool:
    return bool(_TOKEN_NO_POSITIVO.search(clave_fuerza(valor)))


def _renombrar_mapeo(
    datos: pd.DataFrame, mapeo: dict[str, str]
) -> pd.DataFrame:
    destinos: dict[str, str] = {}
    for campo, columna in mapeo.items():
        if columna not in datos.columns:
            raise ValueError(
                f"La columna '{columna}' elegida para "
                f"{ETIQUETAS_CAMPO[campo]} no existe en la hoja."
            )
        if campo in destinos.values():
            raise ValueError(
                "Dos campos distintos apuntan a la misma columna. Elegí una "
                "columna diferente para cada dato."
            )
        destinos[columna] = campo
    return datos.rename(columns=destinos)


def _preparar_bloque(
    bloque: BloqueDetectado, opciones: Optional[dict[str, Any]]
) -> tuple[pd.DataFrame, int, list[str], list[dict[str, Any]]]:
    opciones = opciones or {}
    manual = (opciones.get("mapeo") or {})
    datos = bloque.datos.copy()
    mapeo = dict(bloque.mapeo)
    for campo, columna in manual.items():
        if campo not in CAMPOS:
            continue
        if columna in (None, "", "(sin columna)"):
            mapeo.pop(campo, None)
        else:
            mapeo[campo] = columna

    if "fuerza" not in mapeo:
        raise ValueError(
            "Falta la columna de agrupación política (Agrupación, Partido, "
            "Alianza, Lista o equivalente)."
        )
    if "votos_positivos" not in mapeo and "porcentaje" not in mapeo:
        if not any(
            _PCT_ANUAL_RE.fullmatch(normalizar_nombre(columna))
            for columna in datos.columns
        ):
            raise ValueError(
                "Falta la columna de votos o de porcentaje. Sin alguno de "
                "esos datos no se pueden calcular los resultados."
            )

    # Un porcentaje general/válido no sirve como porcentaje de votos
    # positivos. Si hay votos, se recalcula para no mezclar nulos ni blancos.
    if "porcentaje" in mapeo and "votos_positivos" in mapeo:
        columna_pct = mapeo["porcentaje"]
        norm_pct = normalizar_nombre(columna_pct)
        porcentaje_manual = "porcentaje" in manual and manual.get("porcentaje")
        if (
            not porcentaje_manual
            and any(token in norm_pct for token in ("general", "valido", "total"))
            and not any(
                token in norm_pct for token in ("afirmativo", "positivo")
            )
        ):
            mapeo.pop("porcentaje", None)

    columna_mesa = mapeo.pop("mesa", None)
    datos = _renombrar_mapeo(datos, mapeo)

    # Si el usuario eligió una columna de votos, las columnas porcentuales
    # anuales son redundantes y no deben hacer que la tabla se derrita.
    columnas_pct_anuales = [
        columna
        for columna in datos.columns
        if _PCT_ANUAL_RE.fullmatch(normalizar_nombre(columna))
    ]
    if "votos_positivos" in datos.columns and columnas_pct_anuales:
        datos = datos.drop(columns=columnas_pct_anuales)
        columnas_pct_anuales = []

    if "fuerza" not in datos.columns:
        raise ValueError("Falta la columna de agrupación política.")

    anio = opciones.get("anio") or bloque.anio
    cargo = texto(opciones.get("cargo") or bloque.cargo)
    distrito = texto(opciones.get("distrito") or bloque.distrito)
    circuito = texto(opciones.get("circuito") or bloque.circuito)

    if "anio" in datos.columns:
        if anio:
            datos["anio"] = datos["anio"].where(
                datos["anio"].astype(str).str.strip().ne(""), anio
            )
    elif not columnas_pct_anuales and anio:
        datos["anio"] = anio

    tipo = texto(bloque.metadatos.get("tipo_eleccion"))
    if "cargo" in datos.columns:
        if cargo:
            datos["cargo"] = datos["cargo"].where(
                datos["cargo"].astype(str).str.strip().ne(""), cargo
            )
        if tipo:
            datos["cargo"] = datos["cargo"].map(
                lambda valor: _cargo_con_tipo(texto(valor), tipo)
                if texto(valor)
                else valor
            )
    elif cargo:
        datos["cargo"] = cargo

    if "distrito" in datos.columns:
        if distrito:
            datos["distrito"] = datos["distrito"].where(
                datos["distrito"].astype(str).str.strip().ne(""), distrito
            )
    elif distrito:
        datos["distrito"] = distrito

    if columna_mesa and "circuito" in mapeo:
        datos = datos.rename(columns={columna_mesa: "_mesa"})
    elif columna_mesa:
        datos["circuito"] = (
            "Mesa " + datos[columna_mesa].astype(str).str.strip()
        )
    elif "circuito" not in datos.columns:
        datos["circuito"] = circuito or "General"

    if "_mesa" in datos.columns and "votos_positivos" not in datos.columns:
        datos["circuito"] = (
            datos["circuito"].astype(str).str.strip()
            + " / Mesa "
            + datos["_mesa"].astype(str).str.strip()
        )
        datos = datos.drop(columns=["_mesa"])

    # Filtrar filas vacías, totales y votos no positivos antes de calcular
    # porcentajes; así los totales no se cuentan dos veces.
    fuerza = datos["fuerza"].fillna("").astype(str).str.strip()
    vacias = fuerza.eq("")
    totales = fuerza.map(_es_total)
    no_positivas = fuerza.map(_es_no_positivo)
    excluidas = totales | no_positivas
    detalle_excluidas: list[dict[str, Any]] = []
    for indice in datos.index[vacias | excluidas]:
        motivo = (
            "sin agrupación"
            if bool(vacias.loc[indice])
            else "total o subtotal"
            if bool(totales.loc[indice])
            else "voto no positivo"
        )
        fila_origen = (
            datos.at[indice, "_fila_origen"]
            if "_fila_origen" in datos.columns
            else ""
        )
        detalle_excluidas.append(
            {
                "fila": int(fila_origen)
                if str(fila_origen).isdigit()
                else fila_origen,
                "fuerza": texto(fuerza.loc[indice]),
                "motivo": motivo,
            }
        )
    datos = datos.loc[~(vacias | excluidas)].copy()
    if datos.empty:
        raise ValueError(
            "No quedaron filas de agrupaciones políticas después de excluir "
            "totales y votos no positivos. Revisá el mapeo de columnas."
        )

    datos["fuerza"] = datos["fuerza"].fillna("").astype(str).str.strip()
    return (
        datos,
        int(excluidas.sum() + vacias.sum()),
        [],
        detalle_excluidas,
    )


def _normalizar_tabla(
    tabla: pd.DataFrame, fuente: object
) -> pd.DataFrame:
    tabla = tabla.copy()
    tabla.columns = [normalizar_nombre(columna) for columna in tabla.columns]
    tabla = tabla.loc[:, [columna for columna in tabla.columns if columna]]
    if len(set(tabla.columns)) != len(tabla.columns):
        raise ValueError("Hay encabezados duplicados después de normalizarlos.")

    tabla = tabla.dropna(how="all")
    if tabla.empty:
        raise ValueError("La planilla no contiene filas de resultados.")

    formato_cne = (
        "votos_tipo" in tabla.columns and "votos_positivos" in tabla.columns
    )
    if formato_cne:
        tabla = tabla[
            tabla["votos_tipo"]
            .astype(str)
            .str.strip()
            .str.upper()
            .eq("POSITIVO")
        ].copy()
        if tabla.empty:
            raise ValueError(
                "El CSV CNE no contiene filas con votos_tipo=POSITIVO."
            )
        if "eleccion_tipo" in tabla.columns:
            tipo = tabla["eleccion_tipo"].fillna("").astype(str).str.strip()
            cargo = (
                tabla["cargo"].fillna("").astype(str).str.strip()
                if "cargo" in tabla.columns
                else pd.Series("", index=tabla.index)
            )
            tabla["cargo"] = (cargo + " — " + tipo).str.strip(" —")
        if "circuito_id" in tabla.columns:
            codigo = tabla["circuito_id"].fillna("").astype(str).str.strip()
            nombre = (
                tabla["circuito"].fillna("").astype(str).str.strip()
                if "circuito" in tabla.columns
                else pd.Series("", index=tabla.index)
            )
            tabla["circuito"] = codigo.where(codigo.ne(""), nombre)

    if "anio" not in tabla.columns:
        nombre_archivo = nombre_fuente(fuente)
        anios = sorted(set(_ANIO_RE.findall(nombre_archivo)))
        if len(anios) == 1:
            tabla["anio"] = anios[0]
        elif len(anios) > 1:
            raise ValueError(
                "El nombre del archivo contiene varios años. Agregá una "
                "columna 'anio' para identificar cada registro."
            )

    columnas_anuales: dict[str, str] = {}
    for columna in tabla.columns:
        coincidencia = _PCT_ANUAL_RE.fullmatch(str(columna))
        if coincidencia:
            columnas_anuales[columna] = coincidencia.group(1)

    if columnas_anuales and "anio" not in tabla.columns:
        if "fuerza" not in tabla.columns:
            raise ValueError("Falta la columna de fuerza/agrupación política.")
        id_vars = [
            columna
            for columna in tabla.columns
            if columna not in columnas_anuales and columna != "diferencia"
        ]
        tabla = tabla.melt(
            id_vars=id_vars,
            value_vars=list(columnas_anuales),
            var_name="columna_periodo",
            value_name="porcentaje",
        )
        tabla["anio"] = tabla["columna_periodo"].map(columnas_anuales)
        tabla = tabla.drop(columns="columna_periodo")

    if "anio" not in tabla.columns or "fuerza" not in tabla.columns:
        raise ValueError(
            "Faltan columnas obligatorias: 'anio' y 'fuerza'. También se "
            "aceptan 'año', 'agrupación política', 'partido' o un año en el "
            "nombre del archivo."
        )
    if not ({"porcentaje", "votos_positivos"} & set(tabla.columns)):
        raise ValueError(
            "Falta 'porcentaje' o 'votos_positivos'. Se puede calcular el "
            "porcentaje a partir de los votos positivos."
        )

    if "distrito" not in tabla.columns:
        tabla["distrito"] = "Departamento no especificado"
    if "circuito" not in tabla.columns:
        tabla["circuito"] = "General"
    if "cargo" not in tabla.columns:
        tabla["cargo"] = "No especificado"
    if "votos_positivos" not in tabla.columns:
        tabla["votos_positivos"] = pd.NA
    if "porcentaje" not in tabla.columns:
        tabla["porcentaje"] = pd.NA

    tabla["anio"] = numero(tabla["anio"], entero=True)
    if tabla["anio"].isna().any() or ((tabla["anio"] % 1) != 0).any():
        raise ValueError(
            "La columna 'anio' debe contener años enteros, por ejemplo 2023."
        )
    tabla["anio"] = tabla["anio"].astype(int)
    tabla["votos_positivos"] = numero(tabla["votos_positivos"], entero=True)
    tabla["porcentaje"] = numero(tabla["porcentaje"])

    for columna in ("distrito", "circuito", "cargo", "fuerza"):
        tabla[columna] = (
            tabla[columna]
            .fillna("")
            .astype(str)
            .str.replace(r"\s+", " ", regex=True)
            .str.strip()
        )

    vacias = tabla["fuerza"].eq("")
    if vacias.any():
        raise ValueError(
            f"Hay {int(vacias.sum())} filas sin nombre de fuerza política."
        )
    if (tabla["votos_positivos"].dropna() < 0).any():
        raise ValueError("'votos_positivos' no puede contener valores negativos.")
    porcentajes_incorrectos = tabla["porcentaje"].notna() & ~tabla[
        "porcentaje"
    ].between(0, 100)
    if porcentajes_incorrectos.any():
        raise ValueError(
            "Los porcentajes deben estar entre 0 y 100 (sin el signo %)."
        )

    if formato_cne:
        tabla = tabla.groupby(
            CLAVE_ELECTORAL, as_index=False, dropna=False
        ).agg(
            votos_positivos=("votos_positivos", "sum"),
            porcentaje=("porcentaje", "first"),
        )

    if "_mesa" in tabla.columns and "votos_positivos" in tabla.columns:
        if tabla["votos_positivos"].notna().all():
            agrupado = tabla.groupby(
                CLAVE_ELECTORAL, as_index=False, dropna=False
            ).agg(votos_positivos=("votos_positivos", "sum"))
            grupos = ["anio", "distrito", "circuito", "cargo"]
            totales = agrupado.groupby(grupos)["votos_positivos"].transform(
                "sum"
            )
            agrupado["porcentaje"] = (
                agrupado["votos_positivos"] / totales * 100
            ).round(4)
            tabla = agrupado

    por_calcular = tabla["porcentaje"].isna()
    if por_calcular.any():
        if tabla.loc[por_calcular, "votos_positivos"].isna().any():
            raise ValueError(
                "Hay filas sin porcentaje y sin votos positivos; completá "
                "al menos uno de esos datos."
            )
        grupos = ["anio", "distrito", "circuito", "cargo"]
        completos = tabla.groupby(grupos)["votos_positivos"].transform(
            lambda serie: serie.notna().all()
        )
        if not completos[por_calcular].all():
            raise ValueError(
                "Para calcular porcentajes faltantes, todas las fuerzas del "
                "mismo circuito deben tener votos positivos informados."
            )
        totales = tabla.groupby(grupos)["votos_positivos"].transform("sum")
        if formato_cne:
            tabla = tabla.loc[totales > 0].copy()
            por_calcular = tabla["porcentaje"].isna()
            totales = tabla.groupby(grupos)["votos_positivos"].transform("sum")
        elif (totales[por_calcular] <= 0).any():
            raise ValueError(
                "No se puede calcular el porcentaje: el total de votos "
                "positivos es 0."
            )
        calculado = (tabla["votos_positivos"] / totales * 100).round(4)
        tabla.loc[por_calcular, "porcentaje"] = calculado[por_calcular]

    duplicadas = tabla.duplicated(CLAVE_ELECTORAL, keep=False)
    if duplicadas.any():
        # Repetir exactamente la misma fila no debe duplicar votos; dos
        # valores distintos para la misma clave sí son un conflicto.
        conflicto = False
        for _, grupo in tabla.loc[duplicadas].groupby(
            CLAVE_ELECTORAL, dropna=False
        ):
            votos = grupo["votos_positivos"].dropna().unique()
            pct = grupo["porcentaje"].dropna().unique()
            if len(votos) > 1 or len(pct) > 1:
                conflicto = True
                break
        if conflicto:
            ejemplos = (
                tabla.loc[duplicadas, CLAVE_ELECTORAL]
                .head(3)
                .to_dict("records")
            )
            raise ValueError(
                "Hay datos contradictorios para la misma clave (año, "
                "distrito, circuito, cargo y fuerza). Ejemplos: "
                f"{ejemplos}"
            )
        tabla = tabla.drop_duplicates(CLAVE_ELECTORAL, keep="first")

    return (
        tabla[COLUMNAS_SALIDA]
        .sort_values(CLAVE_ELECTORAL)
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
def inspeccionar_planilla(
    fuente: object,
    opciones: Optional[dict[str, Any]] = None,
) -> InspeccionPlanilla:
    """Inspecciona todas las hojas y devuelve metadatos y bloques detectados."""
    opciones = opciones or {}
    hojas, formato = _leer_crudo(fuente)
    nombre = nombre_fuente(fuente)
    bloques: list[BloqueDetectado] = []
    advertencias: list[str] = []
    errores: list[str] = []

    hoja_elegida = opciones.get("hoja")
    for nombre_hoja, tabla in hojas.items():
        if tabla is None or tabla.empty or len(tabla.columns) < 2:
            continue
        if hoja_elegida and nombre_hoja != hoja_elegida:
            continue
        detectados = _detectar_bloques(
            tabla,
            nombre_hoja,
            opciones if not hoja_elegida or nombre_hoja == hoja_elegida else {},
            nombre,
        )
        if not detectados:
            if hoja_elegida:
                errores.append(
                    f"No encontré un encabezado electoral en la hoja "
                    f"'{nombre_hoja}'. Elegí la fila de encabezado manualmente."
                )
            continue
        bloques.extend(detectados)

    if not bloques:
        if not errores:
            errores.append(
                "No encontré una fila de encabezados con agrupación política "
                "(fuerza) y votos o porcentaje en ninguna hoja. Revisá el "
                "archivo o seleccioná las columnas manualmente."
            )
    for bloque in bloques:
        advertencias.extend(
            f"{bloque.hoja}: {aviso}" for aviso in bloque.advertencias
        )
        errores.extend(
            f"{bloque.hoja}, fila {bloque.fila_encabezado}: {error}"
            for error in bloque.errores
        )

    return InspeccionPlanilla(
        nombre=nombre,
        formato=formato,
        hojas=hojas,
        bloques=bloques,
        advertencias=advertencias,
        errores=errores,
    )


def _normalizar_con_inspeccion(
    fuente: object,
    opciones: Optional[dict[str, Any]] = None,
) -> tuple[pd.DataFrame, InspeccionPlanilla]:
    opciones = opciones or {}
    inspeccion = inspeccionar_planilla(fuente, opciones)
    if not inspeccion.bloques:
        raise ValueError(
            " ".join(inspeccion.errores)
            or "No encontré datos electorales en la planilla."
        )

    partes: list[pd.DataFrame] = []
    errores: list[str] = []
    for bloque in inspeccion.bloques:
        try:
            preparado, _, _, _ = _preparar_bloque(bloque, opciones)
            partes.append(_normalizar_tabla(preparado, fuente))
        except ValueError as error:
            errores.append(
                f"Hoja '{bloque.hoja}', fila {bloque.fila_encabezado}: "
                f"{error}"
            )

    if not partes:
        raise ValueError(
            "No pude normalizar la planilla. " + " ".join(errores or inspeccion.errores)
        )

    try:
        datos = pd.concat(partes, ignore_index=True, sort=False)
    except Exception as error:  # pragma: no cover - defensivo
        raise ValueError("No pude unir las hojas normalizadas.") from error

    duplicadas = datos.duplicated(CLAVE_ELECTORAL, keep=False)
    if duplicadas.any():
        conflicto = False
        for _, grupo in datos.loc[duplicadas].groupby(
            CLAVE_ELECTORAL, dropna=False
        ):
            if grupo["votos_positivos"].dropna().nunique() > 1:
                conflicto = True
                break
            if grupo["porcentaje"].dropna().nunique() > 1:
                conflicto = True
                break
        if conflicto:
            ejemplos = (
                datos.loc[duplicadas, CLAVE_ELECTORAL]
                .head(3)
                .to_dict("records")
            )
            raise ValueError(
                "Las hojas contienen datos contradictorios para la misma "
                f"clave. Ejemplos: {ejemplos}"
            )
        datos = datos.drop_duplicates(CLAVE_ELECTORAL, keep="first")

    return (
        datos[COLUMNAS_SALIDA]
        .sort_values(CLAVE_ELECTORAL)
        .reset_index(drop=True),
        inspeccion,
    )


def normalizar_planilla(
    fuente: object,
    opciones: Optional[dict[str, Any]] = None,
) -> pd.DataFrame:
    """Convierte CSV/XLS/XLSX en el esquema interno del observatorio."""
    datos, _ = _normalizar_con_inspeccion(fuente, opciones)
    return datos
