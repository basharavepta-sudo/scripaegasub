#!/usr/bin/env python3
"""
Warmup script for Ollama - run once to load model into memory.
After warmup, subsequent requests will be much faster (2-5 seconds instead of 30-40).

Usage:
    python warmup_ollama.py

Or double-click warmup_ollama.bat on Windows.
"""

import json
import os
import sys
import time

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

# Load config
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def check_ollama_status(url):
    """Check if Ollama is running."""
    try:
        resp = requests.get(f"{url}/api/tags", timeout=5)
        return resp.status_code == 200
    except:
        return False

def get_loaded_models(url):
    """Get list of currently loaded models."""
    try:
        resp = requests.get(f"{url}/api/ps", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return [m.get('name', '') for m in data.get('models', [])]
    except:
        pass
    return []

def warmup_model(url, model):
    """Send a simple request to load model into memory."""
    print(f"  Loading {model}...")

    payload = {
        "model": model,
        "prompt": "Hi",
        "stream": False,
        "keep_alive": "30m",
        "options": {
            "num_predict": 1
        }
    }

    start = time.time()
    try:
        resp = requests.post(f"{url}/api/generate", json=payload, timeout=120)
        elapsed = time.time() - start

        if resp.status_code == 200:
            print(f"  OK! Model loaded in {elapsed:.1f}s")
            return True
        else:
            print(f"  ERROR: {resp.status_code}")
            return False
    except requests.exceptions.Timeout:
        print("  TIMEOUT (model may still be loading)")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        return False

def main():
    config = load_config()
    ollama_url = config.get("ollama_url", "http://localhost:11434")
    model = config.get("model", "llama3")

    print("=" * 50)
    print("  OLLAMA WARMUP - AI Subtitle Assistant")
    print("=" * 50)
    print()
    print(f"  Server: {ollama_url}")
    print(f"  Model:  {model}")
    print()

    # Check if Ollama is running
    print("[1/3] Checking Ollama status...")
    if not check_ollama_status(ollama_url):
        print("  ERROR: Ollama not running!")
        print()
        print("  Start Ollama first:")
        print("    Windows: Run 'Ollama' from Start menu")
        print("    Linux/Mac: Run 'ollama serve' in terminal")
        print()
        input("Press Enter to exit...")
        sys.exit(1)
    print("  OK! Ollama is running")
    print()

    # Check if model already loaded
    print("[2/3] Checking loaded models...")
    loaded = get_loaded_models(ollama_url)
    if loaded:
        print(f"  Currently loaded: {', '.join(loaded)}")
        if any(model in m for m in loaded):
            print(f"  {model} already loaded!")
            print()
            print("=" * 50)
            print("  READY! Model is warm, go use the script!")
            print("=" * 50)
            print()
            input("Press Enter to exit...")
            return
    else:
        print("  No models loaded yet")
    print()

    # Warmup
    print("[3/3] Warming up model (this may take 20-60 seconds first time)...")
    if warmup_model(ollama_url, model):
        print()
        print("=" * 50)
        print("  SUCCESS! Model is now loaded and warm.")
        print("  Subsequent requests will be fast (2-5 sec).")
        print("  Model stays in memory for 30 minutes.")
        print("=" * 50)
    else:
        print()
        print("  Warmup failed. Check if model is installed:")
        print(f"    ollama pull {model}")

    print()
    input("Press Enter to exit...")

if __name__ == "__main__":
    main()
