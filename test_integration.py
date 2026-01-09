#!/usr/bin/env python3
"""
Integration tests for AI Subtitle Assistant backend.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Add automation dir to path using path relative to this file
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTOMATION_DIR = os.path.join(SCRIPT_DIR, 'automation')
sys.path.insert(0, AUTOMATION_DIR)

import gemini_backend

# Check if google.generativeai is available
GENAI_AVAILABLE = gemini_backend.genai is not None


class TestGeminiBackend(unittest.TestCase):
    """Tests for gemini_backend module."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.request_file = os.path.join(self.temp_dir, "test_request.json")
        self.response_file = os.path.join(self.temp_dir, "test_response.json")
        self.config_file = os.path.join(self.temp_dir, "test_config.json")

        # Create dummy config
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump({
                "gemini_api_key": "TEST_KEY",
                "model": "test-model",
                "default_temperature": 0.7,
                "retry_temperature": 0.9
            }, f)

        # Create dummy request
        self.default_request = {
            "current_line": {"ru": "Привет", "en": "Hello", "duration": 1.5},
            "context_before": [],
            "context_after": [],
            "feedback": ""
        }
        with open(self.request_file, 'w', encoding='utf-8') as f:
            json.dump(self.default_request, f)

    def tearDown(self):
        """Clean up test files."""
        for filepath in [self.request_file, self.response_file, self.config_file]:
            if os.path.exists(filepath):
                os.remove(filepath)
        if os.path.exists(self.temp_dir):
            os.rmdir(self.temp_dir)

    def _run_with_mock_genai(self, response_text):
        """Helper to run main() with mocked genai module."""
        # Create mock objects
        mock_genai = MagicMock()
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.text = response_text

        mock_model.generate_content.return_value = mock_response
        mock_genai.GenerativeModel.return_value = mock_model
        mock_genai.types.GenerationConfig.return_value = MagicMock()

        # Temporarily replace genai in the module
        original_genai = gemini_backend.genai
        gemini_backend.genai = mock_genai

        try:
            with patch.object(sys, 'argv', ['gemini_backend.py', self.request_file, self.response_file, self.config_file]):
                gemini_backend.main()
        finally:
            gemini_backend.genai = original_genai

        # Read and return result
        with open(self.response_file, 'r', encoding='utf-8') as f:
            return json.load(f), mock_genai

    def test_backend_success_json_array(self):
        """Test successful response with JSON array."""
        result, mock_genai = self._run_with_mock_genai('["Привет!", "Здравствуйте!", "Приветствую!"]')

        self.assertIn("variants", result)
        self.assertEqual(len(result["variants"]), 3)
        self.assertEqual(result["variants"][0], "Привет!")
        mock_genai.configure.assert_called_with(api_key="TEST_KEY")

    def test_backend_markdown_wrapped_response(self):
        """Test response wrapped in markdown code blocks."""
        result, _ = self._run_with_mock_genai('```json\n["Вариант 1", "Вариант 2"]\n```')

        self.assertIn("variants", result)
        self.assertEqual(result["variants"], ["Вариант 1", "Вариант 2"])

    def test_backend_plain_text_response(self):
        """Test fallback for plain text response (not JSON)."""
        result, _ = self._run_with_mock_genai('Just a simple text suggestion.')

        self.assertIn("variants", result)
        self.assertEqual(result["variants"], ["Just a simple text suggestion."])

    def test_backend_dict_with_variants_key(self):
        """Test response as dict with 'variants' key."""
        result, _ = self._run_with_mock_genai('{"variants": ["Один", "Два", "Три"]}')

        self.assertIn("variants", result)
        self.assertEqual(result["variants"], ["Один", "Два", "Три"])

    def test_backend_with_feedback(self):
        """Test that feedback triggers higher temperature."""
        # Update request with feedback
        request_with_feedback = self.default_request.copy()
        request_with_feedback["feedback"] = "Сделай короче"
        with open(self.request_file, 'w', encoding='utf-8') as f:
            json.dump(request_with_feedback, f)

        result, mock_genai = self._run_with_mock_genai('["Короткий вариант"]')

        # Verify generate_content was called
        mock_genai.GenerativeModel.return_value.generate_content.assert_called_once()

        # Response should be valid
        self.assertIn("variants", result)

    def test_backend_with_context(self):
        """Test request with context lines."""
        request_with_context = {
            "current_line": {"ru": "Тест", "en": "Test", "duration": 2.0},
            "context_before": [
                {"ru": "Строка до", "en": "Line before"}
            ],
            "context_after": [
                {"ru": "Строка после", "en": "Line after"}
            ],
            "feedback": ""
        }
        with open(self.request_file, 'w', encoding='utf-8') as f:
            json.dump(request_with_context, f)

        result, mock_genai = self._run_with_mock_genai('["Вариант с контекстом"]')

        # Verify prompt includes context
        call_args = mock_genai.GenerativeModel.return_value.generate_content.call_args
        prompt = call_args[0][0]
        self.assertIn("Line before", prompt)
        self.assertIn("Line after", prompt)

    def test_backend_missing_config(self):
        """Test error handling when config file is missing."""
        os.remove(self.config_file)

        with patch.object(sys, 'argv', ['gemini_backend.py', self.request_file, self.response_file, self.config_file]):
            gemini_backend.main()

        with open(self.response_file, 'r', encoding='utf-8') as f:
            result = json.load(f)

        self.assertIn("error", result)
        self.assertIn("Config file not found", result["error"])

    def test_backend_missing_request(self):
        """Test error handling when request file is missing."""
        os.remove(self.request_file)

        # Mock genai to avoid import error
        original_genai = gemini_backend.genai
        gemini_backend.genai = MagicMock()

        try:
            with patch.object(sys, 'argv', ['gemini_backend.py', self.request_file, self.response_file, self.config_file]):
                gemini_backend.main()
        finally:
            gemini_backend.genai = original_genai

        with open(self.response_file, 'r', encoding='utf-8') as f:
            result = json.load(f)

        self.assertIn("error", result)
        self.assertIn("Request file not found", result["error"])

    def test_backend_invalid_api_key(self):
        """Test error when API key is not configured."""
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump({
                "gemini_api_key": "YOUR_API_KEY_HERE",
                "model": "test-model"
            }, f)

        with patch.object(sys, 'argv', ['gemini_backend.py', self.request_file, self.response_file, self.config_file]):
            gemini_backend.main()

        with open(self.response_file, 'r', encoding='utf-8') as f:
            result = json.load(f)

        self.assertIn("error", result)
        self.assertIn("API Key not configured", result["error"])

    def test_backend_genai_not_installed(self):
        """Test error when genai module is not available."""
        # Temporarily set genai to None
        original_genai = gemini_backend.genai
        original_error = gemini_backend.GENAI_IMPORT_ERROR
        gemini_backend.genai = None
        gemini_backend.GENAI_IMPORT_ERROR = "Test: module not found"

        try:
            with patch.object(sys, 'argv', ['gemini_backend.py', self.request_file, self.response_file, self.config_file]):
                gemini_backend.main()
        finally:
            gemini_backend.genai = original_genai
            gemini_backend.GENAI_IMPORT_ERROR = original_error

        with open(self.response_file, 'r', encoding='utf-8') as f:
            result = json.load(f)

        self.assertIn("error", result)
        self.assertIn("google-generativeai package not installed", result["error"])


