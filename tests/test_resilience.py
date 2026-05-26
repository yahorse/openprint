from unittest.mock import AsyncMock, patch

import pytest

from openprint.resilience import (
    PrinterHealthMonitor,
    RetryPrinter,
)


def test_health_monitor_init():
    monitor = PrinterHealthMonitor(check_interval=15.0)
    assert monitor._interval == 15.0
    assert monitor._printers == {}


def test_health_monitor_register():
    monitor = PrinterHealthMonitor()
    monitor.register("printer1", host="192.168.1.100", port=631)
    assert "printer1" in monitor._printers
    assert monitor._printers["printer1"]["host"] == "192.168.1.100"
    assert monitor._printers["printer1"]["online"] is True


def test_health_monitor_register_with_hostname():
    monitor = PrinterHealthMonitor()
    monitor.register("printer1", host="192.168.1.100", hostname="hp.local")
    assert monitor._printers["printer1"]["hostname"] == "hp.local"


def test_retry_printer_init():
    backend = AsyncMock()
    retry = RetryPrinter(
        backend,
        host="192.168.1.100",
        port=631,
        max_retries=2,
        retry_delay=1.0,
    )
    assert retry._max_retries == 2
    assert retry._host == "192.168.1.100"


@pytest.mark.asyncio
async def test_retry_printer_succeeds_first_try():
    backend = AsyncMock()
    retry = RetryPrinter(backend, host="localhost", port=631, max_retries=2)

    with patch("openprint.resilience.check_ipp_alive", new_callable=AsyncMock, return_value=True):
        await retry.print_with_retry(AsyncMock(), b"pdf data")

    backend.print_job.assert_called_once()


@pytest.mark.asyncio
async def test_retry_printer_wakes_then_succeeds():
    backend = AsyncMock()
    retry = RetryPrinter(
        backend, host="localhost", port=631,
        max_retries=2, retry_delay=0.01, wake_timeout=0.1,
    )

    call_count = 0

    async def check_alive(host, port, timeout=3.0):
        nonlocal call_count
        call_count += 1
        return call_count > 1  # Fail first, then succeed

    with patch("openprint.resilience.check_ipp_alive", side_effect=check_alive):
        with patch(
            "openprint.resilience.wait_for_printer", new_callable=AsyncMock, return_value=True,
        ):
            await retry.print_with_retry(AsyncMock(), b"pdf data")

    backend.print_job.assert_called_once()


@pytest.mark.asyncio
async def test_retry_printer_gives_up():
    backend = AsyncMock()
    backend.print_job.side_effect = ConnectionError("refused")
    retry = RetryPrinter(
        backend, host="localhost", port=631,
        max_retries=1, retry_delay=0.01, wake_timeout=0.1,
    )

    with patch("openprint.resilience.check_ipp_alive", new_callable=AsyncMock, return_value=True):
        with pytest.raises(RuntimeError, match="Failed to print"):
            await retry.print_with_retry(AsyncMock(), b"pdf data")
