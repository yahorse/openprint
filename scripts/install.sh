#!/usr/bin/env bash
set -euo pipefail

echo "=== OpenPrint Bridge Installer ==="
echo

# Check for CUPS
if ! command -v lpstat &>/dev/null; then
    echo "CUPS is not installed. Install it first:"
    echo "  sudo apt install cups        # Debian/Ubuntu"
    echo "  sudo dnf install cups        # Fedora"
    echo "  sudo pacman -S cups          # Arch"
    exit 1
fi

# Install openprint
echo "[1/4] Installing openprint..."
pip install openprint 2>/dev/null || pip install --user openprint

# Create service user
echo "[2/4] Creating service user..."
if ! id openprint &>/dev/null; then
    sudo useradd -r -s /usr/sbin/nologin -d /home/openprint -m openprint
    sudo usermod -aG lpadmin openprint
fi
sudo mkdir -p /home/openprint/.openprint
sudo chown -R openprint:openprint /home/openprint/.openprint

# Install systemd service
echo "[3/4] Installing systemd service..."
sudo cp "$(dirname "$0")/../systemd/openprint-bridge.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable openprint-bridge

# Start service
echo "[4/4] Starting OpenPrint Bridge..."
sudo systemctl start openprint-bridge

echo
echo "Done! OpenPrint Bridge is running."
echo
echo "  Dashboard:  http://$(hostname -I | awk '{print $1}'):631"
echo "  Status:     sudo systemctl status openprint-bridge"
echo "  Logs:       sudo journalctl -u openprint-bridge -f"
echo
echo "All CUPS printers are now available via OPP."
echo "New printers will be detected automatically."
