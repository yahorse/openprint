"""Run an OPP print server."""

from openprint import Server


def main() -> None:
    server = Server(
        name="My Printer",
        port=631,
        color=True,
        duplex=True,
        supported_media=["a4", "letter", "legal"],
    )
    print(f"Starting OpenPrint server: {server.config.name}")
    print(f"Listening on {server.config.host}:{server.config.port}")
    server.run()


if __name__ == "__main__":
    main()
