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
from typing import Dict, List, Any, Optional

# Setup logging - reduce verbosity for faster startup
logging.basicConfig(
    level=logging.WARNING,
    format='%(levelname)s: %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# Lazy imports for faster startup
requests = None
REQUESTS_ERROR = None
transformers = None
torch = None
TRANSFORMERS_ERROR = None

def ensure_requests():
    """Lazy load requests module."""
    global requests, REQUESTS_ERROR
    if requests is not None:
        return True
    try:
        import requests as req_module
        requests = req_module
        return True
    except ImportError as e:
        REQUESTS_ERROR = str(e)
        return False

def ensure_transformers():
    """Lazy load transformers/torch modules."""
    global transformers, torch, TRANSFORMERS_ERROR
    if transformers is not None:
        return True
    try:
        import torch as torch_module
        from transformers import AutoModelForCausalLM, AutoTokenizer
        torch = torch_module
        transformers = True
        return True
    except ImportError as e:
        TRANSFORMERS_ERROR = str(e)
        return False


def check_ollama_status(ollama_url: str = "http://localhost:11434") -> bool:
    """Quick check if Ollama is running."""
    if not ensure_requests():
        return False
    try:
        resp = requests.get(f"{ollama_url}/api/tags", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


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

    en_text = current_line.get('en', '') or ''
    ru_text = current_line.get('ru', '') or ''
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
            lines.append(f"Пред. строка: {ctx['ru']}")

    # Current line
    lines.append(f"\n[АНГЛИЙСКИЙ]: {en_text}")
    if ru_text and ru_text != en_text:
        lines.append(f"[ТЕКУЩИЙ РУССКИЙ]: {ru_text}")

    # Instructions
    if default_instructions:
        lines.append(f"\nУказания: {default_instructions}")

    if feedback:
        lines.append(f"Доп. требования: {feedback}")

    # Request variants with clear format
    lines.append(f"\nДай {num_variants} вариант(а) локализации.")
    lines.append("Формат ответа - ТОЛЬКО варианты, каждый с новой строки:")
    lines.append("1. **вариант перевода**")
    lines.append("2. **вариант перевода**")
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
    if not ensure_requests():
        raise ImportError(f"requests not installed: pip install requests. Error: {REQUESTS_ERROR}")

    ollama_url = config.get("ollama_url", "http://localhost:11434")
    model = config.get("model", "llama3")
    temperature = config.get("temperature", 0.7)

    api_url = f"{ollama_url}/api/generate"

    # Получаем max_tokens из конфига или используем значение по умолчанию
    max_tokens = config.get("max_tokens", 600)

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,  # Configurable token limit
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


def generate_with_transformers(prompt: str, config: Dict[str, Any], model_cache: Optional[Dict] = None) -> str:
    """Generate response using local transformers model."""
    if model_cache is None:
        model_cache = {}

    if not ensure_transformers():
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

    max_tokens = config.get("max_tokens", 600)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_tokens,
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

    for line in lines:
        line = line.strip()
        if not line:
            continue

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

        # Clean up Aegisub line break commands that might interfere
        # Replace \N with space, preserve the text
        cleaned = cleaned.replace('\\N', ' ').replace('\\n', ' ')
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

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
                cleaned = match.replace('\\N', ' ').replace('\\n', ' ')
                cleaned = re.sub(r'\s+', ' ', cleaned).strip()
                variants.append(cleaned)

        if not variants:
            russian_match = re.search(r'[а-яА-ЯёЁ][а-яА-ЯёЁ\s\.\,\!\?\-\'\"]*[а-яА-ЯёЁ]', response_text)
            if russian_match:
                variants.append(russian_match.group().strip())

    # Fallback
    if not variants:
        if original_ru:
            variants = [original_ru]
        elif response_text.strip():
            cleaned = response_text.strip().replace('\\N', ' ').replace('\\n', ' ')
            variants = [re.sub(r'\s+', ' ', cleaned).strip()]
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


def generate_scene_description(text: str, context: str, config: Dict[str, Any]) -> str:
    """Generate a visual scene description for image generation."""
    prompt = f"""Based on this subtitle dialogue, create a brief visual scene description for an image generator.
Dialogue: "{text}"
Context: {context if context else 'Movie/TV show scene'}

Describe the scene in 1-2 sentences in English, focusing on:
- Setting/environment
- Character actions/emotions
- Lighting/mood

Keep it concise and visual. Output ONLY the scene description, nothing else."""

    try:
        if config.get("backend") == "ollama":
            return generate_with_ollama(prompt, {**config, "max_tokens": 150})
        else:
            return generate_with_transformers(prompt, {**config, "max_tokens": 150})
    except Exception as e:
        logger.error(f"Scene description error: {e}")
        return f"Scene from dialogue: {text[:50]}"


def generate_image(scene_description: str, config: Dict[str, Any]) -> Optional[str]:
    """
    Generate an image based on scene description.
    Returns path to generated image or None.

    Supports:
    - ComfyUI API (localhost:8188)
    - Automatic1111 API (localhost:7860)
    - Ollama with multimodal models (experimental)
    """
    if not ensure_requests():
        logger.error("requests module required for image generation")
        return None

    image_model = config.get("image_model", "sdxl")

    # Try ComfyUI first
    comfyui_url = config.get("comfyui_url", "http://localhost:8188")
    try:
        # Simple ComfyUI API workflow
        workflow = {
            "prompt": {
                "3": {
                    "class_type": "KSampler",
                    "inputs": {
                        "seed": -1,
                        "steps": 20,
                        "cfg": 7,
                        "sampler_name": "euler",
                        "scheduler": "normal",
                        "denoise": 1,
                        "model": ["4", 0],
                        "positive": ["6", 0],
                        "negative": ["7", 0],
                        "latent_image": ["5", 0]
                    }
                },
                "4": {
                    "class_type": "CheckpointLoaderSimple",
                    "inputs": {"ckpt_name": f"{image_model}.safetensors"}
                },
                "5": {
                    "class_type": "EmptyLatentImage",
                    "inputs": {"width": 512, "height": 512, "batch_size": 1}
                },
                "6": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {
                        "text": f"cinematic film still, {scene_description}, high quality, detailed",
                        "clip": ["4", 1]
                    }
                },
                "7": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {
                        "text": "low quality, blurry, text, watermark",
                        "clip": ["4", 1]
                    }
                },
                "8": {
                    "class_type": "VAEDecode",
                    "inputs": {"samples": ["3", 0], "vae": ["4", 2]}
                },
                "9": {
                    "class_type": "SaveImage",
                    "inputs": {"filename_prefix": "subtitle_scene", "images": ["8", 0]}
                }
            }
        }

        resp = requests.post(f"{comfyui_url}/prompt", json=workflow, timeout=120)
        if resp.status_code == 200:
            result = resp.json()
            prompt_id = result.get("prompt_id")
            if prompt_id:
                # Wait for completion and get image
                import time
                for _ in range(60):  # Max 60 seconds
                    time.sleep(1)
                    history_resp = requests.get(f"{comfyui_url}/history/{prompt_id}")
                    if history_resp.status_code == 200:
                        history = history_resp.json()
                        if prompt_id in history:
                            outputs = history[prompt_id].get("outputs", {})
                            if "9" in outputs and outputs["9"].get("images"):
                                image_data = outputs["9"]["images"][0]
                                filename = image_data.get("filename")
                                subfolder = image_data.get("subfolder", "")
                                return f"{comfyui_url}/view?filename={filename}&subfolder={subfolder}"
                return None
    except requests.exceptions.ConnectionError:
        logger.info("ComfyUI not available, trying Automatic1111...")
    except Exception as e:
        logger.error(f"ComfyUI error: {e}")

    # Try Automatic1111 API
    a1111_url = config.get("a1111_url", "http://localhost:7860")
    try:
        payload = {
            "prompt": f"cinematic film still, {scene_description}, high quality, detailed",
            "negative_prompt": "low quality, blurry, text, watermark",
            "steps": 20,
            "width": 512,
            "height": 512,
            "cfg_scale": 7
        }

        resp = requests.post(f"{a1111_url}/sdapi/v1/txt2img", json=payload, timeout=120)
        if resp.status_code == 200:
            result = resp.json()
            images = result.get("images", [])
            if images:
                # Save base64 image to temp file
                import base64
                import tempfile
                image_data = base64.b64decode(images[0])
                temp_path = os.path.join(tempfile.gettempdir(), "subtitle_scene.png")
                with open(temp_path, "wb") as f:
                    f.write(image_data)
                return temp_path
    except requests.exceptions.ConnectionError:
        logger.info("Automatic1111 not available")
    except Exception as e:
        logger.error(f"Automatic1111 error: {e}")

    return None


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
            original_en = current_line.get("en", "")

            prompt = generate_translation_prompt(request_data, config)

            if backend_type == "ollama":
                response_text = generate_with_ollama(prompt, config)
            else:
                response_text = generate_with_transformers(prompt, config)

            variants = parse_variants(response_text, num_variants, original_ru)

            output_data = {"variants": variants}

            # Generate image if enabled
            if config.get("enable_images", False):
                try:
                    text_for_scene = original_en if original_en else original_ru
                    global_context = config.get("global_context", "")

                    scene_desc = generate_scene_description(text_for_scene, global_context, config)
                    image_path = generate_image(scene_desc, config)

                    if image_path:
                        output_data["image_path"] = image_path
                        output_data["scene_description"] = scene_desc
                except Exception as e:
                    logger.error(f"Image generation failed: {e}")
                    output_data["image_error"] = str(e)

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
