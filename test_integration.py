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

# Add automation dir to path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTOMATION_DIR = os.path.join(SCRIPT_DIR, 'automation')
sys.path.insert(0, AUTOMATION_DIR)

import alma_backend


class TestAlmaBackend(unittest.TestCase):
    """Tests for alma_backend module."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.request_file = os.path.join(self.temp_dir, "test_request.json")
        self.response_file = os.path.join(self.temp_dir, "test_response.json")
        self.config_file = os.path.join(self.temp_dir, "test_config.json")

        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump({
                "backend": "ollama",
                "ollama_url": "http://localhost:11434",
                "model": "llama3",
                "temperature": 0.7,
                "num_variants": 3,
                "global_context": "",
                "default_instructions": "",
                "translation_style": "natural"
            }, f)

        self.default_request = {
            "current_line": {"ru": "Привет", "en": "Hello", "duration": 1.5},
            "context_before": [],
            "context_after": [],
            "feedback": ""
        }
        with open(self.request_file, 'w', encoding='utf-8') as f:
            json.dump(self.default_request, f)

    def tearDown(self):
        for fp in [self.request_file, self.response_file, self.config_file]:
            if os.path.exists(fp):
                os.remove(fp)
        if os.path.exists(self.temp_dir):
            os.rmdir(self.temp_dir)

    def _run_with_mock_ollama(self, response_text):
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
        result, _ = self._run_with_mock_ollama("1. Привет!\n2. Здравствуйте!\n3. Приветствую!")
        self.assertIn("variants", result)
        self.assertEqual(len(result["variants"]), 3)

    def test_backend_success_plain_russian(self):
        result, _ = self._run_with_mock_ollama("Привет мир")
        self.assertIn("variants", result)
        self.assertEqual(result["variants"][0], "Привет мир")

    def test_backend_with_context(self):
        request = {
            "current_line": {"ru": "Тест", "en": "Test", "duration": 2.0},
            "context_before": [{"ru": "Строка до", "en": "Line before"}],
            "context_after": [{"ru": "Строка после", "en": "Line after"}],
            "feedback": ""
        }
        with open(self.request_file, 'w', encoding='utf-8') as f:
            json.dump(request, f)

        result, mock_requests = self._run_with_mock_ollama("Тестовый перевод")
        mock_requests.post.assert_called_once()
        self.assertIn("variants", result)

    def test_backend_with_feedback(self):
        request = self.default_request.copy()
        request["feedback"] = "Сделай короче"
        with open(self.request_file, 'w', encoding='utf-8') as f:
            json.dump(request, f)

        result, _ = self._run_with_mock_ollama("Короткий вариант")
        self.assertIn("variants", result)

    def test_backend_missing_config(self):
        os.remove(self.config_file)
        with patch.object(sys, 'argv', ['alma_backend.py', self.request_file, self.response_file, self.config_file]):
            alma_backend.main()

        with open(self.response_file, 'r', encoding='utf-8') as f:
            result = json.load(f)
        self.assertIn("error", result)
        self.assertIn("Config file not found", result["error"])

    def test_backend_missing_request(self):
        os.remove(self.request_file)
        with patch.object(alma_backend, 'requests') as mock_requests:
            with patch.object(sys, 'argv', ['alma_backend.py', self.request_file, self.response_file, self.config_file]):
                alma_backend.main()

        with open(self.response_file, 'r', encoding='utf-8') as f:
            result = json.load(f)
        self.assertIn("error", result)
        self.assertIn("Request file not found", result["error"])

    def test_backend_ollama_connection_error(self):
        import requests as real_requests
        with patch.object(alma_backend, 'requests') as mock_requests:
            mock_requests.post.side_effect = Exception("Connection refused")
            mock_requests.exceptions = real_requests.exceptions
            with patch.object(sys, 'argv', ['alma_backend.py', self.request_file, self.response_file, self.config_file]):
                alma_backend.main()

        with open(self.response_file, 'r', encoding='utf-8') as f:
            result = json.load(f)
        self.assertIn("error", result)

    def test_backend_batch_mode(self):
        request = {
            "batch_lines": [
                {"en": "Hello", "ru": ""},
                {"en": "World", "ru": ""}
            ]
        }
        with open(self.request_file, 'w', encoding='utf-8') as f:
            json.dump(request, f)

        result, _ = self._run_with_mock_ollama("1.1. Привет\n1.2. Здравствуй\n2.1. Мир\n2.2. Свет")
        self.assertIn("batch_variants", result)


class TestPromptGeneration(unittest.TestCase):
    """Tests for prompt generation."""

    def setUp(self):
        self.config = {
            "num_variants": 3,
            "global_context": "",
            "default_instructions": "",
            "translation_style": "natural"
        }

    def test_generate_prompt_basic(self):
        data = {
            "current_line": {"ru": "Привет", "en": "Hello", "duration": 1.0},
            "context_before": [],
            "context_after": [],
            "feedback": ""
        }
        prompt = alma_backend.generate_translation_prompt(data, self.config)
        self.assertIn("Hello", prompt)
        self.assertIn("3 вариант", prompt)

    def test_generate_prompt_with_feedback(self):
        data = {
            "current_line": {"ru": "Тест", "en": "Test", "duration": 1.0},
            "context_before": [],
            "context_after": [],
            "feedback": "Сделай короче"
        }
        prompt = alma_backend.generate_translation_prompt(data, self.config)
        self.assertIn("Сделай короче", prompt)

    def test_generate_prompt_with_global_context(self):
        config = self.config.copy()
        config["global_context"] = "Это комедия про студентов"
        data = {
            "current_line": {"ru": "Тест", "en": "Test", "duration": 1.0},
            "context_before": [],
            "context_after": [],
            "feedback": ""
        }
        prompt = alma_backend.generate_translation_prompt(data, config)
        self.assertIn("комедия про студентов", prompt)

    def test_generate_prompt_with_style(self):
        config = self.config.copy()
        config["translation_style"] = "formal"
        data = {
            "current_line": {"ru": "", "en": "Hello", "duration": 1.0},
            "context_before": [],
            "context_after": [],
            "feedback": ""
        }
        prompt = alma_backend.generate_translation_prompt(data, config)
        self.assertIn("формально", prompt)

    def test_generate_batch_prompt(self):
        lines = [
            {"en": "Hello"},
            {"en": "World"}
        ]
        prompt = alma_backend.generate_batch_prompt(lines, self.config)
        self.assertIn("Hello", prompt)
        self.assertIn("World", prompt)
        self.assertIn("1.", prompt)
        self.assertIn("2.", prompt)


class TestResponseParsing(unittest.TestCase):
    """Tests for response parsing."""

    def test_parse_numbered_variants(self):
        text = "1. Привет\n2. Здравствуй\n3. Приветствую"
        variants = alma_backend.parse_variants(text, num_variants=3)
        self.assertEqual(len(variants), 3)

    def test_parse_variants_with_different_numbering(self):
        text = "1) Первый\n2) Второй\n3) Третий"
        variants = alma_backend.parse_variants(text)
        self.assertEqual(len(variants), 3)

    def test_parse_plain_russian(self):
        text = "Привет мир"
        variants = alma_backend.parse_variants(text)
        self.assertEqual(variants[0], "Привет мир")

    def test_parse_mixed_content(self):
        text = "Here are the translations:\n1. Привет\n2. Здравствуйте"
        variants = alma_backend.parse_variants(text)
        self.assertIn("Привет", variants)

    def test_parse_empty_response(self):
        variants = alma_backend.parse_variants("", original_ru="Оригинал")
        self.assertEqual(variants[0], "Оригинал")

    def test_parse_english_only_response(self):
        variants = alma_backend.parse_variants("Hello world", original_ru="Привет мир")
        self.assertEqual(variants[0], "Привет мир")

    def test_parse_deduplicates(self):
        text = "1. Привет\n2. Привет\n3. Здравствуй"
        variants = alma_backend.parse_variants(text)
        self.assertEqual(len(variants), 2)

    def test_parse_limits_variants(self):
        text = "1. Один\n2. Два\n3. Три\n4. Четыре"
        variants = alma_backend.parse_variants(text, num_variants=3)
        self.assertEqual(len(variants), 3)

    def test_parse_batch_variants(self):
        text = "1.1. Привет\n1.2. Здравствуй\n2.1. Мир"
        results = alma_backend.parse_batch_variants(text, num_lines=2, num_variants=2)
        self.assertEqual(len(results), 2)
        self.assertIn("Привет", results[0])


class TestConfigLoading(unittest.TestCase):
    """Tests for config loading."""

    def test_load_valid_config(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"backend": "ollama", "model": "llama3"}, f)
            f.flush()
            config = alma_backend.load_config(f.name)
            self.assertEqual(config["backend"], "ollama")
            os.unlink(f.name)

    def test_load_missing_config(self):
        with self.assertRaises(FileNotFoundError):
            alma_backend.load_config("/nonexistent/config.json")

    def test_load_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("not valid json")
            f.flush()
            with self.assertRaises(json.JSONDecodeError):
                alma_backend.load_config(f.name)
            os.unlink(f.name)


if __name__ == '__main__':
    unittest.main()
