"""Pruebas del modelo de homologación histórica y de la migración."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import agora.config as config
import agora.homologacion as homologacion_legacy
import agora.homologacion_historica as historica
import agora.metrics as metrics
from agora.homologacion_historica import (
    aplicar_homologacion_historica,
    cargar_modelo_historico,
    limpiar_denominaciones,
    validar_bloques_miembros,
    validar_denominaciones,
    validar_relaciones,
)
from agora.migraciones import VERSION_ACTUAL, aplicar_migraciones


class BaseHistorica(unittest.TestCase):
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
            },
            clear=False,
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        self._auth = patch("agora.auth.es_admin", return_value=True)
        self._auth.start()
        self.addCleanup(self._auth.stop)
        config.get_db_path.cache_clear()
        homologacion_legacy.cargar_homologacion.clear()
        aplicar_migraciones(self.db, respaldar=False)
        self.modelo = cargar_modelo_historico(self.db)

    def _datos(self) -> pd.DataFrame:
        filas = [
            ("JUNTOS PORLA RIOJA", 2019, "LA_RIOJA"),
            ("VAMOS LA RIOJA", 2021, "LA_RIOJA"),
            ("UNIDOS", 2021, "LA_RIOJA"),
            ("JUNTOS POR EL CAMBIO", 2023, "LA_RIOJA"),
            ("FRENTE DE TODOS", 2019, "LA_RIOJA"),
            ("FRENTE DE TODOS", 2021, "LA_RIOJA"),
            ("UNIÓN POR LA PATRIA", 2023, "LA_RIOJA"),
            (
                "FRENTE DE IZQUIERDA Y DE TRABAJADORES - UNIDAD",
                2019,
                "LA_RIOJA",
            ),
            (
                "FRENTE DE IZQUIERDA Y DE TRABJADORES - UNIDAD",
                2019,
                "LA_RIOJA",
            ),
            ("FRENTE DE IZQUIERDA Y DE TRABAJADORES", 2023, "LA_RIOJA"),
            ("DEMOCRATA CRISTIANO", 2023, "LA_RIOJA"),
            ("UNION DEL CENTRO DEMOCRATICO", 2021, "LA_RIOJA"),
            ("COMPROMISO FEDERAL", 2019, "LA_RIOJA"),
        ]
        return pd.DataFrame(
            [
                {
                    "anio": anio,
                    "distrito": distrito,
                    "circuito": "General",
                    "cargo": "DIPUTADO NACIONAL",
                    "fuerza": fuerza,
                    "votos_positivos": 100,
                    "porcentaje": 10,
                }
                for fuerza, anio, distrito in filas
            ]
        )


class HomologacionHistoricaTests(BaseHistorica):
    def test_denominaciones_se_resuelven_una_vez_por_combinacion(self) -> None:
        datos = pd.concat([self._datos()] * 40, ignore_index=True)
        with patch.object(
            historica,
            "_denominacion_para",
            wraps=historica._denominacion_para,
        ) as espia:
            salida = aplicar_homologacion_historica(
                datos, self.modelo, "estricto"
            )

        self.assertEqual(len(salida), len(datos))
        # 13 combinaciones fuerza/año distintas en la muestra, repetidas 40
        # veces. El caché debe evitar una búsqueda por cada fila.
        self.assertLessEqual(espia.call_count, 20)

    def test_casos_obligatorios_y_variantes_ortograficas(self) -> None:
        salida = aplicar_homologacion_historica(
            self._datos(), self.modelo, "estricto"
        )
        mapa = {
            fila["fuerza_original"]: fila["fuerza"]
            for _, fila in salida.iterrows()
        }
        self.assertEqual(mapa["JUNTOS PORLA RIOJA"], "JUNTOS POR LA RIOJA")
        self.assertEqual(mapa["VAMOS LA RIOJA"], "VAMOS LA RIOJA")
        self.assertEqual(mapa["UNIDOS"], "UNIDOS")
        self.assertEqual(mapa["JUNTOS POR EL CAMBIO"], "JUNTOS POR EL CAMBIO")
        self.assertEqual(mapa["FRENTE DE TODOS"], "FRENTE DE TODOS")
        self.assertEqual(mapa["UNIÓN POR LA PATRIA"], "UNIÓN POR LA PATRIA")
        self.assertEqual(
            mapa["DEMOCRATA CRISTIANO"], "DEMÓCRATA CRISTIANO"
        )
        self.assertEqual(
            mapa["UNION DEL CENTRO DEMOCRATICO"],
            "UNIÓN DEL CENTRO DEMOCRÁTICO",
        )
        fit = "FRENTE DE IZQUIERDA Y DE TRABAJADORES - UNIDAD"
        for variante in (
            fit,
            "FRENTE DE IZQUIERDA Y DE TRABJADORES - UNIDAD",
            "FRENTE DE IZQUIERDA Y DE TRABAJADORES",
        ):
            self.assertEqual(mapa[variante], fit)

    def test_distrito_se_normaliza_sin_perder_origen(self) -> None:
        salida = aplicar_homologacion_historica(
            self._datos(), self.modelo, "estricto"
        )
        self.assertEqual(set(salida["distrito"]), {"LA RIOJA"})
        self.assertEqual(set(salida["distrito_clave"]), {"la rioja"})
        self.assertEqual(set(salida["distrito_original"]), {"LA_RIOJA"})
        self.assertIn("distrito_original", salida.columns)

    def test_regla_pendiente_no_se_aplica(self) -> None:
        salida = aplicar_homologacion_historica(
            self._datos(), self.modelo, "estricto"
        )
        fila = salida[salida["fuerza_original"].eq("COMPROMISO FEDERAL")].iloc[0]
        self.assertEqual(fila["estado_homologacion"], "pendiente")
        self.assertEqual(fila["fuerza"], "COMPROMISO FEDERAL")
        self.assertEqual(fila["nivel_analisis"], "sin_homologar")

    def test_relaciones_provisorias_no_se_aplican(self) -> None:
        datos = self._datos()
        estricto = aplicar_homologacion_historica(
            datos, self.modelo, "estricto"
        )
        relaciones = aplicar_homologacion_historica(
            datos, self.modelo, "relaciones"
        )
        fila_estricta = estricto[
            estricto["fuerza_original"].eq("JUNTOS PORLA RIOJA")
        ].iloc[0]
        fila_relacion = relaciones[
            relaciones["fuerza_original"].eq("JUNTOS PORLA RIOJA")
        ].iloc[0]
        self.assertEqual(fila_estricta["fuerza"], "JUNTOS POR LA RIOJA")
        self.assertEqual(fila_relacion["fuerza"], "JUNTOS POR LA RIOJA")

    def test_relacion_validada_se_aplica_solo_en_escenario_correspondiente(
        self,
    ) -> None:
        with sqlite3.connect(self.db) as conexion:
            conexion.execute(
                "UPDATE fuerzas_relaciones SET estado = 'validada' "
                "WHERE origen_id = (SELECT id FROM fuerzas_identidades "
                "WHERE nombre = 'JUNTOS POR LA RIOJA')"
            )
            conexion.commit()
        modelo = cargar_modelo_historico(self.db)
        datos = self._datos()
        estricto = aplicar_homologacion_historica(
            datos, modelo, "estricto"
        )
        relaciones = aplicar_homologacion_historica(
            datos, modelo, "relaciones"
        )
        self.assertEqual(
            estricto[
                estricto["fuerza_original"].eq("JUNTOS PORLA RIOJA")
            ].iloc[0]["fuerza"],
            "JUNTOS POR LA RIOJA",
        )
        self.assertEqual(
            relaciones[
                relaciones["fuerza_original"].eq("JUNTOS PORLA RIOJA")
            ].iloc[0]["fuerza"],
            "VAMOS LA RIOJA",
        )

    def test_integracion_de_alianza_validada_se_aplica(self) -> None:
        with sqlite3.connect(self.db) as conexion:
            conexion.execute(
                "UPDATE fuerzas_relaciones SET estado = 'validada' "
                "WHERE tipo_relacion = 'integracion_alianza' "
                "AND origen_id = (SELECT id FROM fuerzas_identidades "
                "WHERE nombre = 'UNIDOS')"
            )
            conexion.commit()
        modelo = cargar_modelo_historico(self.db)
        salida = aplicar_homologacion_historica(
            self._datos(), modelo, "relaciones"
        )
        fila = salida[salida["fuerza_original"].eq("UNIDOS")].iloc[0]
        self.assertEqual(fila["fuerza"], "JUNTOS POR EL CAMBIO")
        self.assertIn("integracion_alianza", fila["relacion_aplicada"])

    def test_bloques_provisorios_no_se_aplican(self) -> None:
        datos = self._datos()
        salida = aplicar_homologacion_historica(datos, self.modelo, "bloques")
        fila = salida[salida["fuerza_original"].eq("UNIDOS")].iloc[0]
        self.assertEqual(fila["fuerza"], "UNIDOS")
        self.assertEqual(fila["bloque"], "")

    def test_bloque_validado_agrupa_solo_sus_miembros(self) -> None:
        with sqlite3.connect(self.db) as conexion:
            conexion.execute(
                "UPDATE bloques_electorales SET estado = 'validada' "
                "WHERE nombre = 'JUNTOS POR EL CAMBIO'"
            )
            conexion.execute(
                "UPDATE bloque_miembros SET estado = 'validada' "
                "WHERE bloque_id = (SELECT id FROM bloques_electorales "
                "WHERE nombre = 'JUNTOS POR EL CAMBIO')"
            )
            conexion.commit()
        modelo = cargar_modelo_historico(self.db)
        salida = aplicar_homologacion_historica(
            self._datos(), modelo, "bloques"
        )
        unidos = salida[salida["fuerza_original"].eq("UNIDOS")].iloc[0]
        frente = salida[
            salida["fuerza_original"].eq("FRENTE DE TODOS")
        ].iloc[0]
        self.assertEqual(unidos["fuerza"], "JUNTOS POR EL CAMBIO")
        self.assertEqual(unidos["nivel_analisis"], "bloque")
        self.assertEqual(frente["fuerza"], "FRENTE DE TODOS")
        self.assertEqual(frente["nivel_analisis"], "alianza")

    def test_conflicto_de_denominaciones_se_detecta(self) -> None:
        tabla = pd.DataFrame(
            [
                {
                    "nombre_electoral": "FUERZA X",
                    "identidad": "A",
                    "nombre_clave": "fuerza x",
                    "anio_desde": 2023,
                    "anio_hasta": 2023,
                    "tipo_eleccion": "",
                    "cargo_clave": "",
                    "distrito_clave": "",
                    "nivel": "partido",
                },
                {
                    "nombre_electoral": "FUERZA X",
                    "identidad": "B",
                    "nombre_clave": "fuerza x",
                    "anio_desde": 2023,
                    "anio_hasta": 2023,
                    "tipo_eleccion": "",
                    "cargo_clave": "",
                    "distrito_clave": "",
                    "nivel": "partido",
                },
            ]
        )
        conflictos = validar_denominaciones(tabla)
        self.assertEqual(len(conflictos), 1)

    def test_conflicto_de_relaciones_y_bloques_se_detecta(self) -> None:
        relaciones = pd.DataFrame(
            [
                {
                    "origen": "A",
                    "destino": "B",
                    "tipo_relacion": "continuidad",
                    "anio_desde": 2021,
                    "anio_hasta": 2023,
                    "cargo_clave": "",
                    "distrito_clave": "",
                    "nivel": "alianza",
                },
                {
                    "origen": "A",
                    "destino": "C",
                    "tipo_relacion": "continuidad",
                    "anio_desde": 2023,
                    "anio_hasta": 2023,
                    "cargo_clave": "",
                    "distrito_clave": "",
                    "nivel": "alianza",
                },
            ]
        )
        self.assertEqual(len(validar_relaciones(relaciones)), 1)
        miembros = pd.DataFrame(
            [
                {
                    "bloque": "BLOQUE 1",
                    "identidad": "A",
                    "anio_desde": 2021,
                    "anio_hasta": 2023,
                },
                {
                    "bloque": "BLOQUE 2",
                    "identidad": "A",
                    "anio_desde": 2023,
                    "anio_hasta": 2023,
                },
            ]
        )
        self.assertEqual(len(validar_bloques_miembros(miembros)), 1)

    def test_composicion_no_se_marca_como_destino_contradictorio(self) -> None:
        relaciones = pd.DataFrame(
            [
                {
                    "origen": "ALIANZA COMPUESTA",
                    "destino": "PARTIDO A",
                    "tipo_relacion": "composicion",
                    "anio_desde": 2023,
                    "anio_hasta": 2023,
                    "cargo_clave": "",
                    "distrito_clave": "",
                    "nivel": "alianza",
                },
                {
                    "origen": "ALIANZA COMPUESTA",
                    "destino": "PARTIDO B",
                    "tipo_relacion": "composicion",
                    "anio_desde": 2023,
                    "anio_hasta": 2023,
                    "cargo_clave": "",
                    "distrito_clave": "",
                    "nivel": "alianza",
                },
            ]
        )
        self.assertEqual(validar_relaciones(relaciones), [])

    def test_legacy_no_pisa_denominacion_validada(self) -> None:
        legacy = pd.DataFrame(
            [
                {
                    "fuerza_origen": "FRENTE DE IZQUIERDA Y DE TRABAJADORES - UNIDAD",
                    "fuerza_homologada": "FRENTE DE IZQUIERDA Y DE TRABAJADORES",
                    "tipo": "Alianza",
                    "anio_desde": pd.NA,
                    "anio_hasta": pd.NA,
                    "notas": "",
                }
            ]
        )
        salida = aplicar_homologacion_historica(
            self._datos(), self.modelo, "estricto", legacy
        )
        fila = salida[
            salida["fuerza_original"].eq(
                "FRENTE DE IZQUIERDA Y DE TRABAJADORES - UNIDAD"
            )
        ].iloc[0]
        self.assertEqual(
            fila["fuerza"], "FRENTE DE IZQUIERDA Y DE TRABAJADORES - UNIDAD"
        )
        self.assertEqual(fila["estado_homologacion"], "validada")

    def test_denominacion_pendiente_no_cae_en_legacy(self) -> None:
        legacy = pd.DataFrame(
            [
                {
                    "fuerza_origen": "COMPROMISO FEDERAL",
                    "fuerza_homologada": "COMPROMISO FEDERAL",
                    "tipo": "Alianza",
                    "anio_desde": pd.NA,
                    "anio_hasta": pd.NA,
                    "notas": "",
                }
            ]
        )
        salida = aplicar_homologacion_historica(
            self._datos(), self.modelo, "estricto", legacy
        )
        fila = salida[
            salida["fuerza_original"].eq("COMPROMISO FEDERAL")
        ].iloc[0]
        self.assertEqual(fila["estado_homologacion"], "pendiente")
        self.assertEqual(fila["fuente_homologacion"], "Fuente primaria pendiente")

    def test_limpieza_no_unifica_por_parecido(self) -> None:
        tabla = pd.DataFrame(
            [
                {
                    "nombre_electoral": "UNIDOS",
                    "identidad": "UNIDOS",
                    "anio_desde": 2021,
                    "anio_hasta": 2021,
                },
                {
                    "nombre_electoral": "UNIDOS POR LA RIOJA",
                    "identidad": "OTRA",
                    "anio_desde": 2021,
                    "anio_hasta": 2021,
                },
            ]
        )
        limpia = limpiar_denominaciones(tabla, self.modelo.identidades)
        self.assertEqual(len(limpia), 2)
        self.assertEqual(
            set(limpia["identidad"]), {"UNIDOS", "OTRA"}
        )


class MetricasDatosFaltantesTests(unittest.TestCase):
    def _comparacion(self, actual_extra: bool) -> pd.DataFrame:
        filas = [
            (2019, "Capital", "C1", "A", 60.0),
            (2019, "Capital", "C1", "B", 40.0),
            (2023, "Capital", "C1", "A", 50.0),
            (2023, "Capital", "C1", "B", 30.0),
            (2023, "Capital", "C1", "C", 20.0),
        ]
        if actual_extra:
            filas.append((2023, "Capital", "C2", "A", 10.0))
        return pd.DataFrame(
            [
                {
                    "anio": anio,
                    "distrito": distrito,
                    "circuito": circuito,
                    "cargo": "DIPUTADO NACIONAL",
                    "fuerza": fuerza,
                    "votos_positivos": pct,
                    "porcentaje": pct,
                }
                for anio, distrito, circuito, fuerza, pct in filas
            ]
        )

    def test_ausencia_verificada_por_cobertura_completa(self) -> None:
        comparacion = metrics.construir_comparacion(
            self._comparacion(False),
            2019,
            2023,
            "DIPUTADO NACIONAL",
        )
        c = comparacion[comparacion["fuerza"].eq("C")].iloc[0]
        self.assertEqual(c["estado_base"], "ausencia_verificada")
        self.assertEqual(float(c["pct_base"]), 0.0)
        detalle = metrics.agregar_por_fuerza(comparacion)
        total, _ = metrics.calcular_indice_pedersen(detalle)
        self.assertAlmostEqual(total, 20.0)

    def test_ausencia_sin_cobertura_comparable_no_se_completa(self) -> None:
        comparacion = metrics.construir_comparacion(
            self._comparacion(True),
            2019,
            2023,
            "DIPUTADO NACIONAL",
        )
        c = comparacion[comparacion["fuerza"].eq("C")].iloc[0]
        self.assertEqual(c["estado_base"], "sin_dato")
        self.assertTrue(pd.isna(c["pct_base"]))
        resumen, _ = metrics.analizar_supervivencia(
            metrics.agregar_por_fuerza(comparacion)
        )
        self.assertTrue(resumen["pedersen_parcial"])

    def test_niveles_distintos_no_son_comparables(self) -> None:
        datos = self._comparacion(False)
        datos["nivel_analisis"] = "alianza"
        datos.loc[datos["anio"].eq(2023), "nivel_analisis"] = "legacy"
        resultado = metrics.validar_comparacion(
            datos, 2019, 2023, "DIPUTADO NACIONAL"
        )
        self.assertFalse(resultado["comparable"])
        self.assertFalse(resultado["mismo_nivel"])
        self.assertTrue(
            any("niveles de análisis" in aviso for aviso in resultado["advertencias"])
        )

    def test_nep_y_pedersen(self) -> None:
        self.assertAlmostEqual(
            metrics.calcular_nep(pd.Series([50.0, 30.0, 20.0])),
            2.631578947,
            places=6,
        )
        detalle = pd.DataFrame(
            {
                "fuerza": ["A", "B", "Nueva"],
                "pct_base": [60.0, 40.0, 0.0],
                "pct_actual": [50.0, 30.0, 20.0],
                "estado_base": ["presente", "presente", "ausencia_verificada"],
                "estado_actual": ["presente", "presente", "presente"],
            }
        )
        total, tabla = metrics.calcular_indice_pedersen(detalle)
        self.assertAlmostEqual(total, 20.0)
        self.assertEqual(set(tabla["estado"]), {"Sobreviviente", "Nueva"})


class MigracionTests(BaseHistorica):
    def test_migracion_es_idempotente_y_audita_duplicados(self) -> None:
        ruta = os.path.join(self._tmp.name, "legacy.db")
        with sqlite3.connect(ruta) as conexion:
            conexion.execute(
                """
                CREATE TABLE resultados_electorales (
                    anio INTEGER NOT NULL,
                    distrito TEXT NOT NULL,
                    circuito TEXT NOT NULL,
                    cargo TEXT NOT NULL,
                    fuerza TEXT NOT NULL,
                    votos_positivos REAL,
                    porcentaje REAL NOT NULL
                )
                """
            )
            conexion.executemany(
                "INSERT INTO resultados_electorales VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (2023, "LA_RIOJA", "C1", "DIPUTADO", "A", 10, 10),
                    (2023, "LA_RIOJA", "C1", "DIPUTADO", "A", 10, 10),
                    (2023, "LA_RIOJA", "C1", "DIPUTADO", "B", 20, 20),
                ],
            )
        informe = aplicar_migraciones(ruta, respaldar=False)
        self.assertTrue(informe["migro"])
        self.assertEqual(informe["resultados"]["antes"]["filas"], 3)
        self.assertEqual(informe["resultados"]["despues"]["filas"], 2)
        self.assertEqual(informe["resultados"]["duplicados"], 1)
        with sqlite3.connect(ruta) as conexion:
            self.assertEqual(
                conexion.execute(
                    "SELECT COUNT(*) FROM esquema_migraciones WHERE version = ?",
                    (VERSION_ACTUAL,),
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conexion.execute(
                    "SELECT COUNT(*) FROM registros_auditoria"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conexion.execute(
                    "SELECT COUNT(*) FROM resultados_electorales"
                ).fetchone()[0],
                2,
            )
        segundo = aplicar_migraciones(ruta, respaldar=False)
        self.assertFalse(segundo["migro"])

    def test_conflicto_de_importacion_se_marca_para_revision(self) -> None:
        ruta = os.path.join(self._tmp.name, "conflicto.db")
        with sqlite3.connect(ruta) as conexion:
            conexion.execute(
                """
                CREATE TABLE resultados_electorales (
                    anio INTEGER NOT NULL,
                    distrito TEXT NOT NULL,
                    circuito TEXT NOT NULL,
                    cargo TEXT NOT NULL,
                    fuerza TEXT NOT NULL,
                    votos_positivos REAL,
                    porcentaje REAL NOT NULL
                )
                """
            )
            conexion.executemany(
                "INSERT INTO resultados_electorales VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (2023, "LA_RIOJA", "C1", "DIPUTADO", "A", 10, 10),
                    (2023, "LA_RIOJA", "C1", "DIPUTADO", "A", 11, 11),
                ],
            )
        informe = aplicar_migraciones(ruta, respaldar=False)
        self.assertEqual(informe["resultados"]["conflictos"], 1)
        with sqlite3.connect(ruta) as conexion:
            self.assertEqual(
                conexion.execute(
                    "SELECT COUNT(*) FROM registros_auditoria "
                    "WHERE requiere_revision = 1 AND estado = 'conflicto'"
                ).fetchone()[0],
                1,
            )

    def test_permisos_de_escritura(self) -> None:
        from agora.homologacion_historica import guardar_identidades

        with patch("agora.auth.es_admin", return_value=False):
            with self.assertRaises(PermissionError):
                guardar_identidades(self.modelo.identidades)


if __name__ == "__main__":
    unittest.main()
