from __future__ import annotations

import asyncio
import socket
from typing import Any

from zeroconf import ServiceInfo, ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf

SERVICE_TYPE = "_opp._tcp.local."


class PrinterAdvertiser:
    """Advertises an OPP printer via mDNS/DNS-SD."""

    def __init__(
        self,
        name: str,
        port: int,
        color: bool = True,
        duplex: bool = True,
    ) -> None:
        self.name = name
        self.port = port
        self._zeroconf: AsyncZeroconf | None = None
        self._info = ServiceInfo(
            SERVICE_TYPE,
            f"{name}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(self._get_local_ip())],
            port=port,
            properties={
                "v": "1",
                "name": name,
                "color": str(color).lower(),
                "duplex": str(duplex).lower(),
                "pdf": "2.0",
            },
        )

    async def start(self) -> None:
        self._zeroconf = AsyncZeroconf()
        await self._zeroconf.async_register_service(self._info)

    async def stop(self) -> None:
        if self._zeroconf:
            await self._zeroconf.async_unregister_service(self._info)
            await self._zeroconf.async_close()

    @staticmethod
    def _get_local_ip() -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return "127.0.0.1"


class PrinterScanner:
    """Discovers OPP printers on the local network."""

    def __init__(self) -> None:
        self._found: list[dict[str, Any]] = []

    async def scan(self, timeout: float = 3.0) -> list[dict[str, Any]]:
        self._found = []
        zc = AsyncZeroconf()
        browser = AsyncServiceBrowser(
            zc.zeroconf, SERVICE_TYPE, handlers=[self._on_change]
        )
        await asyncio.sleep(timeout)
        browser.cancel()
        await zc.async_close()
        return self._found

    def _on_change(
        self,
        zeroconf: Zeroconf,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        if state_change is ServiceStateChange.Added:
            info = zeroconf.get_service_info(service_type, name)
            if info:
                addresses = [
                    socket.inet_ntoa(addr) for addr in info.addresses
                ]
                props = {
                    k.decode(): v.decode()
                    for k, v in info.properties.items()
                }
                self._found.append(
                    {
                        "name": props.get("name", name),
                        "host": addresses[0] if addresses else "unknown",
                        "port": info.port,
                        "color": props.get("color") == "true",
                        "duplex": props.get("duplex") == "true",
                    }
                )
