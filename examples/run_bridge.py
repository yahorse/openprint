"""Run the OPP-CUPS bridge.

Discovers all printers configured in CUPS and exposes them
via the OpenPrint Protocol. Any OPP client can then discover
and print to your existing printers without drivers.

    python examples/run_bridge.py

Then from any device on the network:

    curl http://<bridge-ip>:631/opp/v1/printers
    curl -X POST http://<bridge-ip>:631/opp/v1/jobs \
        -F "file=@document.pdf" \
        -F "printer=HP_LaserJet"
"""

from openprint import Bridge


def main() -> None:
    bridge = Bridge(
        name="OpenPrint Bridge",
        port=631,
    )
    print("Starting OpenPrint-CUPS Bridge")
    print("All CUPS printers will be available via OPP")
    print()
    bridge.run()


if __name__ == "__main__":
    main()
