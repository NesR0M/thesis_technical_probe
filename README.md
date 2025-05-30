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
pip install RPi.GPIO
pip install sdnotify
```

---

## 🔐 Set API Keys

Create a `.env` file in the project directory and add your API keys:

```env
OPENAI_API_KEY=your_key_here
ELEVENLABS_API_KEY=your_key_here
```

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
WatchdogSec=30s
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

| Aktion                        | Befehl                                           |
|------------------------------|--------------------------------------------------|
| Service starten              | `sudo systemctl start probe.service`            |
| Service stoppen              | `sudo systemctl stop probe.service`             |
| Service beim Boot aktivieren| `sudo systemctl enable probe.service`           |
| Service beim Boot deaktivieren| `sudo systemctl disable probe.service`        |
| Status anzeigen              | `sudo systemctl status probe.service`           |
| Logs live anzeigen           | `journalctl -u probe.service -f`                |

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
