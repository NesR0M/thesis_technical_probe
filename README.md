# thesis_technical_probe setup

This guide describes how to install and run the prototype on a Raspberry Pi.

---

## 🔧 Virtual Environment Setup

```bash
python3 -m venv venv --system-site-packages
source venv/bin/activate
```

---

## 📦 Install Required Python Packages

```bash
pip install openai
pip install elevenlabs
pip install python-dotenv
pip install sdnotify
# Optional, falls nicht schon systemweit installiert:
pip install RPi.GPIO
```

---

## 🔐 Set API Keys

Create a `.env` file in the project directory and add your API keys:

```env
OPENAI_API_KEY=your_key_here
ELEVENLABS_API_KEY=your_key_here
```

---

## 🧾 Logging Setup

### 1. Create Log Directory

```bash
sudo mkdir -p /var/log/probe
```

### 2. Set Ownership to Your User

```bash
sudo chown morsen:morsen /var/log/probe
```

Make sure your Python logging writes to this path (e.g., `/var/log/probe/probe.log`).

---


## ⚙️ Systemd Service Setup

Create a service file:

```bash
sudo nano /etc/systemd/system/probe.service
```

Paste the following content:

```ini
[Unit]
Description=Technology Probe
After=network.target sound.target

[Service]
WorkingDirectory=/home/morsen/thesis
ExecStart=/home/morsen/thesis/venv/bin/python /home/morsen/thesis/probe.py
Environment="PATH=/home/morsen/thesis/venv/bin"
Restart=always
RestartSec=10
WatchdogSec=60s
NotifyAccess=all
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

---

## ▶️ Start the Service

```bash
sudo systemctl daemon-reexec
sudo systemctl enable probe.service
sudo systemctl start probe.service
```

---

## 🛠️ Helpful Commands

| Aktion                         | Befehl                                         |
|-------------------------------|------------------------------------------------|
| Service starten               | `sudo systemctl start probe.service`          |
| Service stoppen               | `sudo systemctl stop probe.service`           |
| Service beim Boot aktivieren | `sudo systemctl enable probe.service`         |
| Service beim Boot deaktivieren| `sudo systemctl disable probe.service`        |
| Status anzeigen               | `sudo systemctl status probe.service`         |
| Logs live anzeigen            | `journalctl -u probe.service -f`              |

---

## 🔁 Update the Service File

After editing the `.service` file, run:

```bash
sudo systemctl daemon-reload
sudo systemctl restart probe.service
```

---

## ☑️ Reminder

Always activate your virtual environment before testing manually:

```bash
source venv/bin/activate
```

---

## 🚀 Optional: Create Start Script

Create a script to auto-update and launch your prototype:

`~/thesis/start_probe.sh`

```bash
#!/bin/bash

# Switch to project directory
cd /home/morsen/thesis

# Pull latest version
echo "[Updater] Versuche git pull..."
/usr/bin/git pull

# Activate virtual environment and start script
echo "[Starter] Starte Python-Skript..."
source venv/bin/activate
python3 probe.py
```

Make it executable:

```bash
chmod +x ~/thesis/start_probe.sh
```

## Optional: Save Wifi connection manually:

```bash
nmcli connection add type wifi con-name "ParticipantWiFi" ifname wlan0 ssid "ParticipantWiFi"
nmcli connection modify "ParticipantWiFi" wifi-sec.key-mgmt wpa-psk wifi-sec.psk "ParticipantPassword"
```

# Optionally make it autoconnect:

```bash
nmcli connection modify "ParticipantWiFi" connection.autoconnect yes
```
✅ This config is saved and will try to connect as soon as the SSID is in range.

