from __future__ import annotations

import asyncio
import logging
import socket
import subprocess
from typing import Any

import httpx

logger = logging.getLogger("openprint.resilience")


async def wake_on_lan(mac_address: str, broadcast: str = "255.255.255.255") -> None:
    """Send a Wake-on-LAN magic packet to wake a sleeping printer."""
    mac_bytes = bytes.fromhex(mac_address.replace(":", "").replace("-", ""))
    magic = b"\xff" * 6 + mac_bytes * 16

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        await asyncio.to_thread(sock.sendto, magic, (broadcast, 9))
        logger.info("WoL packet sent to %s", mac_address)
    finally:
        sock.close()


async def ping_host(host: str, timeout: float = 2.0) -> bool:
    """Check if a host is reachable."""
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["ping", "-c", "1", "-W", str(int(timeout)), host],
            capture_output=True,
        )
        return result.returncode == 0
    except Exception:
        return False


async def check_ipp_alive(host: str, port: int = 631, timeout: float = 3.0) -> bool:
    """Check if an IPP endpoint is responding."""
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
            resp = await client.get(f"http://{host}:{port}/")
            return resp.status_code < 500
    except Exception:
        return False


async def wait_for_printer(
    host: str,
    port: int = 631,
    mac_address: str | None = None,
    max_wait: float = 30.0,
    poll_interval: float = 2.0,
) -> bool:
    """Wait for a printer to become available, optionally sending WoL first.

    Returns True if the printer came online within max_wait seconds.
    """
    if mac_address:
        await wake_on_lan(mac_address)
        await asyncio.sleep(1.0)

    elapsed = 0.0
    while elapsed < max_wait:
        if await check_ipp_alive(host, port):
            logger.info("Printer at %s:%d is online (waited %.1fs)", host, port, elapsed)
            return True
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

    logger.warning("Printer at %s:%d did not come online within %.0fs", host, port, max_wait)
    return False


async def resolve_mdns_host(hostname: str) -> str | None:
    """Resolve an mDNS hostname to an IP address."""
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["avahi-resolve", "-n", hostname],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split()
            if len(parts) >= 2:
                return parts[1]
    except Exception:
        pass

    try:
        result = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
        if result:
            return result[0][4][0]
    except Exception:
        pass

    return None


class RetryPrinter:
    """Wraps a print backend with retry logic for sleeping printers.

    Instead of failing immediately when a printer is unavailable,
    this tries to wake it up and retries the operation.
    """

    def __init__(
        self,
        backend: Any,
        host: str,
        port: int = 631,
        mac_address: str | None = None,
        max_retries: int = 3,
        retry_delay: float = 5.0,
        wake_timeout: float = 20.0,
    ) -> None:
        self._backend = backend
        self._host = host
        self._port = port
        self._mac = mac_address
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._wake_timeout = wake_timeout

    async def print_with_retry(self, job: Any, pdf_data: bytes) -> None:
        """Try to print, waking the printer if needed."""
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                alive = await check_ipp_alive(self._host, self._port, timeout=3.0)
                if not alive:
                    logger.info(
                        "Printer at %s:%d not responding (attempt %d/%d), waking...",
                        self._host, self._port, attempt + 1, self._max_retries + 1,
                    )
                    came_online = await wait_for_printer(
                        self._host, self._port,
                        mac_address=self._mac,
                        max_wait=self._wake_timeout,
                    )
                    if not came_online:
                        raise ConnectionError(
                            f"Printer at {self._host}:{self._port} is not responding"
                        )

                await self._backend.print_job(job, pdf_data)
                return

            except Exception as exc:
                last_error = exc
                if attempt < self._max_retries:
                    delay = self._retry_delay * (attempt + 1)
                    logger.warning(
                        "Print attempt %d failed: %s. Retrying in %.0fs...",
                        attempt + 1, exc, delay,
                    )
                    await asyncio.sleep(delay)

        raise RuntimeError(
            f"Failed to print after {self._max_retries + 1} attempts: {last_error}"
        )


class PrinterHealthMonitor:
    """Periodically checks printer health and updates state.

    Detects printers going offline/coming back and resolves
    IP changes from DHCP by re-resolving mDNS hostnames.
    """

    def __init__(self, check_interval: float = 30.0) -> None:
        self._interval = check_interval
        self._printers: dict[str, dict[str, Any]] = {}
        self._task: asyncio.Task[None] | None = None
        self._on_state_change: Any = None

    def register(
        self,
        printer_id: str,
        host: str,
        port: int = 631,
        hostname: str | None = None,
    ) -> None:
        self._printers[printer_id] = {
            "host": host,
            "port": port,
            "hostname": hostname,
            "online": True,
        }

    def set_callback(self, callback: Any) -> None:
        self._on_state_change = callback

    async def start(self) -> None:
        self._task = asyncio.create_task(self._check_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _check_loop(self) -> None:
        while True:
            for pid, info in list(self._printers.items()):
                try:
                    # Re-resolve mDNS hostname if available (handles DHCP changes)
                    if info.get("hostname"):
                        new_ip = await resolve_mdns_host(info["hostname"])
                        if new_ip and new_ip != info["host"]:
                            logger.info(
                                "Printer %s IP changed: %s -> %s",
                                pid, info["host"], new_ip,
                            )
                            info["host"] = new_ip

                    alive = await check_ipp_alive(info["host"], info["port"])
                    was_online = info["online"]
                    info["online"] = alive

                    if alive != was_online and self._on_state_change:
                        state = "idle" if alive else "offline"
                        await self._on_state_change(pid, state)

                except Exception as exc:
                    logger.warning("Health check failed for %s: %s", pid, exc)

            await asyncio.sleep(self._interval)
