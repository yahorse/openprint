"""Discover OPP printers on the local network."""

from openprint import Client


def main() -> None:
    client = Client()
    print("Scanning for printers (3 seconds)...")
    printers = client.discover(timeout=3.0)

    if not printers:
        print("No printers found.")
        return

    print(f"Found {len(printers)} printer(s):\n")
    for p in printers:
        color = "color" if p["color"] else "mono"
        duplex = "duplex" if p["duplex"] else "simplex"
        print(f"  {p['name']}")
        print(f"    {p['host']}:{p['port']} — {color}, {duplex}")
        print()


if __name__ == "__main__":
    main()
