"""Pruebas de la identidad visual y del empaquetado para Streamlit Cloud."""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


class IdentidadVisualTests(unittest.TestCase):
    def test_cabecera_usa_paleta_y_titulo_institucional(self) -> None:
        html = (RAIZ / "agora" / "assets" / "header.html").read_text(
            encoding="utf-8"
        )
        css = (RAIZ / "agora" / "assets" / "header.css").read_text(
            encoding="utf-8"
        )

        self.assertIn("Ágora", html)
        self.assertIn("Electoral", html)
        self.assertIn("Datos abiertos", html)
        self.assertIn("Consulta pública", html)
        self.assertIn("#4c1d95", html)
        self.assertIn("#ffd600", html)

        for color in (
            "#4c1d95",
            "#7c3aed",
            "#c084fc",
            "#ffd600",
            "#f8f5ff",
            "#241238",
        ):
            self.assertIn(color, css.lower())
        self.assertIn("linear-gradient", css)
        self.assertIn(".agora-hero", css)
        self.assertIn(".agora-model-strip", css)
        self.assertIn("@media (max-width: 760px)", css)

    def test_logo_es_aproximadamente_50_por_ciento_mas_grande(self) -> None:
        css = (RAIZ / "agora" / "assets" / "header.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("width: 105px", css)
        self.assertIn("height: 105px", css)

    def test_tema_streamlit_y_version(self) -> None:
        config = tomllib.loads(
            (RAIZ / ".streamlit" / "config.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(config["theme"]["primaryColor"].lower(), "#7c3aed")
        self.assertEqual(config["theme"]["backgroundColor"].lower(), "#f8f5ff")

        from agora.config import APP_VERSION, PALETA

        self.assertEqual(APP_VERSION, "2.2.1")
        self.assertEqual(PALETA["violeta_oscuro"], "#4c1d95")
        self.assertEqual(PALETA["amarillo"], "#ffd600")

    def test_dependencias_y_entrada_para_cloud(self) -> None:
        requirements = (RAIZ / "requirements.txt").read_text(encoding="utf-8")
        for dependencia in (
            "streamlit",
            "pandas",
            "requests",
            "openpyxl",
            "xlrd",
        ):
            self.assertIn(dependencia, requirements)

        app = (RAIZ / "app.py").read_text(encoding="utf-8")
        self.assertIn("setup_page()", app)
        self.assertIn("from agora.ui import main", app)
        self.assertTrue((RAIZ / ".streamlit" / "secrets.toml.example").exists())


if __name__ == "__main__":
    unittest.main()
