@echo off
rem Aktualizacja DualSense bridge z GitHuba (git pull + nowe biblioteki). To samo robi przycisk "Aktualizuj" w oknie.
cd /d "%~dp0"
python receiver\rx_update.py
pause