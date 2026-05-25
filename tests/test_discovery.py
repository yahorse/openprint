from openprint.discovery import SERVICE_TYPE, PrinterAdvertiser, PrinterScanner


def test_service_type():
    assert SERVICE_TYPE == "_opp._tcp.local."


def test_advertiser_init():
    adv = PrinterAdvertiser(name="Test", port=631)
    assert adv.name == "Test"
    assert adv.port == 631


def test_scanner_init():
    scanner = PrinterScanner()
    assert scanner._found == []
