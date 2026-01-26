#!/usr/bin/env python3
"""
Gemini Backend for AI Subtitle Assistant.
Processes subtitle editing requests using Google's Gemini AI.
"""

import json
import os
import sys
import logging
from typing import Dict, List, Optional, Any

# Import google.generativeai - will be checked at runtime
genai = None
GENAI_IMPORT_ERROR = None

try:
    import google.generativeai as genai
except ImportError as e:
    GENAI_IMPORT_ERROR = str(e)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)


def load_config(config_path: str) -> Dict[str, Any]:
    """Loads configuration from json file."""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    # Validate required fields
    if not isinstance(config, dict):
        raise ValueError("Config must be a JSON object")

    return config


def generate_prompt(data: Dict[str, Any], num_variants: int = 3) -> str:
    """Generates the prompt for Gemini."""
    current_line = data.get("current_line", {})
    context_before = data.get("context_before", [])
    context_after = data.get("context_after", [])
    feedback = data.get("feedback", "")

    # Helper to sanitize inputs (hide \N from AI)
    def sanitize(text):
        if not text: return ""
        return str(text).replace("\\N", " [br] ")

    prompt_parts = [
        "You are an expert subtitle editor and translator.",
        "Your task is to improve the translation or styling of a subtitle line.",
        "The subtitles are being translated from English to Russian.",
        "IMPORTANT: The token ' [br] ' represents a line break. Use ' [br] ' instead of \\N or newlines.",
        ""
    ]

    # Context before
    if context_before:
        prompt_parts.append("Context (lines before):")
        for item in context_before:
            ru_text = sanitize(item.get('ru', '') or '[empty]')
            en_text = sanitize(item.get('en', '') or '[no source]')
            prompt_parts.append(f"- RU: {ru_text}")
            prompt_parts.append(f"  EN: {en_text}")
        prompt_parts.append("")

    # Current line
    prompt_parts.append("=== CURRENT LINE TO EDIT ===")
    ru_text = sanitize(current_line.get('ru', '') or '[empty]')
    en_text = sanitize(current_line.get('en', '') or '[no source]')
    duration = current_line.get('duration', 0)

    prompt_parts.append(f"Russian (current): {ru_text}")
    prompt_parts.append(f"English (source): {en_text}")
    prompt_parts.append(f"Duration: {duration:.1f} seconds")
    prompt_parts.append("============================")
    prompt_parts.append("")

    # Context after
    if context_after:
        prompt_parts.append("Context (lines after):")
        for item in context_after:
            ru_text = sanitize(item.get('ru', '') or '[empty]')
            en_text = sanitize(item.get('en', '') or '[no source]')
            prompt_parts.append(f"- RU: {ru_text}")
            prompt_parts.append(f"  EN: {en_text}")
        prompt_parts.append("")

    # User feedback
    if feedback:
        prompt_parts.append(f"User Feedback/Instruction: {feedback}")
        prompt_parts.append("")

    # Instructions
    prompt_parts.extend([
        f"Please provide exactly {num_variants} different variants of the Russian translation/edit.",
        "Consider:",
        "- The duration constraint (text should fit the timing)",
        "- Natural Russian language flow",
        "- Context from surrounding lines",
        "- Accuracy to the English source (if provided)",
        "",
        f"Return ONLY a raw JSON array of {num_variants} strings, like this:",
        f'["Вариант 1", ... (total {num_variants} items) ...]',
        "",
        "IMPORTANT RULES:",
        "1. DO NOT use backslashes or \\N. ALWAYS use ' [br] ' for line breaks.",
        "2. Try to preserve the approximate position of line breaks ([br]) to match the original rhythm.",
        "3. Return ONLY the JSON array. No markdown, no explanations.",
        "4. Example with line break: 'First line [br] Second line'"
    ])

    return "\n".join(prompt_parts)


def clean_response_text(text: str) -> str:
    """Clean up response text, removing markdown formatting."""
    text = text.strip()

    # Remove markdown code blocks
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove first line (```json or ```)
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        # Remove last line if it's ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    return text


