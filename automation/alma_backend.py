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

# Base prompt
BASE_PROMPT = """Ты профессиональный редактор субтитров.
- Отвечай ТОЛЬКО на русском!
- Сохраняй смысл оригинала
"""

# Mode: EN->RU translation
TRANSLATION_MODE = """ЗАДАЧА: Локализация с английского на русский.

ИДИОМЫ И СЛЕНГ - адаптируй правильно:
- "piece of cake" → "раз плюнуть" (НЕ "кусок торта")
- "break a leg" → "ни пуха ни пера" (НЕ "сломай ногу")
- "holy shit" → "ё-моё/блин/чёрт" (по контексту)
- "badass" → "крутой/отмороженный"
- "dude/bro" → "чувак/братан"
- "what the hell" → "какого чёрта"
- "gonna/wanna" → обычные глаголы
- ругательства адаптируй под русские аналоги

ПРАВИЛА:
- Передай точный смысл
- Сохрани эмоции и интонацию
- Используй живой русский язык"""

# Mode: RU editing only (no English)
EDITING_MODE = """ЗАДАЧА: Улучшить русский текст.

ПРАВИЛА:
- Сохрани смысл
- Сделай фразу естественнее
- Исправь неуклюжие обороты
- Не меняй факты, имена, числа"""


