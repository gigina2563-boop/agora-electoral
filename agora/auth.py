"""Acceso público y autenticación mínima de la administradora.

El observatorio es de consulta pública. Sólo la sesión autenticada puede
importar planillas o modificar la homologación. La contraseña nunca se guarda
en el código: se configura únicamente su hash en ``st.secrets`` o en la
variable de entorno ``ADMIN_PASSWORD_HASH``.

Formato del hash::

    pbkdf2_sha256$600000$<sal_base64url>$<hash_base64url>

Para generarlo, ejecutar ``python -m agora.auth`` y pegar el valor impreso en
Secrets. La contraseña se escribe sin eco en la terminal.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from typing import Optional

import streamlit as st

from .config import config

SESSION_KEY = "agora_admin_autenticada"
_LOGIN_ABIERTO_KEY = "agora_login_abierto"
_INTENTOS_KEY = "agora_admin_intentos"
_BLOQUEO_KEY = "agora_admin_bloqueo_hasta"

_ALGORITMO = "pbkdf2_sha256"
_ITERACIONES = 600_000
_ITERACIONES_MINIMAS = 100_000
_ITERACIONES_MAXIMAS = 2_000_000
_SALT_BYTES = 16
_HASH_BYTES = 32
_MAX_INTENTOS = 5
_BLOQUEO_SEGUNDOS = 60


def _b64_codificar(valor: bytes) -> str:
    return base64.urlsafe_b64encode(valor).decode("ascii").rstrip("=")


def _b64_decodificar(valor: str) -> bytes:
    relleno = "=" * (-len(valor) % 4)
    return base64.urlsafe_b64decode(valor + relleno)


def _parsear_hash(hash_guardado: Optional[str]) -> Optional[tuple[int, bytes, bytes]]:
    if not hash_guardado:
        return None
    partes = str(hash_guardado).strip().split("$")
    if len(partes) != 4:
        return None
    algoritmo, iteraciones_texto, sal_texto, esperado_texto = partes
    if algoritmo != _ALGORITMO:
        return None
    try:
        iteraciones = int(iteraciones_texto)
    except (TypeError, ValueError):
        return None
    if not (
        _ITERACIONES_MINIMAS <= iteraciones <= _ITERACIONES_MAXIMAS
    ):
        return None
    try:
        sal = _b64_decodificar(sal_texto)
        esperado = _b64_decodificar(esperado_texto)
    except (ValueError, TypeError):
        return None
    if len(sal) < 8 or len(esperado) != _HASH_BYTES:
        return None
    return iteraciones, sal, esperado


def _hash_configurado() -> Optional[str]:
    return config("ADMIN_PASSWORD_HASH")


def admin_configurada() -> bool:
    """Indica si hay un hash de contraseña válido configurado."""
    return _parsear_hash(_hash_configurado()) is not None


def generar_hash_password(
    password: str, *, iteraciones: int = _ITERACIONES
) -> str:
    """Genera un hash PBKDF2-HMAC-SHA256 con sal aleatoria.

    No se usa para validar en producción; sólo para crear el secreto inicial.
    """
    if not isinstance(password, str) or not password:
        raise ValueError("La contraseña no puede estar vacía.")
    if iteraciones < _ITERACIONES_MINIMAS:
        raise ValueError("El número de iteraciones es demasiado bajo.")
    sal = secrets.token_bytes(_SALT_BYTES)
    derivada = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        sal,
        iteraciones,
        dklen=_HASH_BYTES,
    )
    return (
        f"{_ALGORITMO}${iteraciones}$"
        f"{_b64_codificar(sal)}${_b64_codificar(derivada)}"
    )


def verificar_password(password: str, hash_guardado: Optional[str]) -> bool:
    """Valida una contraseña con comparación de tiempo constante."""
    if not password or not hash_guardado:
        return False
    parseado = _parsear_hash(hash_guardado)
    if parseado is None:
        return False
    iteraciones, sal, esperado = parseado
    try:
        calculado = hashlib.pbkdf2_hmac(
            "sha256",
            str(password).encode("utf-8"),
            sal,
            iteraciones,
            dklen=len(esperado),
        )
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(calculado, esperado)


def es_admin() -> bool:
    """Devuelve True sólo si la sesión actual inició sesión como administradora."""
    try:
        return st.session_state.get(SESSION_KEY) is True
    except Exception:
        return False


def _segundos_bloqueo() -> int:
    try:
        hasta = float(st.session_state.get(_BLOQUEO_KEY, 0) or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, int(round(hasta - time.time())))


def iniciar_sesion(password: str) -> tuple[bool, str]:
    """Intenta autenticar la sesión actual.

    Devuelve ``(exito, mensaje)``. El mensaje está pensado para la interfaz y
    nunca incluye la contraseña ni el hash.
    """
    if not admin_configurada():
        return False, "El acceso administrativo no está configurado."

    restante = _segundos_bloqueo()
    if restante > 0:
        return False, f"Demasiados intentos. Esperá {restante} segundos."

    if verificar_password(password, _hash_configurado()):
        st.session_state[SESSION_KEY] = True
        st.session_state[_INTENTOS_KEY] = 0
        st.session_state.pop(_BLOQUEO_KEY, None)
        return True, ""

    try:
        intentos = int(st.session_state.get(_INTENTOS_KEY, 0)) + 1
    except (TypeError, ValueError):
        intentos = 1
    st.session_state[_INTENTOS_KEY] = intentos
    if intentos >= _MAX_INTENTOS:
        st.session_state[_BLOQUEO_KEY] = (
            time.time() + _BLOQUEO_SEGUNDOS
        )
        st.session_state[_INTENTOS_KEY] = 0
        return False, "Demasiados intentos. Esperá un minuto."
    return False, "Contraseña incorrecta."


def cerrar_sesion() -> None:
    """Cierra la sesión administrativa y limpia el estado de login."""
    for clave in (
        SESSION_KEY,
        _LOGIN_ABIERTO_KEY,
        _INTENTOS_KEY,
        _BLOQUEO_KEY,
        "agora_admin_password",
    ):
        st.session_state.pop(clave, None)


def exigir_admin() -> None:
    """Autorización del lado de Python para operaciones de escritura.

    No alcanza con ocultar botones en la interfaz: los servicios que modifican
    datos llaman a esta función antes de abrir SQLite.
    """
    if not es_admin():
        raise PermissionError(
            "Esta operación requiere una sesión de administradora."
        )


def _cli() -> None:
    from getpass import getpass

    password = getpass("Contraseña de administradora: ")
    repetida = getpass("Repetir contraseña: ")
    if password != repetida:
        raise SystemExit("Las contraseñas no coinciden.")
    if len(password) < 12:
        raise SystemExit("Usá una contraseña de al menos 12 caracteres.")
    print(generar_hash_password(password))


if __name__ == "__main__":  # pragma: no cover - utilidad de terminal
    _cli()
