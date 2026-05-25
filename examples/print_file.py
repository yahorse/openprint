"""Print a PDF file to the first discovered OPP printer."""

import sys

from openprint import Client


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python print_file.py <file.pdf>")
        sys.exit(1)

    file_path = sys.argv[1]
    client = Client()

    print("Discovering printers...")
    printers = client.discover()

    if not printers:
        print("No printers found.")
        sys.exit(1)

    printer = printers[0]
    print(f"Printing to: {printer['name']} ({printer['host']}:{printer['port']})")

    job = client.print(file_path)
    print(f"Job submitted: {job['id']} — status: {job['status']}")


if __name__ == "__main__":
    main()
