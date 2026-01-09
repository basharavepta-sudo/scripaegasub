#!/usr/bin/env python3
"""
ALMA Backend for AI Subtitle Assistant.
Uses ALMA or compatible models for translation via Ollama or transformers.
Optimized for speed and flexibility.
"""

import json
import os
import sys
import logging
import re
from typing import Dict, List, Any

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# Try importing requests (for Ollama API)
requests = None
REQUESTS_ERROR = None

try:
    import requests
except ImportError as e:
    REQUESTS_ERROR = str(e)

# Try importing transformers (optional, for local model)
transformers = None
torch = None
TRANSFORMERS_ERROR = None

try:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    transformers = True
except ImportError as e:
    TRANSFORMERS_ERROR = str(e)


def load_config(config_path: str) -> Dict[str, Any]:
    """Loads configuration from json file."""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    if not isinstance(config, dict):
        raise ValueError("Config must be a JSON object")

    return config


# Style presets for translation
STYLE_PRESETS = {
    "natural": "естественно и разговорно, адаптируя под русскую речь",
    "formal": "формально и официально",
    "casual": "неформально, с разговорными выражениями и молодёжным сленгом",
    "literal": "буквально, близко к оригиналу"
}

# Base localization instructions (always included)
LOCALIZATION_PROMPT = """Ты профессиональный локализатор субтитров с английского на русский.
Важно: это НЕ просто перевод, а ЛОКАЛИЗАЦИЯ для русскоязычной аудитории.
- Английский сленг, идиомы, культурные отсылки адаптируй под понятные русским аналоги
- Используй живой русский язык, не кальки с английского
- Сохраняй эмоциональный окрас и интонацию оригинала
- Учитывай длительность субтитра (текст должен успеть прочитаться)
"""


def generate_translation_prompt(data: Dict[str, Any], config: Dict[str, Any]) -> str:
    """Generates optimized prompt for translation."""
    current_line = data.get("current_line", {})
    context_before = data.get("context_before", [])
    context_after = data.get("context_after", [])
    feedback = data.get("feedback", "")

    # Get config values
    num_variants = config.get("num_variants", 3)
    global_context = config.get("global_context", "")
    default_instructions = config.get("default_instructions", "")
    style = config.get("translation_style", "natural")
    style_desc = STYLE_PRESETS.get(style, STYLE_PRESETS["natural"])

    # Sanitize inputs
    def sanitize(text):
        if not text: return ""
        return str(text).replace("\\N", " [br] ")

    en_text = sanitize(current_line.get('en', '') or '')
    ru_text = sanitize(current_line.get('ru', '') or '')
    duration = current_line.get('duration', 0)

    # Build prompt with localization focus
    lines = [LOCALIZATION_PROMPT.strip()]

    # Global context (if set)
    if global_context:
        lines.append(f"\nО проекте: {global_context}")

    # Style
    lines.append(f"Стиль: {style_desc}.")

    # Duration constraint
    if duration > 0:
        max_chars = int(duration * 15)  # ~15 chars per second readable
        lines.append(f"Длительность: {duration:.1f}с (максимум ~{max_chars} символов).")

    # Context lines (compact)
    if context_before:
        ctx = context_before[-1]  # Only last line for speed
        if ctx.get('ru'):
            ctx_ru = sanitize(ctx['ru'])
            lines.append(f"Пред. строка: {ctx_ru}")

    # Current line
    lines.append(f"\n[АНГЛИЙСКИЙ]: {en_text}")
    if ru_text and ru_text != en_text:
        lines.append(f"[ТЕКУЩИЙ РУССКИЙ]: {ru_text}")

    # Instructions
    lines.append("\nВАЖНО: Ипользуй токен ' [br] ' для переноса строки вместо \\N.")

    if default_instructions:
        lines.append(f"Указания: {default_instructions}")

    if feedback:
        lines.append(f"Доп. требования: {feedback}")

    # Request variants with clear format
    lines.append(f"\nДай {num_variants} вариант(а) локализации.")
    lines.append("Формат ответа - ТОЛЬКО варианты, каждый с новой строки:")
    lines.append("1. **вариант перевода**")
    lines.append("2. **вариант с [br] переносом**")
    if num_variants >= 3:
        lines.append("3. **вариант перевода**")

    return "\n".join(lines)


def generate_batch_prompt(lines_data: List[Dict], config: Dict[str, Any]) -> str:
    """Generates prompt for batch translation (multiple lines at once)."""
    num_variants = config.get("num_variants", 3)
    global_context = config.get("global_context", "")
    style = config.get("translation_style", "natural")
    style_desc = STYLE_PRESETS.get(style, STYLE_PRESETS["natural"])

    prompt_lines = ["Переведи субтитры с английского на русский."]

    if global_context:
        prompt_lines.append(f"Контекст: {global_context}")

    prompt_lines.append(f"Стиль: {style_desc}.")
    prompt_lines.append(f"\nСтроки для перевода:")

    for i, line_data in enumerate(lines_data, 1):
        en = line_data.get("en", "")
        prompt_lines.append(f"{i}. {en}")

    prompt_lines.append(f"\nДля каждой строки дай {num_variants} вариант(а/ов).")
    prompt_lines.append("Формат:\n1.1. перевод\n1.2. перевод\n2.1. перевод\n...")

    return "\n".join(prompt_lines)