def parse_variants(response_text: str) -> List[str]:
    """Parse variants from response text."""
    text = clean_response_text(response_text)

    if not text:
        return ["[Пустой ответ от AI]"]

    # Pre-process text to fix common JSON escaping issues with \N
    # Many models output \N directly instead of \\N inside JSON strings
    # We replace \N with \\N, but only if it's not already escaped
    import re
    text = re.sub(r'(?<!\\)\\N', r'\\\\N', text)

    try:
        parsed = json.loads(text)

        # Handle different response formats
        if isinstance(parsed, list):
            variants = parsed
        elif isinstance(parsed, dict):
            # Try common keys
            for key in ["variants", "translations", "options", "results"]:
                if key in parsed and isinstance(parsed[key], list):
                    variants = parsed[key]
                    break
            else:
                # Use string representation as fallback
                variants = [str(parsed)]
        else:
            variants = [str(parsed)]

        # Ensure all variants are strings
        variants = [str(v) for v in variants if v is not None]

        # RESTORE [br] TO \N
        # We asked the AI to use [br], now we put \N back
        restored_variants = []
        for v in variants:
            # Replace [br] (case insensitive) with \N
            # Handle [br], [BR], [Br], etc.
            v_restored = re.sub(r'\s*\[br\]\s*', r'\\N', v, flags=re.IGNORECASE)
            restored_variants.append(v_restored)

        variants = restored_variants

        # Ensure we have at least one variant
        if not variants:
            variants = [text]

        return variants

    except json.JSONDecodeError:
        # Not valid JSON, return as single variant
        logger.warning("Response is not valid JSON, using as single variant")
        return [text]


def main():
    """Main entry point."""
    # Parse arguments
    if len(sys.argv) < 3:
        print("Usage: python gemini_backend.py <request_file> <response_file> [config_file]", file=sys.stderr)
        sys.exit(1)

    request_file = sys.argv[1]
    response_file = sys.argv[2]

    # Determine config path
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

        # Get API key (environment variable takes precedence)
        api_key = os.environ.get("GEMINI_API_KEY") or config.get("gemini_api_key")

        if not api_key or api_key == "YOUR_API_KEY_HERE":
            raise ValueError(
                "API Key not configured. "
                "Set GEMINI_API_KEY environment variable or update config.json. "
                "Get a key at: https://aistudio.google.com/app/apikey"
            )

        model_name = config.get("model", "gemini-2.0-flash-exp")
        default_temperature = config.get("default_temperature", 0.7)
        retry_temperature = config.get("retry_temperature", 0.9)

        # Check if genai is available
        if genai is None:
            raise ImportError(
                f"google-generativeai package not installed. "
                f"Install with: pip install google-generativeai. "
                f"Original error: {GENAI_IMPORT_ERROR}"
            )

        # Configure Gemini
        genai.configure(api_key=api_key)

        # Read request
        logger.info(f"Reading request from: {request_file}")
        if not os.path.exists(request_file):
            raise FileNotFoundError(f"Request file not found: {request_file}")

        with open(request_file, 'r', encoding='utf-8') as f:
            request_data = json.load(f)

        # Generate prompt
        num_variants = min(max(config.get("num_variants", 3), 1), 5)
        prompt = generate_prompt(request_data, num_variants)
        logger.debug(f"Generated prompt:\n{prompt}")

        # Determine temperature
        temperature = default_temperature
        if request_data.get("feedback"):
            temperature = retry_temperature
            logger.info(f"Using retry temperature: {temperature}")

        # Call API
        logger.info(f"Calling Gemini API (model: {model_name}, temp: {temperature})")
        model = genai.GenerativeModel(model_name)

        generation_config = genai.types.GenerationConfig(
            temperature=temperature
        )

        response = model.generate_content(prompt, generation_config=generation_config)

        # Check for empty response
        if not response or not response.text:
            raise ValueError("Empty response from Gemini API")

        response_text = response.text
        logger.info(f"Received response ({len(response_text)} chars)")
        logger.debug(f"Raw response: {response_text}")

        # Parse variants
        variants = parse_variants(response_text)
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
        # Try to write error to response file anyway
        try:
            with open(response_file, 'w', encoding='utf-8') as f:
                json.dump({"error": f"Failed to write response: {e}"}, f)
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
