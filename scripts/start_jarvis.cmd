@echo off
REM Double-clickable entry point for JARVIS. Starts Ollama (if down), the API
REM server and the Electron HUD, checking each one is actually answering before
REM moving on. See start_jarvis.ps1 for what each step does and why.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_jarvis.ps1"
