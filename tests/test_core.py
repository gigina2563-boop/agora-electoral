"""Pruebas de núcleo de Ágora Electoral (sin navegador)."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import agora.ai as ai
import agora.config as config
import agora.homologacion as homologacion
import agora.ingest as ingest
import agora.metrics as metrics


class BaseTemporal(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = os.path.join(self._tmp.name, "observatorio.db")
        self._env = patch.dict(
            os.environ,
            {
                "AGORA_DB_PATH": self.db,
                "AGORA_SEED": "0",
                "AGORA_ENTORNO": "produccion",
                "GROQ_API_KEY": "",
                "GEMINI_API_KEY": "",
                "GOOGLE_API_KEY": "",
                "OLLAMA_ENABLED": "",
            },
            clear=False,
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        config.get_db_path.cache_clear()
        ingest.cargar_datos_db.clear()
        homologacion.cargar_homologacion.clear()


class IngestaTests(BaseTemporal):
    def test_csv_estandar_con_decimales_argentinos(self) -> None:
        contenido = (
            "anio,distrito,circuito,cargo,fuerza,votos_positivos,porcentaje\n"
            "2023,Capital,C1,PRESIDENTE/A,Fuerza A,1.234,60\n"
            "2023,Capital,C1,PRESIDENTE/A,Fuerza B,800,40\n"
        ).encode("utf-8")
        tabla = ingest.normalizar_planilla(io.BytesIO(contenido))
        self.assertEqual(len(tabla), 2)
        self.assertEqual(tabla["anio"].unique().tolist(), [2023])
        self.assertEqual(
            set(tabla["fuerza"]), {"Fuerza A", "Fuerza B"}
        )
        self.assertAlmostEqual(
            float(tabla.loc[tabla["fuerza"] == "Fuerza A", "porcentaje"].iloc[0]),
            60.0,
        )

    def test_planilla_ancha_pct(self) -> None:
        contenido = (
            "fuerza,circuito,pct_2023,pct_2025\n"
            "A,C1,60,50\n"
            "B,C1,40,30\n"
            "C,C1,0,20\n"
        ).encode("utf-8")
        tabla = ingest.normalizar_planilla(io.BytesIO(contenido))
        self.assertEqual(sorted(tabla["anio"].unique().tolist()), [2023, 2025])
        self.assertEqual(len(tabla), 6)
        self.assertEqual(tabla["porcentaje"].notna().sum(), 6)

    def test_anio_desde_nombre_de_archivo(self) -> None:
        ruta = Path(self._tmp.name) / "resultados_2023.csv"
        ruta.write_text(
            "fuerza,circuito,votos_positivos,porcentaje\n"
            "A,C1,60,60\nB,C1,40,40\n",
            encoding="utf-8",
        )
        tabla = ingest.normalizar_planilla(str(ruta))
        self.assertEqual(tabla["anio"].unique().tolist(), [2023])

    def test_excel_con_hoja_anual(self) -> None:
        ruta = Path(self._tmp.name) / "resultados.xlsx"
        with pd.ExcelWriter(ruta, engine="openpyxl") as salida:
            pd.DataFrame(
                {
                    "fuerza": ["A", "B"],
                    "circuito": ["C1", "C1"],
                    "votos_positivos": [60, 40],
                    "porcentaje": [60, 40],
                }
            ).to_excel(salida, sheet_name="2023", index=False)
        tabla = ingest.normalizar_planilla(str(ruta))
        self.assertEqual(tabla["anio"].unique().tolist(), [2023])
        self.assertEqual(len(tabla), 2)

    def test_csv_cne_agrega_mesas_y_filtra_positivos(self) -> None:
        contenido = (
            "año,eleccion_tipo,seccion_nombre,circuito_id,cargo_nombre,"
            "agrupacion_nombre,votos_tipo,votos_cantidad\n"
            "2023,GENERAL,Capital,0001A,PRESIDENTE/A,Fuerza A,POSITIVO,60\n"
            "2023,GENERAL,Capital,0001A,PRESIDENTE/A,Fuerza A,POSITIVO,40\n"
            "2023,GENERAL,Capital,0001A,PRESIDENTE/A,Fuerza B,POSITIVO,100\n"
            "2023,GENERAL,Capital,0001A,PRESIDENTE/A,Fuerza B,NULO,999\n"
        ).encode("utf-8")
        tabla = ingest.normalizar_planilla(io.BytesIO(contenido))
        self.assertEqual(len(tabla), 2)
        self.assertEqual(
            tabla["cargo"].unique().tolist(), ["PRESIDENTE/A — GENERAL"]
        )
        self.assertEqual(
            tabla["circuito"].unique().tolist(), ["0001A"]
        )
        fila_a = tabla[tabla["fuerza"] == "Fuerza A"].iloc[0]
        fila_b = tabla[tabla["fuerza"] == "Fuerza B"].iloc[0]
        self.assertEqual(int(fila_a["votos_positivos"]), 100)
        self.assertEqual(int(fila_b["votos_positivos"]), 100)
        self.assertAlmostEqual(float(fila_a["porcentaje"]), 50.0, places=3)
        self.assertAlmostEqual(float(fila_b["porcentaje"]), 50.0, places=3)

    def test_errores_de_validacion(self) -> None:
        casos = [
            (
                "anio,distrito,circuito,cargo,votos_positivos\n"
                "2023,C,C1,P,10\n",
                "fuerza",
            ),
            (
                "fuerza,circuito,votos_positivos\nA,C1,10\n",
                "anio",
            ),
            (
                "anio,distrito,circuito,cargo,fuerza,votos_positivos\n"
                "2023,C,C1,P,A,-1\n",
                "negativos",
            ),
            (
                "anio,distrito,circuito,cargo,fuerza,porcentaje\n"
                "2023,C,C1,P,A,120\n",
                "porcentajes",
            ),
        ]
        for contenido, fragmento in casos:
            with self.subTest(fragmento=fragmento):
                with self.assertRaisesRegex(
                    ValueError, fragmento, msg=contenido
                ):
                    ingest.normalizar_planilla(
                        io.BytesIO(contenido.encode("utf-8"))
                    )

    def test_porcentaje_se_calcula_si_falta(self) -> None:
        contenido = (
            "anio,distrito,circuito,cargo,fuerza,votos_positivos\n"
            "2023,C,C1,P,A,60\n2023,C,C1,P,B,40\n"
        ).encode("utf-8")
        tabla = ingest.normalizar_planilla(io.BytesIO(contenido))
        self.assertAlmostEqual(
            float(tabla.loc[tabla["fuerza"] == "A", "porcentaje"].iloc[0]),
            60.0,
            places=3,
        )

    def test_guarda_y_lee_sqlite(self) -> None:
        contenido = (
            "anio,distrito,circuito,cargo,fuerza,votos_positivos,porcentaje\n"
            "2023,C,C1,P,A,60,60\n2023,C,C1,P,B,40,40\n"
        ).encode("utf-8")
        primera = ingest.guardar_fuentes_en_db([io.BytesIO(contenido)])
        self.assertEqual(primera, 2)
        self.assertEqual(len(ingest.cargar_datos_db()), 2)
        # Volver a cargar la misma clave no duplica.
        ingest.guardar_fuentes_en_db([io.BytesIO(contenido)])
        self.assertEqual(len(ingest.cargar_datos_db()), 2)

    def test_xls_oficial_zona_detecta_metadatos_y_totales(self) -> None:
        ruta = Path(__file__).parent / "fixtures" / "zona_DN_DIST_12.xls"
        inspeccion = ingest.inspeccionar_planilla(str(ruta))
        self.assertEqual(inspeccion.formato, "excel")
        self.assertEqual(len(inspeccion.bloques), 1)
        bloque = inspeccion.bloques[0]
        self.assertEqual(bloque.fila_encabezado, 15)
        self.assertEqual(bloque.anio, 2021)
        self.assertIn("DIPUTADO NACIONAL", bloque.cargo)
        self.assertEqual(bloque.distrito, "LA RIOJA")
        self.assertEqual(bloque.filas_utiles, 5)
        self.assertEqual(bloque.filas_excluidas, 5)
        motivos = {
            fila["fuerza"]: fila["motivo"]
            for fila in bloque.filas_excluidas_detalle
        }
        self.assertIn("TOTALES", motivos)
        self.assertIn("VOTOS NULOS", motivos)
        self.assertEqual(
            motivos["TOTALES"], "total o subtotal"
        )

        tabla = ingest.normalizar_planilla(str(ruta))
        self.assertEqual(len(tabla), 5)
        self.assertEqual(tabla["anio"].unique().tolist(), [2021])
        self.assertEqual(tabla["fuerza"].nunique(), 5)
        self.assertEqual(tabla["circuito"].unique().tolist(), ["General"])
        self.assertEqual(int(tabla["votos_positivos"].sum()), 178151)
        self.assertNotIn("VOTOS NULOS", tabla["fuerza"].tolist())
        self.assertNotIn("TOTALES", tabla["fuerza"].tolist())

    def test_encabezado_en_dos_filas_y_totales(self) -> None:
        ruta = Path(self._tmp.name) / "encabezado_doble.xlsx"
        filas = [
            ["JUSTICIA NACIONAL ELECTORAL", "", "", ""],
            ["ELECCIONES GENERALES 2025", "", "", ""],
            ["Cargo: GOBERNADOR", "", "", ""],
            ["Agrupación Política", "Votos", "Votos", ""],
            ["", "", "Afirmativos [%]", "Nulos"],
            ["Fuerza A", 60, 60, 10],
            ["Fuerza B", 40, 40, 5],
            ["VOTOS NULOS", 10, "", ""],
            ["TOTALES", 110, 100, ""],
        ]
        pd.DataFrame(filas).to_excel(
            ruta, index=False, header=False, engine="openpyxl"
        )
        inspeccion = ingest.inspeccionar_planilla(str(ruta))
        self.assertEqual(len(inspeccion.bloques), 1)
        bloque = inspeccion.bloques[0]
        self.assertEqual(bloque.filas_encabezado, [4, 5])
        self.assertEqual(bloque.anio, 2025)
        self.assertIn("GOBERNADOR", bloque.cargo)

        tabla = ingest.normalizar_planilla(str(ruta))
        self.assertEqual(len(tabla), 2)
        self.assertEqual(int(tabla["votos_positivos"].sum()), 100)
        self.assertEqual(sorted(tabla["porcentaje"].tolist()), [40.0, 60.0])

    def test_multiples_hojas_no_mezclan_categorias(self) -> None:
        ruta = Path(self._tmp.name) / "categorias.xlsx"
        with pd.ExcelWriter(ruta, engine="openpyxl") as salida:
            pd.DataFrame(
                [
                    ["Cargo: DIPUTADO NACIONAL"],
                    ["Partido", "Votos"],
                    ["A", 60],
                    ["B", 40],
                ]
            ).to_excel(salida, sheet_name="2023", index=False, header=False)
            pd.DataFrame(
                [
                    ["Cargo: SENADOR NACIONAL"],
                    ["Partido", "Votos"],
                    ["A", 70],
                    ["B", 30],
                ]
            ).to_excel(salida, sheet_name="2025", index=False, header=False)

        inspeccion = ingest.inspeccionar_planilla(str(ruta))
        self.assertEqual(inspeccion.hojas_con_datos, ["2023", "2025"])
        self.assertEqual(
            {bloque.cargo for bloque in inspeccion.bloques},
            {"DIPUTADO NACIONAL", "SENADOR NACIONAL"},
        )
        tabla = ingest.normalizar_planilla(str(ruta))
        self.assertEqual(len(tabla), 4)
        self.assertEqual(sorted(tabla["anio"].unique().tolist()), [2023, 2025])
        self.assertEqual(
            set(tabla["cargo"]), {"DIPUTADO NACIONAL", "SENADOR NACIONAL"}
        )

    def test_mapeo_manual_de_columnas_ambiguas(self) -> None:
        contenido = (
            "Concepto,Valor,Participacion\n"
            "A,60,60\n"
            "B,40,40\n"
        ).encode("utf-8")
        with self.assertRaisesRegex(ValueError, "fuerza"):
            ingest.normalizar_planilla(io.BytesIO(contenido))

        tabla = ingest.normalizar_planilla(
            io.BytesIO(contenido),
            opciones={
                "fila_encabezado": 1,
                "anio": 2023,
                "mapeo": {
                    "fuerza": "Concepto",
                    "votos_positivos": "Valor",
                    "porcentaje": "Participacion",
                },
            },
        )
        self.assertEqual(len(tabla), 2)
        self.assertEqual(tabla["anio"].unique().tolist(), [2023])
        self.assertEqual(sorted(tabla["porcentaje"].tolist()), [40.0, 60.0])

    def test_fuente_original_se_guarda_para_auditoria(self) -> None:
        contenido = (
            "anio,distrito,circuito,cargo,fuerza,votos_positivos,porcentaje\n"
            "2023,C,C1,P,A,60,60\n"
            "2023,C,C1,P,B,40,40\n"
        ).encode("utf-8")
        ingest.guardar_fuentes_en_db([io.BytesIO(contenido)])
        fuentes = ingest.listar_fuentes_db()
        self.assertEqual(len(fuentes), 1)

        conexion = sqlite3.connect(self.db)
        try:
            original, resumen_json = conexion.execute(
                "SELECT contenido, resumen_json FROM planillas_fuentes"
            ).fetchone()
        finally:
            conexion.close()
        self.assertEqual(bytes(original), contenido)
        resumen = json.loads(resumen_json)
        self.assertEqual(resumen["bloques"][0]["fila_encabezado"], 1)
        self.assertEqual(resumen["bloques"][0]["mapeo"]["fuerza"], "fuerza")

    def test_semilla_construye_base(self) -> None:
        with patch.dict(os.environ, {"AGORA_SEED": "1"}):
            sembrada = ingest.asegurar_db_sembrada()
        self.assertTrue(sembrada)
        datos = ingest.cargar_datos_db()
        self.assertGreater(len(datos), 1000)
        self.assertIn(2023, datos["anio"].unique().tolist())


class MetricasTests(BaseTemporal):
    def test_serie_agregada_pondera_por_votos(self) -> None:
        tabla = pd.DataFrame(
            {
                "fuerza": ["A", "A", "B", "B"],
                "pct": [50.0, 60.0, 50.0, 40.0],
                "votos": [50.0, 60.0, 50.0, 40.0],
            }
        )
        serie = metrics.serie_agregada(tabla, "pct", "votos")
        self.assertAlmostEqual(float(serie["A"]), 55.0)
        self.assertAlmostEqual(float(serie["B"]), 45.0)

    def test_serie_agregada_sin_votos_usa_promedio(self) -> None:
        tabla = pd.DataFrame(
            {
                "fuerza": ["A", "A", "B", "B"],
                "pct": [50.0, 70.0, 50.0, 30.0],
                "votos": [pd.NA, pd.NA, pd.NA, pd.NA],
            }
        )
        serie = metrics.serie_agregada(tabla, "pct", "votos")
        self.assertAlmostEqual(float(serie["A"]), 60.0)
        self.assertAlmostEqual(float(serie["B"]), 40.0)

    def test_pedersen_nuevas_y_extintas(self) -> None:
        tabla = pd.DataFrame(
            {
                "fuerza": ["A", "B", "C", "D"],
                "pct_base": [60.0, 40.0, 0.0, 0.0],
                "pct_actual": [50.0, 30.0, 0.0, 20.0],
            }
        )
        total, detalle = metrics.calcular_indice_pedersen(tabla)
        self.assertAlmostEqual(total, 20.0)
        estados = dict(zip(detalle["fuerza"], detalle["estado"]))
        self.assertEqual(estados["A"], "Sobreviviente")
        self.assertEqual(estados["B"], "Sobreviviente")
        self.assertEqual(estados["D"], "Nueva")
        tabla_extinta = pd.DataFrame(
            {
                "fuerza": ["A", "B"],
                "pct_base": [60.0, 40.0],
                "pct_actual": [60.0, 0.0],
            }
        )
        _, detalle_extinta = metrics.calcular_indice_pedersen(tabla_extinta)
        self.assertEqual(
            dict(zip(detalle_extinta["fuerza"], detalle_extinta["estado"]))["B"],
            "Extinta",
        )

    def test_supervivencia(self) -> None:
        tabla = pd.DataFrame(
            {
                "fuerza": ["A", "B", "C", "D"],
                "pct_base": [50.0, 30.0, 20.0, 0.0],
                "pct_actual": [45.0, 25.0, 0.0, 30.0],
            }
        )
        resumen, detalle = metrics.analizar_supervivencia(tabla)
        self.assertEqual(resumen["fuerzas_supervivientes"], 2)
        self.assertEqual(resumen["fuerzas_iniciales"], 3)
        self.assertAlmostEqual(resumen["tasa_supervivencia"], 66.67, places=1)
        self.assertEqual(len(detalle), 4)

    def test_nep(self) -> None:
        nep = metrics.calcular_nep(pd.Series([50.0, 30.0, 20.0]))
        self.assertAlmostEqual(nep, 2.63, places=2)

    def test_comparacion_conserva_fuerza_nueva(self) -> None:
        datos = pd.DataFrame(
            {
                "anio": [2023, 2023, 2025, 2025, 2025],
                "distrito": ["C"] * 5,
                "circuito": ["C1"] * 5,
                "cargo": ["P"] * 5,
                "fuerza": ["A", "B", "A", "B", "Nueva"],
                "porcentaje": [60.0, 40.0, 50.0, 30.0, 20.0],
                "votos_positivos": [60.0, 40.0, 50.0, 30.0, 20.0],
            }
        )
        comparacion = metrics.construir_comparacion(
            datos, 2023, 2025, "P", "Todos", True
        )
        self.assertEqual(len(comparacion), 3)
        resumen = metrics.agregar_por_fuerza(comparacion)
        self.assertIn("Nueva", resumen["fuerza"].tolist())
        fila_nueva = resumen[resumen["fuerza"] == "Nueva"].iloc[0]
        self.assertEqual(float(fila_nueva["pct_base"]), 0.0)
        self.assertAlmostEqual(float(fila_nueva["pct_actual"]), 20.0)


class HomologacionTests(BaseTemporal):
    def test_aplica_regla_por_anio(self) -> None:
        tabla = pd.DataFrame(
            [
                {
                    "fuerza_origen": "Partido A",
                    "fuerza_homologada": "A 2023",
                    "tipo": "Partido",
                    "anio_desde": 2023,
                    "anio_hasta": 2023,
                    "notas": "",
                },
                {
                    "fuerza_origen": "Partido A",
                    "fuerza_homologada": "A 2025",
                    "tipo": "Partido",
                    "anio_desde": 2025,
                    "anio_hasta": 2025,
                    "notas": "",
                },
            ]
        )
        datos = pd.DataFrame(
            {
                "anio": [2023, 2025],
                "fuerza": ["Partido A", "Partido A"],
                "porcentaje": [10.0, 20.0],
            }
        )
        resultado = homologacion.aplicar_homologacion(datos, tabla)
        self.assertEqual(resultado["fuerza"].tolist(), ["A 2023", "A 2025"])
        self.assertEqual(
            resultado["fuerza_origen"].tolist(),
            ["Partido A", "Partido A"],
        )

    def test_tildes_y_mayusculas(self) -> None:
        tabla = pd.DataFrame(
            [
                {
                    "fuerza_origen": "UNIÓN POR LA PATRIA",
                    "fuerza_homologada": "UNIÓN POR LA PATRIA",
                    "tipo": "Alianza",
                    "anio_desde": pd.NA,
                    "anio_hasta": pd.NA,
                    "notas": "",
                }
            ]
        )
        datos = pd.DataFrame({"anio": [2025], "fuerza": ["union por la patria"]})
        resultado = homologacion.aplicar_homologacion(datos, tabla)
        self.assertEqual(resultado["fuerza"].iloc[0], "UNIÓN POR LA PATRIA")

    def test_guardar_cargar_y_faltantes(self) -> None:
        tabla = homologacion.limpiar_homologacion(
            pd.DataFrame(
                [
                    {
                        "fuerza_origen": "Fuerza A",
                        "fuerza_homologada": "A",
                        "tipo": "Partido",
                        "anio_desde": "",
                        "anio_hasta": "",
                        "notas": "",
                    }
                ]
            )
        )
        self.assertEqual(homologacion.guardar_homologacion(tabla), 1)
        cargada = homologacion.cargar_homologacion()
        self.assertEqual(len(cargada), 1)
        datos = pd.DataFrame(
            {
                "anio": [2023, 2023],
                "fuerza": ["Fuerza A", "Fuerza B"],
            }
        )
        self.assertEqual(
            homologacion.fuerzas_sin_mapear(datos, cargada), ["Fuerza B"]
        )
        self.assertEqual(
            homologacion.sembrar_identidades(datos, cargada), 1
        )
        self.assertEqual(
            homologacion.fuerzas_sin_mapear(
                datos, homologacion.cargar_homologacion()
            ),
            [],
        )

    def test_validaciones(self) -> None:
        with self.assertRaisesRegex(ValueError, "fuerza_homologada"):
            homologacion.limpiar_homologacion(
                pd.DataFrame([{"fuerza_origen": "A"}])
            )
        with self.assertRaisesRegex(ValueError, "mayor"):
            homologacion.limpiar_homologacion(
                pd.DataFrame(
                    [
                        {
                            "fuerza_origen": "A",
                            "fuerza_homologada": "A",
                            "anio_desde": 2025,
                            "anio_hasta": 2023,
                        }
                    ]
                )
            )

    def test_semilla(self) -> None:
        homologacion.asegurar_homologacion_sembrada()
        tabla = homologacion.cargar_homologacion()
        self.assertGreaterEqual(len(tabla), 8)
        self.assertIn(
            "UNIÓN POR LA PATRIA", tabla["fuerza_homologada"].tolist()
        )


class MotorTests(BaseTemporal):
    def setUp(self) -> None:
        # Aísla las pruebas de claves reales en `.streamlit/secrets.toml`.
        # Cada prueba controla las variables de entorno que necesita.
        self._config_patch = patch(
            "agora.ai.config",
            side_effect=lambda nombre, defecto=None: os.environ.get(
                nombre, defecto
            ),
        )
        self._config_patch.start()
        self.addCleanup(self._config_patch.stop)

    def _contexto(self) -> dict:
        tabla_fuerzas = pd.DataFrame(
            {
                "fuerza": ["A", "B"],
                "pct_base": [50.0, 50.0],
                "pct_actual": [55.0, 45.0],
            }
        )
        territorial = pd.DataFrame(
            {
                "circuito": ["C1", "C1"],
                "fuerza": ["A", "B"],
                "pct_base": [50.0, 50.0],
                "pct_actual": [55.0, 45.0],
                "diferencia": [5.0, -5.0],
            }
        )
        return ai.construir_contexto_dictamen(
            "A",
            tabla_fuerzas,
            territorial,
            2023,
            2025,
            10.0,
            {
                "tasa_supervivencia": 100.0,
                "fuerzas_supervivientes": 2,
                "fuerzas_iniciales": 2,
                "pedersen_nuevas_y_extintas": 0.0,
            },
            cargo="PRESIDENTE/A",
            distrito="Todos",
            n_registros=2,
        )

    def test_prompt_separa_secciones(self) -> None:
        prompt = ai.construir_prompt(self._contexto())
        for encabezado in (
            "Datos comprobados",
            "Indicadores calculados",
            "Interpretación politológica",
            "Prospectiva 2027",
            "Nota metodológica",
        ):
            self.assertIn(encabezado, prompt)
        self.assertIn("No inventes", prompt)
        self.assertIn("homologación", prompt)

    def test_reglas_genera_estructura(self) -> None:
        texto = ai.generar_dictamen_reglas(self._contexto())
        for encabezado in (
            "Datos comprobados",
            "Indicadores calculados",
            "Interpretación politológica",
            "Prospectiva 2027",
            "Nota metodológica",
        ):
            self.assertIn(encabezado, texto)
        self.assertIn("Pedersen", texto)
        self.assertIn("55,00%", texto)

    def test_groq_es_primero_y_ollama_no_se_usa_en_produccion(self) -> None:
        informe = (
            "## Datos comprobados\n- A y B.\n\n"
            "## Indicadores calculados\n- Pedersen 10%.\n\n"
            "## Interpretación politológica\n- Lectura sobria.\n"
        )
        with patch.dict(
            os.environ,
            {
                "GROQ_API_KEY": "test",
                "AGORA_ENTORNO": "produccion",
                "OLLAMA_ENABLED": "",
            },
        ):
            with patch(
                "agora.ai.consultar_groq",
                return_value=(informe, "Groq · test"),
            ) as groq, patch("agora.ai.consultar_ollama") as ollama:
                resultado = ai.generar_dictamen_hibrido(self._contexto())
        self.assertEqual(resultado["texto"], informe)
        self.assertTrue(resultado["motor"].startswith("Groq"))
        groq.assert_called_once()
        ollama.assert_not_called()
        self.assertEqual(resultado["intentos"], [])

    def test_respuesta_sin_estructura_cae_a_reglas(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GROQ_API_KEY": "test",
                "GEMINI_API_KEY": "",
                "GOOGLE_API_KEY": "",
                "AGORA_ENTORNO": "produccion",
                "OLLAMA_ENABLED": "",
            },
        ):
            with patch(
                "agora.ai.consultar_groq",
                return_value=("Texto libre sin secciones", "Groq · test"),
            ):
                resultado = ai.generar_dictamen_hibrido(self._contexto())
        self.assertIn("Motor analítico de reglas", resultado["motor"])
        self.assertIn("Datos comprobados", resultado["texto"])
        self.assertTrue(
            any(
                "secciones obligatorias" in intento
                for intento in resultado["intentos"]
            )
        )

    def test_fallback_a_reglas_sin_claves(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GROQ_API_KEY": "",
                "GEMINI_API_KEY": "",
                "GOOGLE_API_KEY": "",
                "AGORA_ENTORNO": "produccion",
                "OLLAMA_ENABLED": "",
            },
        ):
            resultado = ai.generar_dictamen_hibrido(self._contexto())
        self.assertIn("Motor analítico de reglas", resultado["motor"])
        self.assertIn("Datos comprobados", resultado["texto"])

    def test_ollama_local_si_se_habilita(self) -> None:
        informe = (
            "## Datos comprobados\n- A y B.\n\n"
            "## Indicadores calculados\n- Pedersen 10%.\n\n"
            "## Interpretación politológica\n- Lectura local.\n"
        )
        with patch.dict(
            os.environ,
            {
                "GROQ_API_KEY": "",
                "GEMINI_API_KEY": "",
                "GOOGLE_API_KEY": "",
                "AGORA_ENTORNO": "local",
                "OLLAMA_ENABLED": "",
            },
        ):
            with patch(
                "agora.ai.consultar_ollama",
                return_value=(informe, "Ollama local · test"),
            ) as ollama:
                resultado = ai.generar_dictamen_hibrido(self._contexto())
        self.assertEqual(resultado["texto"], informe)
        self.assertIn("Ollama local", resultado["motor"])
        ollama.assert_called_once()

    def test_consultar_groq_usa_endpoint_configurable(self) -> None:
        class Respuesta:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "choices": [
                        {"message": {"content": "Informe de prueba"}}
                    ]
                }

        with patch.dict(
            os.environ,
            {
                "GROQ_API_KEY": "test",
                "GROQ_API_BASE": "http://127.0.0.1:9999/openai/v1",
            },
        ):
            with patch("agora.ai.requests.post", return_value=Respuesta()) as post:
                contenido, motor = ai.consultar_groq("prompt")
        self.assertEqual(contenido, "Informe de prueba")
        self.assertIn("Groq", motor)
        self.assertIn(
            "http://127.0.0.1:9999/openai/v1/chat/completions",
            post.call_args.args[0],
        )


class HigieneTests(unittest.TestCase):
    def test_sin_rutas_absolutas_del_autor(self) -> None:
        raiz = Path(__file__).resolve().parent.parent
        archivos = [raiz / "app.py", *sorted((raiz / "agora").glob("*.py"))]
        for archivo in archivos:
            contenido = archivo.read_text(encoding="utf-8")
            self.assertNotIn("/home/gigina", contenido, msg=str(archivo))

    def test_no_hay_modulos_locales_rotos(self) -> None:
        raiz = Path(__file__).resolve().parent.parent
        fuente = (raiz / "app.py").read_text(encoding="utf-8")
        self.assertIn("setup_page()", fuente)
        self.assertIn("from agora.ui import main", fuente)
        self.assertLess(
            fuente.index("setup_page()"),
            fuente.index("from agora.ui import main"),
        )


if __name__ == "__main__":
    unittest.main()
