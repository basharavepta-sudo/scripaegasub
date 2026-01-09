# AI Subtitle Assistant for Aegisub

This tool integrates Google Gemini AI into Aegisub to help you translate, edit, and improve subtitles.

## Features

- Context-aware editing: Sends surrounding lines to AI for better context.
- Dual-language support: Can read an English source file (.txt or .srt) to guide translation.
- Interactive Feedback: Ask AI to "make it shorter", "more formal", etc.
- multiple variants: Choose from 3 different AI suggestions.

## Installation

1.  **Install Python**: Make sure you have Python installed (3.8+ recommended).
    *   Ensure `python` is in your system PATH and refers to Python 3.
    *   **Note**: On some systems (like Linux/macOS), the command `python` might refer to Python 2. In that case, you may need to alias `python` to `python3` or modify the Lua script to use `python3`.

2.  **Install Dependencies**:
    Open a terminal/command prompt and run:
    ```bash
    pip install -r requirements.txt
    ```
    (You might need `pip3` depending on your system).

3.  **Copy Files**:
    Copy the contents of the `automation` folder into your Aegisub automation directory.
    *   **Windows**: Usually `%APPDATA%\Aegisub\automation\autoload\` or `C:\Program Files\Aegisub\automation\autoload\`
    *   **macOS**: `~/Library/Application Support/Aegisub/automation/autoload/`
    *   **Linux**: `~/.aegisub/automation/autoload/` or `/usr/share/aegisub/automation/autoload/`

    You should have a structure like:
    ```
    .../autoload/
        ai_subtitle.lua
        gemini_backend.py
        config.json
        modules/
            json.lua
    ```

4.  **Get API Key**:
    *   Go to [Google AI Studio](https://aistudio.google.com/app/apikey).
    *   Create an API key.

5.  **Configure**:
    *   Open `config.json` in the automation folder.
    *   Paste your API key into `"gemini_api_key"`.
    *   (Optional) Change model or temperature settings.

## Usage

1.  Open Aegisub and load your subtitles.
2.  (Optional) If you have an English translation file, name it the same as your subtitle file (e.g., `episode1.ass` -> `episode1.txt` or `episode1.srt`) and place it in the same folder.
3.  Select a line you want to edit.
4.  Go to **Automation** -> **AI Subtitle Assistant**.
5.  Choose context size and whether to use the source file.
6.  Wait for AI suggestions and choose the best one!

## Troubleshooting

-   **"Error: no response from AI"**: Check if Python is installed and `google-generativeai` is installed. Check `config.json` for valid API key.
-   **JSON Error**: Ensure you didn't break `config.json` syntax.
