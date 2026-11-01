"""Prueba de integración de la aplicación Streamlit."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import agora.config as config
import agora.homologacion as homologacion
import agora.ingest as ingest

RAIZ = Path(__file__).resolve().parent.parent

PLANILLA = (
    "anio,distrito,circuito,cargo,fuerza,votos_positivos,porcentaje\n"
    "2023,Capital,C1,PRESIDENTE/A,LA LIBERTAD AVANZA,50,50\n"
    "2023,Capital,C1,PRESIDENTE/A,UNIÓN POR LA PATRIA,30,30\n"
    "2023,Capital,C1,PRESIDENTE/A,FRENTE DE IZQUIERDA Y DE TRABAJADORES,20,20\n"
    "2023,Chilecito,C2,PRESIDENTE/A,LA LIBERTAD AVANZA,50,50\n"
    "2023,Chilecito,C2,PRESIDENTE/A,UNIÓN POR LA PATRIA,30,30\n"
    "2023,Chilecito,C2,PRESIDENTE/A,FRENTE DE IZQUIERDA Y DE TRABAJADORES,20,20\n"
    "2025,Capital,C1,PRESIDENTE/A,LA LIBERTAD AVANZA,45,45\n"
    "2025,Capital,C1,PRESIDENTE/A,UNIÓN POR LA PATRIA,25,25\n"
    "2025,Capital,C1,PRESIDENTE/A,NUEVA FUERZA FEDERAL,30,30\n"
    "2025,Chilecito,C2,PRESIDENTE/A,LA LIBERTAD AVANZA,45,45\n"
    "2025,Chilecito,C2,PRESIDENTE/A,UNIÓN POR LA PATRIA,25,25\n"
    "2025,Chilecito,C2,PRESIDENTE/A,NUEVA FUERZA FEDERAL,30,30\n"
)


class AppTestIntegration(unittest.TestCase):
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
                "AGORA_OFFLINE": "1",
            },
            clear=False,
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        config.get_db_path.cache_clear()
        ingest.cargar_datos_db.clear()
        homologacion.cargar_homologacion.clear()
        ingest.guardar_fuentes_en_db([io.BytesIO(PLANILLA.encode("utf-8"))])

    def test_comparacion_y_dictamen_por_reglas(self) -> None:
        at = AppTest.from_file(str(RAIZ / "app.py"), default_timeout=90)
        at.run()
        self.assertEqual(len(at.exception), 0, at.exception)

        cargos = [s for s in at.selectbox if "Cargo" in s.label]
        self.assertEqual(len(cargos), 1)
        self.assertEqual(cargos[0].value, "PRESIDENTE/A")

        metricas = {m.label: m.value for m in at.metric}
        self.assertEqual(metricas["Primera fuerza · 2025"], "45.00%")
        self.assertEqual(metricas["Volatilidad total (Pedersen)"], "30.00%")
        self.assertEqual(metricas["Tasa de supervivencia"], "66.7%")
        self.assertEqual(metricas["Componente nuevas/extintas"], "25.00%")

        botones = [b for b in at.button if "Generar dictamen" in b.label]
        self.assertTrue(botones)
        botones[0].click().run()
        self.assertEqual(len(at.exception), 0, at.exception)
        self.assertTrue(
            any(
                "Motor analítico de reglas" in c.value
                for c in at.caption
            )
        )
        self.assertTrue(
            any(
                "Datos comprobados" in m.value
                for m in at.markdown
            )
        )


if __name__ == "__main__":
    unittest.main()