def generate_translation_prompt(data: Dict[str, Any], config: Dict[str, Any]) -> str:
    """Generates optimized prompt for translation or editing."""
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

    en_text = current_line.get('en', '') or ''
    ru_text = current_line.get('ru', '') or ''

    # Determine mode: translation (EN->RU) or editing (RU only)
    has_english = bool(en_text and en_text.strip())

    # Build prompt
    lines = [BASE_PROMPT.strip()]

    # Add mode-specific instructions
    if has_english:
        lines.append(TRANSLATION_MODE.strip())
    else:
        lines.append(EDITING_MODE.strip())

    # Style
    lines.append(f"\nСтиль: {style_desc}.")

    # Global context (if set)
    if global_context:
        lines.append(f"О проекте: {global_context}")

    # Context (before/after subtitles)
    if context_before or context_after:
        lines.append("\n[Контекст - только для понимания, НЕ редактируй]")

    if context_before:
        lines.append("ДО:")
        for ctx in context_before[-2:]:
            if ctx.get('ru'):
                lines.append(f"  {ctx['ru']}")

    # Current line
    if has_english:
        lines.append("\n>>> ПЕРЕВЕДИ <<<")
        lines.append(f"EN: {en_text}")
        if ru_text:
            lines.append(f"(текущий RU: {ru_text})")
    else:
        lines.append("\n>>> УЛУЧШИ <<<")
        lines.append(f"RU: {ru_text}")

    if context_after:
        lines.append("\nПОСЛЕ:")
        for ctx in context_after[:2]:
            if ctx.get('ru'):
                lines.append(f"  {ctx['ru']}")

    # Instructions
    if default_instructions:
        lines.append(f"\nУказания: {default_instructions}")
    if feedback:
        lines.append(f"Требования: {feedback}")

    # Request variants
    lines.append(f"\nДай {num_variants} вариант(а).")
    lines.append("Формат: 1. ... 2. ... 3. ...")
    lines.append("Полный текст, без комментариев!")

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
        "context": [],  # Empty context = new chat, no memory from previous requests
        "keep_alive": "30m",  # Keep model in memory for 30 minutes (faster subsequent requests)
        "options": {
            "temperature": temperature,
            "num_predict": 1024,
            "top_p": 0.9,
            "repeat_penalty": 1.1,
            "num_ctx": 2048
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
    lines = response_text.strip().split('\n')

    logger.debug(f"Parsing response: {response_text[:500]}...")

    # Normalize original for comparison (strip and lowercase)
    original_normalized = original_ru.strip().lower() if original_ru else ""

    # First, try to parse numbered variants (1. ... 2. ... 3. ...)
    # Collect multi-line variants - lines until next number
    current_variant = []
    current_num = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Check if this line starts a new numbered variant
        num_match = re.match(r'^(\d+)[\.\)\:]\s*(.*)$', line)
        if num_match:
            # Save previous variant if exists
            if current_variant and current_num > 0:
                full_text = ' '.join(current_variant)
                variants.append(full_text)

            # Start new variant
            current_num = int(num_match.group(1))
            rest = num_match.group(2).strip()
            current_variant = [rest] if rest else []
        elif current_num > 0:
            # Continue current variant (multi-line)
            # Skip meta lines
            skip_patterns = ['формат', 'вариант', 'ответ:', 'перевод:', 'here are', 'translation:']
            if not any(skip in line.lower() for skip in skip_patterns):
                current_variant.append(line)

    # Don't forget last variant
    if current_variant and current_num > 0:
        full_text = ' '.join(current_variant)
        variants.append(full_text)

    # Clean up variants
    cleaned_variants = []
    for var in variants:
        # Remove ** markers
        cleaned = var.replace('**', '')
        # Clean quotes
        cleaned = re.sub(r'^["\']|["\']$', '', cleaned.strip())
        # Replace \N with space
        cleaned = cleaned.replace('\\N', ' ').replace('\\n', ' ')
        # Normalize spaces
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        # Must contain Cyrillic and be substantial
        if cleaned and len(cleaned) > 2 and re.search(r'[а-яА-ЯёЁ]', cleaned):
            # Skip if too similar to original
            cleaned_normalized = cleaned.strip().lower()
            if original_normalized:
                orig_alpha = re.sub(r'[^\w]', '', original_normalized)
                clean_alpha = re.sub(r'[^\w]', '', cleaned_normalized)
                if orig_alpha == clean_alpha:
                    logger.warning(f"Skipping variant too similar to original: {cleaned[:50]}")
                    continue
            cleaned_variants.append(cleaned)

    variants = cleaned_variants

    # Try to extract Russian from response if no variants found
    if not variants:
        # Also handle **bold** in full text
        bold_matches = re.findall(r'\*\*([^*]+)\*\*', response_text)
        for match in bold_matches:
            if re.search(r'[а-яА-ЯёЁ]', match):
                cleaned = match.replace('\\N', ' ').replace('\\n', ' ')
                cleaned = re.sub(r'\s+', ' ', cleaned).strip()
                # Skip if identical to original
                if original_ru and cleaned.strip() == original_ru.strip():
                    continue
                variants.append(cleaned)

        if not variants:
            # Try to find any Russian text in response
            russian_match = re.search(r'[а-яА-ЯёЁ][а-яА-ЯёЁ\s\.\,\!\?\-\'\"]+[а-яА-ЯёЁ]', response_text)
            if russian_match:
                found = russian_match.group().strip()
                # Skip if identical to original
                if not (original_ru and found.strip() == original_ru.strip()):
                    variants.append(found)

    # IMPORTANT: Do NOT fallback to original_ru - that defeats the purpose!
    # If we couldn't parse any variants, return an error message
    if not variants:
        logger.error(f"Failed to parse any variants from response: {response_text[:200]}")
        if response_text.strip():
            # Return the raw response so user can see what AI returned
            cleaned = response_text.strip().replace('\\N', ' ').replace('\\n', ' ')
            cleaned = re.sub(r'\s+', ' ', cleaned).strip()[:200]
            variants = [f"[AI ответ не распознан]: {cleaned}"]
        else:
            variants = ["[AI не вернул ответ - проверьте Ollama]"]

    # Deduplicate and limit
    seen = set()
    unique = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            unique.append(v)
            if len(unique) >= num_variants:
                break

    logger.info(f"Parsed variants: {unique}")
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
            logger.info(f"Raw AI response:\n{response_text[:500]}")

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
