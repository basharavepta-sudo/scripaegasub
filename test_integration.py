#!/usr/bin/env python3
"""
Integration tests for AI Subtitle Assistant backend (ALMA/Ollama version).
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

import alma_backend


class TestAlmaBackend(unittest.TestCase):
    """Tests for alma_backend module."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.request_file = os.path.join(self.temp_dir, "test_request.json")
        self.response_file = os.path.join(self.temp_dir, "test_response.json")
        self.config_file = os.path.join(self.temp_dir, "test_config.json")

        # Create dummy config for Ollama
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump({
                "backend": "ollama",
                "ollama_url": "http://localhost:11434",
                "model": "llama3",
                "temperature": 0.7,
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

    def _run_with_mock_ollama(self, response_text):
        """Helper to run main() with mocked Ollama API."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": response_text}
        mock_response.raise_for_status = MagicMock()

        with patch.object(alma_backend, 'requests') as mock_requests:
            mock_requests.post.return_value = mock_response
            with patch.object(sys, 'argv', ['alma_backend.py', self.request_file, self.response_file, self.config_file]):
                alma_backend.main()

        with open(self.response_file, 'r', encoding='utf-8') as f:
            return json.load(f), mock_requests

    def test_backend_success_numbered_variants(self):
        """Test successful response with numbered variants."""
        result, mock_requests = self._run_with_mock_ollama(
            "1. Привет!\n2. Здравствуйте!\n3. Приветствую!"
        )

        self.assertIn("variants", result)
        self.assertEqual(len(result["variants"]), 3)
        self.assertEqual(result["variants"][0], "Привет!")
        self.assertEqual(result["variants"][1], "Здравствуйте!")

    def test_backend_success_plain_russian(self):
        """Test successful response with plain Russian text."""
        result, _ = self._run_with_mock_ollama("Привет мир")

        self.assertIn("variants", result)
        self.assertGreater(len(result["variants"]), 0)
        self.assertEqual(result["variants"][0], "Привет мир")

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

        result, mock_requests = self._run_with_mock_ollama("Тестовый перевод")

        # Verify request was made
        mock_requests.post.assert_called_once()

        # Check response is valid
        self.assertIn("variants", result)

    def test_backend_with_feedback(self):
        """Test that feedback is included in request."""
        request_with_feedback = self.default_request.copy()
        request_with_feedback["feedback"] = "Сделай короче"
        with open(self.request_file, 'w', encoding='utf-8') as f:
            json.dump(request_with_feedback, f)

        result, mock_requests = self._run_with_mock_ollama("Короткий вариант")

        self.assertIn("variants", result)

    def test_backend_missing_config(self):
        """Test error handling when config file is missing."""
        os.remove(self.config_file)

        with patch.object(sys, 'argv', ['alma_backend.py', self.request_file, self.response_file, self.config_file]):
            alma_backend.main()

        with open(self.response_file, 'r', encoding='utf-8') as f:
            result = json.load(f)

        self.assertIn("error", result)
        self.assertIn("Config file not found", result["error"])

    def test_backend_missing_request(self):
        """Test error handling when request file is missing."""
        os.remove(self.request_file)

        with patch.object(alma_backend, 'requests') as mock_requests:
            with patch.object(sys, 'argv', ['alma_backend.py', self.request_file, self.response_file, self.config_file]):
                alma_backend.main()

        with open(self.response_file, 'r', encoding='utf-8') as f:
            result = json.load(f)

        self.assertIn("error", result)
        self.assertIn("Request file not found", result["error"])

    def test_backend_ollama_connection_error(self):
        """Test error when Ollama is not running."""
        import requests as real_requests

        with patch.object(alma_backend, 'requests') as mock_requests:
            mock_requests.post.side_effect = real_requests.exceptions.ConnectionError("Connection refused")
            mock_requests.exceptions = real_requests.exceptions

            with patch.object(sys, 'argv', ['alma_backend.py', self.request_file, self.response_file, self.config_file]):
                alma_backend.main()

        with open(self.response_file, 'r', encoding='utf-8') as f:
            result = json.load(f)

        self.assertIn("error", result)
        self.assertIn("Cannot connect to Ollama", result["error"])


class TestPromptGeneration(unittest.TestCase):
    """Tests for prompt generation."""

    def test_generate_prompt_ollama_basic(self):
        """Test basic Ollama prompt generation."""
        data = {
            "current_line": {"ru": "Привет", "en": "Hello", "duration": 1.0},
            "context_before": [],
            "context_after": [],
            "feedback": ""
        }
        prompt = alma_backend.generate_translation_prompt(data, for_ollama=True)

        self.assertIn("Hello", prompt)
        self.assertIn("1.0s duration", prompt)
        self.assertIn("3 different Russian translation", prompt)

    def test_generate_prompt_with_feedback(self):
        """Test prompt includes feedback."""
        data = {
            "current_line": {"ru": "Тест", "en": "Test", "duration": 1.0},
            "context_before": [],
            "context_after": [],
            "feedback": "Сделай более формально"
        }
        prompt = alma_backend.generate_translation_prompt(data, for_ollama=True)

        self.assertIn("Сделай более формально", prompt)
        self.assertIn("User instruction", prompt)

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
        prompt = alma_backend.generate_translation_prompt(data, for_ollama=True)

        self.assertIn("Previous", prompt)
        self.assertIn("Next", prompt)

    def test_generate_prompt_alma_style(self):
        """Test ALMA-style prompt (for transformers)."""
        data = {
            "current_line": {"ru": "", "en": "Hello world", "duration": 1.0},
            "context_before": [],
            "context_after": [],
            "feedback": ""
        }
        prompt = alma_backend.generate_translation_prompt(data, for_ollama=False)

        self.assertIn("Translate this from English to Russian", prompt)
        self.assertIn("Hello world", prompt)


class TestResponseParsing(unittest.TestCase):
    """Tests for response parsing."""

    def test_parse_numbered_variants(self):
        """Test parsing numbered variants."""
        text = "1. Привет\n2. Здравствуй\n3. Приветствую"
        variants = alma_backend.parse_variants(text)

        self.assertEqual(len(variants), 3)
        self.assertEqual(variants[0], "Привет")
        self.assertEqual(variants[1], "Здравствуй")
        self.assertEqual(variants[2], "Приветствую")

    def test_parse_variants_with_dots(self):
        """Test parsing variants with different numbering."""
        text = "1) Первый\n2) Второй\n3) Третий"
        variants = alma_backend.parse_variants(text)

        self.assertEqual(len(variants), 3)
        self.assertEqual(variants[0], "Первый")

    def test_parse_plain_russian(self):
        """Test parsing plain Russian text."""
        text = "Привет мир"
        variants = alma_backend.parse_variants(text)

        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0], "Привет мир")

    def test_parse_mixed_content(self):
        """Test parsing response with mixed content."""
        text = "Here are the translations:\n1. Привет\n2. Здравствуйте"
        variants = alma_backend.parse_variants(text)

        self.assertGreater(len(variants), 0)
        self.assertIn("Привет", variants)

    def test_parse_empty_response(self):
        """Test parsing empty response uses fallback."""
        variants = alma_backend.parse_variants("", original_ru="Оригинал")

        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0], "Оригинал")

    def test_parse_english_only_response(self):
        """Test parsing response with only English."""
        variants = alma_backend.parse_variants("Hello world", original_ru="Привет мир")

        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0], "Привет мир")

    def test_parse_deduplicates(self):
        """Test that duplicate variants are removed."""
        text = "1. Привет\n2. Привет\n3. Здравствуй"
        variants = alma_backend.parse_variants(text)

        self.assertEqual(len(variants), 2)
        self.assertEqual(variants[0], "Привет")
        self.assertEqual(variants[1], "Здравствуй")

    def test_parse_limits_to_three(self):
        """Test that variants are limited to 3."""
        text = "1. Один\n2. Два\n3. Три\n4. Четыре\n5. Пять"
        variants = alma_backend.parse_variants(text)

        self.assertEqual(len(variants), 3)


class TestConfigLoading(unittest.TestCase):
    """Tests for config loading."""

    def test_load_valid_config(self):
        """Test loading valid config."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"backend": "ollama", "model": "llama3"}, f)
            f.flush()

            config = alma_backend.load_config(f.name)

            self.assertEqual(config["backend"], "ollama")
            self.assertEqual(config["model"], "llama3")

            os.unlink(f.name)

    def test_load_missing_config(self):
        """Test error on missing config."""
        with self.assertRaises(FileNotFoundError):
            alma_backend.load_config("/nonexistent/config.json")

    def test_load_invalid_json(self):
        """Test error on invalid JSON."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("not valid json")
            f.flush()

            with self.assertRaises(json.JSONDecodeError):
                alma_backend.load_config(f.name)

            os.unlink(f.name)


if __name__ == '__main__':
    unittest.main()
