from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Awaitable, Callable
from typing import Any

from zeroconf import ServiceBrowser, ServiceInfo, ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncZeroconf

from openprint.backends.cups import CUPSBackend

logger = logging.getLogger("openprint.scanner")

IPP_SERVICE_TYPES = ["_ipp._tcp.local.", "_ipps._tcp.local."]
OPP_SERVICE_TYPE = "_opp._tcp.local."


class NetworkPrinterScanner:
    """Continuously watches the network for IPP/IPP-S printers via mDNS.

    Calls on_found/on_lost callbacks when printers appear or disappear,
    enabling the bridge to hot-add printers without restarts.
    """

    def __init__(
        self,
        on_found: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_lost: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._on_found = on_found
        self._on_lost = on_lost
        self._zeroconf: AsyncZeroconf | None = None
        self._browsers: list[ServiceBrowser] = []
        self._known: dict[str, dict[str, Any]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._zeroconf = AsyncZeroconf()
        for stype in IPP_SERVICE_TYPES:
            browser = ServiceBrowser(
                self._zeroconf.zeroconf,
                stype,
                handlers=[self._on_state_change],
            )
            self._browsers.append(browser)
        logger.info("Network scanner started, watching for IPP printers")

    async def stop(self) -> None:
        for browser in self._browsers:
            browser.cancel()
        if self._zeroconf:
            await self._zeroconf.async_close()
        self._browsers.clear()

    @property
    def known_printers(self) -> dict[str, dict[str, Any]]:
        return dict(self._known)

    def _on_state_change(
        self,
        zeroconf: Zeroconf,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        if state_change is ServiceStateChange.Added:
            info = zeroconf.get_service_info(service_type, name)
            if info:
                printer = self._parse_service_info(info, service_type)
                if printer:
                    self._known[printer["id"]] = printer
                    logger.info("Discovered printer: %s at %s:%d",
                                printer["name"], printer["host"], printer["port"])
                    if self._on_found and self._loop:
                        asyncio.run_coroutine_threadsafe(
                            self._on_found(printer), self._loop
                        )

        elif state_change is ServiceStateChange.Removed:
            printer_id = self._name_to_id(name)
            if printer_id in self._known:
                logger.info("Printer removed: %s", printer_id)
                del self._known[printer_id]
                if self._on_lost and self._loop:
                    asyncio.run_coroutine_threadsafe(
                        self._on_lost(printer_id), self._loop
                    )

    def _parse_service_info(
        self, info: ServiceInfo, service_type: str
    ) -> dict[str, Any] | None:
        addresses = [socket.inet_ntoa(addr) for addr in info.addresses]
        if not addresses:
            return None

        host = addresses[0]
        port = info.port or 631
        tls = "ipps" in service_type

        props: dict[str, str] = {}
        if info.properties:
            for k, v in info.properties.items():
                key = k.decode() if isinstance(k, bytes) else k
                val = v.decode() if isinstance(v, bytes) else str(v)
                props[key] = val

        name = props.get("ty") or props.get("product") or info.name.split(".")[0]
        name = name.strip("()")

        printer_id = f"ipp_{host.replace('.', '_')}_{port}"

        return {
            "id": printer_id,
            "name": name,
            "host": host,
            "port": port,
            "tls": tls,
            "uri": f"{'ipps' if tls else 'ipp'}://{host}:{port}/ipp/print",
            "properties": props,
            "color": props.get("Color", "T").upper() == "T",
            "duplex": props.get("Duplex", "T").upper() == "T",
            "pdf": "application/pdf" in props.get("pdl", "application/pdf"),
        }

    @staticmethod
    def _name_to_id(name: str) -> str:
        clean = name.split(".")[0].replace(" ", "_")
        return f"ipp_{clean}"


class CUPSWatcher:
    """Periodically polls CUPS for new or removed printers."""

    def __init__(
        self,
        interval: float = 10.0,
        on_found: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_lost: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._interval = interval
        self._on_found = on_found
        self._on_lost = on_lost
        self._known: set[str] = set()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("CUPS watcher started (interval: %.0fs)", self._interval)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _poll_loop(self) -> None:
        while True:
            try:
                printers = await CUPSBackend.list_printers()
                current_names = {p["name"] for p in printers}

                new_printers = current_names - self._known
                for p in printers:
                    if p["name"] in new_printers:
                        logger.info("CUPS: new printer detected: %s", p["name"])
                        if self._on_found:
                            await self._on_found(p)

                removed = self._known - current_names
                for name in removed:
                    logger.info("CUPS: printer removed: %s", name)
                    if self._on_lost:
                        await self._on_lost(name)

                self._known = current_names
            except Exception as exc:
                logger.warning("CUPS poll error: %s", exc)

            await asyncio.sleep(self._interval)
