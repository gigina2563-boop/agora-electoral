"""Prueba de integración de la aplicación Streamlit."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import agora.auth as auth
import agora.config as config
import agora.homologacion as homologacion
import agora.ingest as ingest

RAIZ = Path(__file__).resolve().parent.parent
CLAVE_ADMIN = "clave-admin-de-prueba-2026"
HASH_ADMIN = auth.generar_hash_password(CLAVE_ADMIN, iteraciones=100_000)

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
        self._auth_config = patch(
            "agora.auth.config",
            side_effect=self._config_auth,
        )
        self._auth_config.start()
        self.addCleanup(self._auth_config.stop)
        config.get_db_path.cache_clear()
        ingest.cargar_datos_db.clear()
        homologacion.cargar_homologacion.clear()
        with patch("agora.auth.es_admin", return_value=True):
            ingest.guardar_fuentes_en_db(
                [io.BytesIO(PLANILLA.encode("utf-8"))]
            )

    @staticmethod
    def _config_auth(
        nombre: str, defecto: str | None = None
    ) -> str | None:
        if nombre == "ADMIN_PASSWORD_HASH":
            return HASH_ADMIN
        return config.config(nombre, defecto)

    def _abrir_login(self, at: AppTest) -> None:
        at.run()
        self.assertEqual(len(at.exception), 0, at.exception)
        botones = [b for b in at.button if b.label == "Acceso administrador"]
        self.assertTrue(botones)
        botones[0].click().run()
        self.assertEqual(len(at.exception), 0, at.exception)

    def test_visitante_no_ve_controles_administrativos(self) -> None:
        at = AppTest.from_file(str(RAIZ / "app.py"), default_timeout=90)
        at.run()
        self.assertEqual(len(at.exception), 0, at.exception)
        self.assertIn(
            "Acceso administrador", [b.label for b in at.button]
        )
        self.assertNotIn("Cerrar sesión", [b.label for b in at.button])
        self.assertFalse(
            any(
                "Carga de planillas" in expander.label
                for expander in at.expander
            )
        )
        self.assertEqual(len(at.file_uploader), 0)
        self.assertNotIn(
            "Editar homologación", [b.label for b in at.button]
        )
        self.assertNotIn(
            "Editar tabla de homologación", [b.label for b in at.button]
        )
        self.assertIn(
            "Resultados electorales", [tab.label for tab in at.tabs]
        )

    def test_login_incorrecto_no_habilita_administracion(self) -> None:
        at = AppTest.from_file(str(RAIZ / "app.py"), default_timeout=90)
        self._abrir_login(at)
        password = [
            campo for campo in at.text_input if campo.label == "Contraseña"
        ]
        self.assertTrue(password)
        password[0].set_value("clave-incorrecta")
        ingresar = [b for b in at.button if b.label == "Ingresar"]
        self.assertTrue(ingresar)
        ingresar[0].click().run()
        self.assertEqual(len(at.exception), 0, at.exception)
        self.assertTrue(
            any("Contraseña incorrecta" in error.value for error in at.error)
        )
        self.assertNotIn("Cerrar sesión", [b.label for b in at.button])
        self.assertEqual(len(at.file_uploader), 0)

    def test_login_correcto_habilita_y_cerrar_sesion_bloquea(self) -> None:
        at = AppTest.from_file(str(RAIZ / "app.py"), default_timeout=90)
        self._abrir_login(at)
        password = [
            campo for campo in at.text_input if campo.label == "Contraseña"
        ]
        self.assertTrue(password)
        password[0].set_value(CLAVE_ADMIN)
        ingresar = [b for b in at.button if b.label == "Ingresar"]
        self.assertTrue(ingresar)
        ingresar[0].click().run()
        self.assertEqual(len(at.exception), 0, at.exception)
        self.assertIn("Cerrar sesión", [b.label for b in at.button])
        self.assertTrue(
            any(
                "Carga de planillas" in expander.label
                for expander in at.expander
            )
        )
        self.assertTrue(
            any(
                "Subí una o varias planillas" in cargador.label
                for cargador in at.file_uploader
            )
        )
        self.assertIn(
            "Editar homologación", [b.label for b in at.button]
        )

        cerrar = [b for b in at.button if b.label == "Cerrar sesión"]
        cerrar[0].click().run()
        self.assertEqual(len(at.exception), 0, at.exception)
        self.assertIn(
            "Acceso administrador", [b.label for b in at.button]
        )
        self.assertNotIn("Cerrar sesión", [b.label for b in at.button])
        self.assertEqual(len(at.file_uploader), 0)
        self.assertNotIn(
            "Editar homologación", [b.label for b in at.button]
        )

    def test_panel_de_homologacion_historica_para_administradora(self) -> None:
        at = AppTest.from_file(str(RAIZ / "app.py"), default_timeout=90)
        self._abrir_login(at)
        password = [
            campo for campo in at.text_input if campo.label == "Contraseña"
        ]
        password[0].set_value(CLAVE_ADMIN)
        [b for b in at.button if b.label == "Ingresar"][0].click().run()
        self.assertEqual(len(at.exception), 0, at.exception)

        abrir = [
            b for b in at.button if b.label == "Homologación histórica"
        ]
        self.assertTrue(abrir)
        abrir[0].click().run()
        self.assertEqual(len(at.exception), 0, at.exception)

        etiquetas = [tab.label for tab in at.tabs]
        for etiqueta in (
            "Identidades",
            "Denominaciones",
            "Relaciones",
            "Bloques",
            "Ausencias",
            "Conflictos y duplicados",
        ):
            self.assertIn(etiqueta, etiquetas)
        self.assertTrue(
            any(
                "Validada · se aplica" in bloque.value
                for bloque in at.markdown
            )
        )
        self.assertTrue(
            any(
                "Provisoria · requiere fuente" in bloque.value
                for bloque in at.markdown
            )
        )

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