class TestPromptGeneration(unittest.TestCase):
    """Tests for prompt generation."""

    def test_generate_prompt_basic(self):
        """Test basic prompt generation."""
        data = {
            "current_line": {"ru": "Привет", "en": "Hello", "duration": 1.0},
            "context_before": [],
            "context_after": [],
            "feedback": ""
        }
        prompt = gemini_backend.generate_prompt(data)

        self.assertIn("Hello", prompt)
        self.assertIn("Привет", prompt)
        self.assertIn("1.0 seconds", prompt)
        self.assertIn("CURRENT LINE TO EDIT", prompt)

    def test_generate_prompt_with_feedback(self):
        """Test prompt includes feedback."""
        data = {
            "current_line": {"ru": "Тест", "en": "Test", "duration": 1.0},
            "context_before": [],
            "context_after": [],
            "feedback": "Сделай более формально"
        }
        prompt = gemini_backend.generate_prompt(data)

        self.assertIn("Сделай более формально", prompt)
        self.assertIn("User Feedback", prompt)

    def test_generate_prompt_with_context(self):
        """Test prompt includes context lines."""
        data = {
            "current_line": {"ru": "Тест", "en": "Test", "duration": 1.0},
            "context_before": [
                {"ru": "Предыдущая", "en": "Previous"}
            ],
            "context_after": [
                {"ru": "Следующая", "en": "Next"}
            ],
            "feedback": ""
        }
        prompt = gemini_backend.generate_prompt(data)

        self.assertIn("Previous", prompt)
        self.assertIn("Next", prompt)
        self.assertIn("Context (lines before)", prompt)
        self.assertIn("Context (lines after)", prompt)

    def test_generate_prompt_empty_source(self):
        """Test prompt with empty English source."""
        data = {
            "current_line": {"ru": "Только русский", "en": "", "duration": 2.0},
            "context_before": [],
            "context_after": [],
            "feedback": ""
        }
        prompt = gemini_backend.generate_prompt(data)

        self.assertIn("Только русский", prompt)
        self.assertIn("[no source]", prompt)


