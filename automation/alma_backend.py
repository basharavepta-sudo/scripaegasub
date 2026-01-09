#!/usr/bin/env python3
"""
ALMA Backend for AI Subtitle Assistant.
Uses ALMA-13B-R or compatible models for translation via Hugging Face transformers or Ollama.
"""

import json
import os
import sys
import logging
import re
from typing import Dict, List, Optional, Any

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# Try importing transformers (for local model)
transformers = None
torch = None
TRANSFORMERS_ERROR = None

try:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    transformers = True
except ImportError as e:
    TRANSFORMERS_ERROR = str(e)

# Try importing requests (for Ollama API)
requests = None
REQUESTS_ERROR = None

try:
    import requests
except ImportError as e:
    REQUESTS_ERROR = str(e)


def load_config(config_path: str) -> Dict[str, Any]:
    """Loads configuration from json file."""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    if not isinstance(config, dict):
        raise ValueError("Config must be a JSON object")

    return config


def generate_translation_prompt(data: Dict[str, Any], for_ollama: bool = False) -> str:
    """Generates the prompt for ALMA model."""
    current_line = data.get("current_line", {})
    context_before = data.get("context_before", [])
    context_after = data.get("context_after", [])
    feedback = data.get("feedback", "")

    en_text = current_line.get('en', '') or ''
    ru_text = current_line.get('ru', '') or ''
    duration = current_line.get('duration', 0)

    # Build context info
    context_info = []

    if context_before:
        context_info.append("Previous lines:")
        for item in context_before[-2:]:  # Last 2 lines for context
            if item.get('en'):
                context_info.append(f"  EN: {item['en']}")
            if item.get('ru'):
                context_info.append(f"  RU: {item['ru']}")

    if context_after:
        context_info.append("Next lines:")
        for item in context_after[:2]:  # Next 2 lines
            if item.get('en'):
                context_info.append(f"  EN: {item['en']}")
            if item.get('ru'):
                context_info.append(f"  RU: {item['ru']}")

    context_str = "\n".join(context_info) if context_info else ""

    # Different prompt format for Ollama vs direct ALMA
    if for_ollama:
        prompt = f"""You are a professional subtitle translator. Translate the following English subtitle to Russian.
Keep it natural, concise (fits {duration:.1f}s duration), and contextually appropriate.

{context_str}

Current line to translate:
English: {en_text}
{f"Current Russian (to improve): {ru_text}" if ru_text else ""}
{f"User instruction: {feedback}" if feedback else ""}

Provide exactly 3 different Russian translation variants, one per line.
Format: just the translations, numbered 1. 2. 3."""
    else:
        # ALMA-style prompt
        prompt = f"Translate this from English to Russian:\nEnglish: {en_text}\nRussian:"

    return prompt


def generate_with_ollama(prompt: str, config: Dict[str, Any]) -> str:
    """Generate response using Ollama API."""
    if requests is None:
        raise ImportError(f"requests package not installed. Install with: pip install requests. Error: {REQUESTS_ERROR}")

    ollama_url = config.get("ollama_url", "http://localhost:11434")
    model = config.get("model", "llama3")
    temperature = config.get("temperature", 0.7)

    api_url = f"{ollama_url}/api/generate"

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": 500
        }
    }

    logger.info(f"Calling Ollama API at {api_url} with model {model}")

    try:
        response = requests.post(api_url, json=payload, timeout=120)
        response.raise_for_status()
        result = response.json()
        return result.get("response", "")
    except requests.exceptions.ConnectionError:
        raise ConnectionError(f"Cannot connect to Ollama at {ollama_url}. Make sure Ollama is running.")
    except requests.exceptions.Timeout:
        raise TimeoutError("Ollama request timed out")
    except Exception as e:
        raise RuntimeError(f"Ollama API error: {e}")


def generate_with_transformers(prompt: str, config: Dict[str, Any], model_cache: Dict = {}) -> str:
    """Generate response using local transformers model."""
    if not transformers:
        raise ImportError(f"transformers/torch not installed. Install with: pip install transformers torch. Error: {TRANSFORMERS_ERROR}")

    model_name = config.get("model", "haoranxu/ALMA-13B-R")
    temperature = config.get("temperature", 0.7)
    device = config.get("device", "cuda" if torch.cuda.is_available() else "cpu")

    # Cache model and tokenizer
    if "model" not in model_cache:
        logger.info(f"Loading model {model_name} on {device}...")
        model_cache["tokenizer"] = AutoTokenizer.from_pretrained(model_name)
        model_cache["model"] = AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype=torch.float16 if device == "cuda" else torch.float32,
            device_map="auto" if device == "cuda" else None
        )
        if device == "cpu":
            model_cache["model"] = model_cache["model"].to(device)
        logger.info("Model loaded successfully")

    tokenizer = model_cache["tokenizer"]
    model = model_cache["model"]

    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=200,
            temperature=temperature,
            do_sample=True,
            top_p=0.9,
            num_return_sequences=1,
            pad_token_id=tokenizer.eos_token_id
        )

    response = tokenizer.decode(outputs[0], skip_special_tokens=True)
    # Remove the prompt from response
    if response.startswith(prompt):
        response = response[len(prompt):].strip()

    return response


