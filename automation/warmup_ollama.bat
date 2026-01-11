@echo off
REM Warmup script for Ollama - double-click to run
REM Keeps the model loaded in memory for faster subsequent requests

cd /d "%~dp0"
python warmup_ollama.py