def generate_with_ollama(prompt: str, config: Dict[str, Any]) -> str:
    """Generate response using Ollama API."""
    if requests is None:
        raise ImportError(f"requests not installed: pip install requests. Error: {REQUESTS_ERROR}")

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
            "num_predict": 300,  # Reduced for speed
            "top_p": 0.9,
            "repeat_penalty": 1.1
        }
    }

    logger.info(f"Calling Ollama: {model}")

    try:
        response = requests.post(api_url, json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        return result.get("response", "")
    except requests.exceptions.ConnectionError:
        raise ConnectionError(f"Ollama не запущен ({ollama_url}). Запустите: ollama serve")
    except requests.exceptions.Timeout:
        raise TimeoutError("Таймаут запроса к Ollama")
    except Exception as e:
        raise RuntimeError(f"Ошибка Ollama: {e}")


def generate_with_transformers(prompt: str, config: Dict[str, Any], model_cache: Dict = {}) -> str:
    """Generate response using local transformers model."""
    if not transformers:
        raise ImportError(f"transformers/torch not installed. Error: {TRANSFORMERS_ERROR}")

    model_name = config.get("model", "haoranxu/ALMA-13B-R")
    temperature = config.get("temperature", 0.7)
    device = config.get("device", "cuda" if torch.cuda.is_available() else "cpu")

    if "model" not in model_cache:
        logger.info(f"Loading {model_name}...")
        model_cache["tokenizer"] = AutoTokenizer.from_pretrained(model_name)
        model_cache["model"] = AutoModelForCausalLM.from_pretrained(
            model_name,
            torch_dtype=torch.float16 if device == "cuda" else torch.float32,
            device_map="auto" if device == "cuda" else None
        )
        if device == "cpu":
            model_cache["model"] = model_cache["model"].to(device)

    tokenizer = model_cache["tokenizer"]
    model = model_cache["model"]

    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=150,
            temperature=temperature,
            do_sample=True,
            top_p=0.9,
            pad_token_id=tokenizer.eos_token_id
        )

    response = tokenizer.decode(outputs[0], skip_special_tokens=True)
    if response.startswith(prompt):
        response = response[len(prompt):].strip()

    return response


def parse_variants(response_text: str, num_variants: int = 3, original_ru: str = "") -> List[str]:
    """Parse translation variants from response."""
    variants = []
    raw_lines = response_text.strip().split('\n')
    merged_lines = []

    # Merge lines that are split by \N
    current_line = ""
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue

        # Check if we should merge with previous line
        # Heuristic: if previous line ends with \N, it's a hard line break within the subtitle,
        # so the next line of text belongs to the same subtitle variant.
        should_merge = False
        if current_line:
            if current_line.endswith(r'\N') or current_line.endswith(r'\N"') or current_line.endswith(r"\N'"):
                should_merge = True

        if should_merge:
            current_line += line
        else:
            if current_line:
                merged_lines.append(current_line)
            current_line = line

    if current_line:
        merged_lines.append(current_line)

    for line in merged_lines:
        # Remove numbering (1., 1), 1:, 1.1., etc.)
        cleaned = re.sub(r'^[\d]+[\.\)\:]\s*', '', line)

        # Extract text from **bold** markers if present
        bold_match = re.search(r'\*\*(.+?)\*\*', cleaned)
        if bold_match:
            cleaned = bold_match.group(1)
        else:
            cleaned = cleaned.strip()

        # Remove any remaining ** markers
        cleaned = cleaned.replace('**', '')

        # Clean whitespace
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        # Restore [br] -> \N
        cleaned = re.sub(r'\s*\[br\]\s*', r'\\N', cleaned, flags=re.IGNORECASE)

        # Must contain Cyrillic and be substantial
        if cleaned and len(cleaned) > 2:
            if re.search(r'[а-яА-ЯёЁ]', cleaned):
                variants.append(cleaned)

    # Try to extract Russian from response if no variants found
    if not variants:
        # Also handle **bold** in full text
        bold_matches = re.findall(r'\*\*([^*]+)\*\*', response_text)
        for match in bold_matches:
            if re.search(r'[а-яА-ЯёЁ]', match):
                cleaned = re.sub(r'\s+', ' ', match).strip()
                cleaned = re.sub(r'\s*\[br\]\s*', r'\\N', cleaned, flags=re.IGNORECASE)
                variants.append(cleaned)

        if not variants:
            # More complex regex to catch Russian text that might include [br]
            # \w matches letters, numbers, underscore. [\[\]] matches brackets.
            russian_match = re.search(r'[а-яА-ЯёЁ][а-яА-ЯёЁ\s\.\,\!\?\-\'\"\[\]brBR]*[а-яА-ЯёЁ]', response_text)
            if russian_match:
                cleaned = russian_match.group().strip()
                cleaned = re.sub(r'\s*\[br\]\s*', r'\\N', cleaned, flags=re.IGNORECASE)
                variants.append(cleaned)

    # Fallback
    if not variants:
        if original_ru:
            variants = [original_ru]
        elif response_text.strip():
            cleaned = response_text.strip()
            cleaned = re.sub(r'\s+', ' ', cleaned).strip()
            cleaned = re.sub(r'\s*\[br\]\s*', r'\\N', cleaned, flags=re.IGNORECASE)
            variants = [cleaned]
        else:
            variants = ["[Не удалось получить перевод]"]

    # Deduplicate and limit
    seen = set()
    unique = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            unique.append(v)
            if len(unique) >= num_variants:
                break

    return unique