def parse_variants(response_text: str, original_ru: str = "") -> List[str]:
    """Parse translation variants from response."""
    variants = []
    lines = response_text.strip().split('\n')

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Remove numbering like "1.", "1)", "1:", etc.
        cleaned = re.sub(r'^[\d]+[\.\)\:]\s*', '', line)
        cleaned = cleaned.strip()

        # Skip if it's just English or too short
        if cleaned and len(cleaned) > 2:
            # Check if it contains Cyrillic (Russian)
            if re.search(r'[а-яА-ЯёЁ]', cleaned):
                variants.append(cleaned)

    # If we couldn't parse variants with Cyrillic, try to extract Russian text
    if not variants:
        # Try to extract Russian text from anywhere in response
        russian_match = re.search(r'[а-яА-ЯёЁ][а-яА-ЯёЁ\s\.\,\!\?\-]*[а-яА-ЯёЁ]', response_text)
        if russian_match:
            variants.append(russian_match.group().strip())

    # If still no variants with Cyrillic, use original_ru as fallback
    if not variants:
        if original_ru:
            variants = [original_ru]
        elif response_text.strip():
            # Last resort: use response as is (might be English)
            variants = [response_text.strip()]
        else:
            variants = ["[Не удалось получить перевод]"]

    # Limit to 3 variants and ensure uniqueness
    seen = set()
    unique_variants = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            unique_variants.append(v)
            if len(unique_variants) >= 3:
                break

    return unique_variants


def main():
    """Main entry point."""
    if len(sys.argv) < 3:
        print("Usage: python alma_backend.py <request_file> <response_file> [config_file]", file=sys.stderr)
        sys.exit(1)

    request_file = sys.argv[1]
    response_file = sys.argv[2]

    if len(sys.argv) >= 4:
        config_path = sys.argv[3]
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, "config.json")

    output_data: Dict[str, Any] = {}

    try:
        # Load config
        logger.info(f"Loading config from: {config_path}")
        config = load_config(config_path)

        backend_type = config.get("backend", "ollama")  # "ollama" or "transformers"

        # Read request
        logger.info(f"Reading request from: {request_file}")
        if not os.path.exists(request_file):
            raise FileNotFoundError(f"Request file not found: {request_file}")

        with open(request_file, 'r', encoding='utf-8') as f:
            request_data = json.load(f)

        # Check if feedback implies retry with higher creativity
        if request_data.get("feedback"):
            config["temperature"] = config.get("retry_temperature", 0.9)

        current_line = request_data.get("current_line", {})
        original_ru = current_line.get("ru", "")

        # Generate based on backend type
        if backend_type == "ollama":
            prompt = generate_translation_prompt(request_data, for_ollama=True)
            logger.info("Using Ollama backend")
            response_text = generate_with_ollama(prompt, config)
        else:
            prompt = generate_translation_prompt(request_data, for_ollama=False)
            logger.info("Using transformers backend")
            response_text = generate_with_transformers(prompt, config)

        logger.info(f"Received response ({len(response_text)} chars)")
        logger.debug(f"Raw response: {response_text}")

        # Parse variants
        variants = parse_variants(response_text, original_ru)

        # If we only got one variant from ALMA-style translation, generate more
        if len(variants) < 3 and backend_type == "transformers":
            # Generate additional variants with different temperatures
            for temp in [0.8, 0.95]:
                if len(variants) >= 3:
                    break
                config["temperature"] = temp
                additional = generate_with_transformers(prompt, config)
                for v in parse_variants(additional, ""):
                    if v not in variants:
                        variants.append(v)
                        if len(variants) >= 3:
                            break

        logger.info(f"Parsed {len(variants)} variants")
        output_data = {"variants": variants}

    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        output_data = {"error": str(e)}
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        output_data = {"error": f"Invalid JSON in request file: {e}"}
    except ImportError as e:
        logger.error(f"Import error: {e}")
        output_data = {"error": str(e)}
    except ConnectionError as e:
        logger.error(f"Connection error: {e}")
        output_data = {"error": str(e)}
    except ValueError as e:
        logger.error(f"Value error: {e}")
        output_data = {"error": str(e)}
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        output_data = {"error": f"Unexpected error: {str(e)}"}

    # Write response
    logger.info(f"Writing response to: {response_file}")
    try:
        with open(response_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to write response file: {e}")
        try:
            with open(response_file, 'w', encoding='utf-8') as f:
                json.dump({"error": f"Failed to write response: {e}"}, f)
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
