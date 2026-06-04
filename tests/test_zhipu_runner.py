import unittest

from dailyresearch.zhipu_runner import build_zhipu_payload, extract_zhipu_text


class ZhipuRunnerTests(unittest.TestCase):
    def test_extract_zhipu_text_uses_first_message_content(self) -> None:
        payload = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "brief",
                    }
                }
            ]
        }
        self.assertEqual(extract_zhipu_text(payload), "brief")

    def test_build_payload_is_summary_only_even_with_legacy_search_config(self) -> None:
        payload = build_zhipu_payload(
            model="glm-5.1",
            system="s",
            user="u",
            zhipu_settings={
                "temperature": 0.7,
                "web_search": {
                    "enable": True,
                    "search_engine": "search_pro",
                    "count": 10,
                    "search_recency_filter": "oneDay",
                    "content_size": "high",
                },
            },
        )
        self.assertEqual(payload["model"], "glm-5.1")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertNotIn("tools", payload)
        self.assertNotIn("tool_choice", payload)


if __name__ == "__main__":
    unittest.main()
