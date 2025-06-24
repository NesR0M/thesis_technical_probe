#!/bin/bash

# Wechsle ins Projektverzeichnis
cd /home/morsen/thesis

# Git Pull für Updates
echo "[Updater] Versuche git pull..."
git pull

# Python-Code starten (z. B. in venv)
echo "[Starter] Starte Python-Skript..."
source venv/bin/activate
python3 probe.py
