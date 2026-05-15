"""postino config_gen — emits canonical postfix + dovecot config artifacts."""

from postino_core.config_gen.generator import generate
from postino_core.config_gen.input import GenInput, GenResult, RenderResult

__all__ = ["GenInput", "GenResult", "RenderResult", "generate"]
