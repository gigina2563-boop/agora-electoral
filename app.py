"""Ágora Electoral · punto de entrada Streamlit.

La lógica está separada en el paquete `agora/` para facilitar el mantenimiento
sin alterar los módulos históricos `analizador.py`, `ingesta_electoral.py` y
`respuesta.py`, que se conservan en el repositorio.
"""

from __future__ import annotations

from agora.config import setup_page

# Debe ser la primera instrucción de Streamlit de la aplicación.
setup_page()

from agora.ui import main  # noqa: E402  (import posterior a set_page_config)


if __name__ == "__main__":
    main()
