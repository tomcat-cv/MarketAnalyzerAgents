import unittest

from dailyresearch.openai_runner import build_payload, extract_response_text


class OpenAIRunnerTests(unittest.TestCase):
    def test_extract_response_text_uses_output_text(self) -> None:
        self.assertEqual(extract_response_text({"output_text": "hello"}), "hello")

    def test_extract_response_text_walks_message_content(self) -> None:
        payload = {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "brief"}],
                }
            ]
        }
        self.assertEqual(extract_response_text(payload), "brief")

    def test_build_payload_is_summary_only(self) -> None:
        payload = build_payload(
            model="gpt-test",
            system="s",
            user="u",
            reasoning_effort="medium",
        )
        self.assertNotIn("tools", payload)
        self.assertEqual(payload["model"], "gpt-test")


if __name__ == "__main__":
    unittest.main()
