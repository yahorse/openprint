"""OpenPrint Protocol — driverless HTTP/REST printing."""

__version__ = "0.1.0"

from openprint.client import Client
from openprint.server import Server

__all__ = ["Client", "Server"]