class TestResponseParsing(unittest.TestCase):
    """Tests for response parsing."""

    def test_parse_json_array(self):
        """Test parsing JSON array."""
        text = '["Один", "Два", "Три"]'
        variants = gemini_backend.parse_variants(text)
        self.assertEqual(variants, ["Один", "Два", "Три"])

    def test_parse_markdown_wrapped(self):
        """Test parsing markdown-wrapped JSON."""
        text = '```json\n["Один", "Два"]\n```'
        variants = gemini_backend.parse_variants(text)
        self.assertEqual(variants, ["Один", "Два"])

    def test_parse_markdown_no_language(self):
        """Test parsing markdown without language specifier."""
        text = '```\n["А", "Б"]\n```'
        variants = gemini_backend.parse_variants(text)
        self.assertEqual(variants, ["А", "Б"])

    def test_parse_plain_text(self):
        """Test parsing plain text."""
        text = "Просто текст"
        variants = gemini_backend.parse_variants(text)
        self.assertEqual(variants, ["Просто текст"])

    def test_parse_empty(self):
        """Test parsing empty response."""
        variants = gemini_backend.parse_variants("")
        self.assertEqual(len(variants), 1)
        self.assertIn("Пустой ответ", variants[0])

    def test_parse_whitespace_only(self):
        """Test parsing whitespace-only response."""
        variants = gemini_backend.parse_variants("   \n\t  ")
        self.assertEqual(len(variants), 1)
        self.assertIn("Пустой ответ", variants[0])

    def test_parse_dict_with_variants(self):
        """Test parsing dict with variants key."""
        text = '{"variants": ["А", "Б"]}'
        variants = gemini_backend.parse_variants(text)
        self.assertEqual(variants, ["А", "Б"])

    def test_parse_dict_with_translations(self):
        """Test parsing dict with translations key."""
        text = '{"translations": ["Перевод 1", "Перевод 2"]}'
        variants = gemini_backend.parse_variants(text)
        self.assertEqual(variants, ["Перевод 1", "Перевод 2"])

    def test_parse_mixed_types_in_array(self):
        """Test parsing array with mixed types."""
        text = '["Строка", 123, null, true]'
        variants = gemini_backend.parse_variants(text)
        # Should convert all to strings, skip None
        self.assertEqual(len(variants), 3)
        self.assertEqual(variants[0], "Строка")
        self.assertEqual(variants[1], "123")
        self.assertEqual(variants[2], "True")


class TestCleanResponseText(unittest.TestCase):
    """Tests for clean_response_text function."""

    def test_clean_simple_text(self):
        """Test cleaning simple text."""
        result = gemini_backend.clean_response_text("simple text")
        self.assertEqual(result, "simple text")

    def test_clean_markdown_json(self):
        """Test cleaning markdown with json specifier."""
        text = '```json\n["a", "b"]\n```'
        result = gemini_backend.clean_response_text(text)
        self.assertEqual(result, '["a", "b"]')

    def test_clean_markdown_no_specifier(self):
        """Test cleaning markdown without language specifier."""
        text = '```\ncontent\n```'
        result = gemini_backend.clean_response_text(text)
        self.assertEqual(result, "content")

    def test_clean_with_whitespace(self):
        """Test cleaning text with surrounding whitespace."""
        result = gemini_backend.clean_response_text("  \n text \n  ")
        self.assertEqual(result, "text")


if __name__ == '__main__':
    unittest.main()