def parse_batch_variants(response_text: str, num_lines: int, num_variants: int = 3) -> List[List[str]]:
    """Parse batch translation response."""
    results = [[] for _ in range(num_lines)]

    # Try to parse numbered format like 1.1., 1.2., 2.1., etc.
    pattern = r'(\d+)\.(\d+)\.\s*(.+?)(?=\d+\.\d+\.|$)'
    matches = re.findall(pattern, response_text, re.DOTALL)

    for line_num, var_num, text in matches:
        line_idx = int(line_num) - 1
        if 0 <= line_idx < num_lines:
            text = text.strip()
            if text and re.search(r'[а-яА-ЯёЁ]', text):
                if len(results[line_idx]) < num_variants:
                    results[line_idx].append(text)

    # Fallback: just split by lines
    if all(len(r) == 0 for r in results):
        lines = [l.strip() for l in response_text.split('\n') if l.strip()]
        russian_lines = [l for l in lines if re.search(r'[а-яА-ЯёЁ]', l)]

        for i, line in enumerate(russian_lines[:num_lines * num_variants]):
            line_idx = i // num_variants
            if line_idx < num_lines and len(results[line_idx]) < num_variants:
                # Remove numbering
                cleaned = re.sub(r'^[\d]+[\.\)\:]\s*', '', line)
                results[line_idx].append(cleaned.strip())

    return results


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
        logger.info(f"Loading config: {config_path}")
        config = load_config(config_path)

        backend_type = config.get("backend", "ollama")
        num_variants = min(max(config.get("num_variants", 3), 1), 5)  # Clamp 1-5

        logger.info(f"Reading request: {request_file}")
        if not os.path.exists(request_file):
            raise FileNotFoundError(f"Request file not found: {request_file}")

        with open(request_file, 'r', encoding='utf-8') as f:
            request_data = json.load(f)

        # Check for retry with feedback
        if request_data.get("feedback"):
            config["temperature"] = config.get("retry_temperature", 0.9)

        # Check for batch mode
        batch_lines = request_data.get("batch_lines", [])

        if batch_lines:
            # Batch mode
            logger.info(f"Batch mode: {len(batch_lines)} lines")
            prompt = generate_batch_prompt(batch_lines, config)

            if backend_type == "ollama":
                response_text = generate_with_ollama(prompt, config)
            else:
                response_text = generate_with_transformers(prompt, config)

            batch_results = parse_batch_variants(response_text, len(batch_lines), num_variants)
            output_data = {"batch_variants": batch_results}

        else:
            # Single line mode
            current_line = request_data.get("current_line", {})
            original_ru = current_line.get("ru", "")

            prompt = generate_translation_prompt(request_data, config)
            logger.debug(f"Prompt: {prompt}")

            if backend_type == "ollama":
                response_text = generate_with_ollama(prompt, config)
            else:
                response_text = generate_with_transformers(prompt, config)

            logger.info(f"Response: {len(response_text)} chars")

            variants = parse_variants(response_text, num_variants, original_ru)
            logger.info(f"Parsed {len(variants)} variants")

            output_data = {"variants": variants}

    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        output_data = {"error": str(e)}
    except json.JSONDecodeError as e:
        logger.error(f"JSON error: {e}")
        output_data = {"error": f"Invalid JSON: {e}"}
    except ImportError as e:
        logger.error(f"Import error: {e}")
        output_data = {"error": str(e)}
    except ConnectionError as e:
        logger.error(f"Connection error: {e}")
        output_data = {"error": str(e)}
    except TimeoutError as e:
        logger.error(f"Timeout: {e}")
        output_data = {"error": str(e)}
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        output_data = {"error": f"Ошибка: {str(e)}"}

    logger.info(f"Writing response: {response_file}")
    try:
        with open(response_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Write error: {e}")
        try:
            with open(response_file, 'w', encoding='utf-8') as f:
                json.dump({"error": f"Write failed: {e}"}, f)
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
