import json
import os
import sys
import google.generativeai as genai
from typing import Dict, List, Optional

def load_config(config_path: str) -> Dict:
    """Loads configuration from json file."""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def generate_prompt(data: Dict) -> str:
    """Generates the prompt for Gemini."""
    current_line = data.get("current_line", {})
    context_before = data.get("context_before", [])
    context_after = data.get("context_after", [])
    feedback = data.get("feedback", "")

    prompt = "You are an expert subtitle editor. "
    prompt += "Your task is to improve the translation or styling of a subtitle line.\n\n"

    prompt += "Context (lines before):\n"
    for item in context_before:
        prompt += f"- RU: {item.get('ru', '')} | EN: {item.get('en', '')}\n"

    prompt += "\nCURRENT LINE TO EDIT:\n"
    prompt += f"RU: {current_line.get('ru', '')}\n"
    prompt += f"EN: {current_line.get('en', '')}\n"
    prompt += f"Duration: {current_line.get('duration', 0)} seconds\n"

    prompt += "\nContext (lines after):\n"
    for item in context_after:
        prompt += f"- RU: {item.get('ru', '')} | EN: {item.get('en', '')}\n"

    if feedback:
        prompt += f"\nUser Feedback/Instruction: {feedback}\n"

    prompt += "\nPlease provide 3 different variants of the translation/edit for the current line.\n"
    prompt += "Consider the duration and context. The subtitles should be natural and fit the timing.\n"
    prompt += "Return ONLY a raw JSON array of strings, e.g. [\"Variant 1\", \"Variant 2\", \"Variant 3\"].\n"
    prompt += "Do not include markdown formatting like ```json ... ```."

    return prompt

def main():
    # Expecting arguments: request_file response_file config_file
    if len(sys.argv) < 3:
        print("Usage: python gemini_backend.py <request_file> <response_file> [config_file]")
        sys.exit(1)

    request_file = sys.argv[1]
    response_file = sys.argv[2]

    # Determine config path
    if len(sys.argv) >= 4:
        config_path = sys.argv[3]
    else:
        # Default to config.json in the same directory as this script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, "config.json")

    try:
        # Load config
        config = load_config(config_path)
        api_key = config.get("gemini_api_key")
        model_name = config.get("model", "gemini-2.0-flash-exp")
        temperature = config.get("default_temperature", 0.7)
        if os.environ.get("GEMINI_API_KEY"):
             api_key = os.environ.get("GEMINI_API_KEY")

        if not api_key or api_key == "YOUR_API_KEY_HERE":
            raise ValueError("API Key not configured in config.json or environment variables.")

        # Configure Gemini
        genai.configure(api_key=api_key)

        # Read request
        if not os.path.exists(request_file):
            raise FileNotFoundError(f"Request file not found: {request_file}")

        with open(request_file, 'r', encoding='utf-8') as f:
            request_data = json.load(f)

        # Generate prompt
        prompt = generate_prompt(request_data)

        # Call API
        model = genai.GenerativeModel(model_name)

        # Check if feedback implies a retry with higher creativity
        if request_data.get("feedback"):
            temperature = config.get("retry_temperature", 0.9)

        generation_config = genai.types.GenerationConfig(
            temperature=temperature
        )

        response = model.generate_content(prompt, generation_config=generation_config)

        # Parse response
        response_text = response.text.strip()

        # Clean up markdown if present (e.g. ```json ... ```)
        if response_text.startswith("```"):
            lines = response_text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].startswith("```"):
                lines = lines[:-1]
            response_text = "\n".join(lines).strip()

        try:
            variants = json.loads(response_text)
            if not isinstance(variants, list):
                # If it's not a list, maybe it's a dict or single string. Wrap or extract.
                if isinstance(variants, dict) and "variants" in variants:
                    variants = variants["variants"]
                else:
                    variants = [response_text]
        except json.JSONDecodeError:
            # Fallback if not valid JSON, just return raw text as one variant
            variants = [response_text]

        output_data = {"variants": variants}

    except Exception as e:
        output_data = {"error": str(e)}

    # Write response
    with open(response_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
