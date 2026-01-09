import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add automation dir to path to import backend
sys.path.append(os.path.join(os.getcwd(), 'automation'))
import gemini_backend

class TestGeminiBackend(unittest.TestCase):
    def setUp(self):
        self.request_file = "test_request.json"
        self.response_file = "test_response.json"
        self.config_file = "test_config.json"

        # Create dummy config
        with open(self.config_file, 'w') as f:
            json.dump({
                "gemini_api_key": "TEST_KEY",
                "model": "test-model"
            }, f)

        # Create dummy request
        with open(self.request_file, 'w') as f:
            json.dump({
                "current_line": {"ru": "Привет", "en": "Hello", "duration": 1.0},
                "context_before": [],
                "context_after": [],
                "feedback": ""
            }, f)

    def tearDown(self):
        if os.path.exists(self.request_file):
            os.remove(self.request_file)
        if os.path.exists(self.response_file):
            os.remove(self.response_file)
        if os.path.exists(self.config_file):
            os.remove(self.config_file)

    @patch('google.generativeai.GenerativeModel')
    @patch('google.generativeai.configure')
    def test_backend_success(self, mock_configure, mock_model_cls):
        # Mock API response
        mock_model_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '["Variant 1", "Variant 2"]'
        mock_model_instance.generate_content.return_value = mock_response
        mock_model_cls.return_value = mock_model_instance

        # Run main with arguments
        with patch.object(sys, 'argv', ['gemini_backend.py', self.request_file, self.response_file, self.config_file]):
            gemini_backend.main()

        # Check response file
        with open(self.response_file, 'r') as f:
            result = json.load(f)

        self.assertIn("variants", result)
        self.assertEqual(result["variants"], ["Variant 1", "Variant 2"])

        # Verify configure called with key
        mock_configure.assert_called_with(api_key="TEST_KEY")

    @patch('google.generativeai.GenerativeModel')
    @patch('google.generativeai.configure')
    def test_backend_invalid_json_response(self, mock_configure, mock_model_cls):
        # Mock API response returning plain text instead of JSON
        mock_model_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = 'Just a simple text suggestion.'
        mock_model_instance.generate_content.return_value = mock_response
        mock_model_cls.return_value = mock_model_instance

        with patch.object(sys, 'argv', ['gemini_backend.py', self.request_file, self.response_file, self.config_file]):
            gemini_backend.main()

        with open(self.response_file, 'r') as f:
            result = json.load(f)

        self.assertIn("variants", result)
        self.assertEqual(result["variants"], ["Just a simple text suggestion."])

if __name__ == '__main__':
    unittest.main()
