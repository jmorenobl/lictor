"""Lictor: implementa escenarios Gherkin con un agente y los verifica de forma determinista."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("lictor")
except PackageNotFoundError:  # a source tree that was never installed
    __version__ = "0+desconocida"
