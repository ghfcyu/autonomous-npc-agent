"""OpenAICompatProvider 网络异常处理测试。

验证所有 OSError 子类（含 TimeoutError/ConnectionError）被统一包装为
LLMError，使决策层 except (LLMError, ...) 回退链正常工作。
修复背景：except urllib.error.URLError 不覆盖 TimeoutError（OSError 子类
但非 URLError 子类），导致真实 LLM 调用超时异常穿透 decision.py 回退链。
"""

import unittest
from unittest.mock import patch, MagicMock

from engine.llm.base import LLMError
from engine.llm.openai_compat import OpenAICompatProvider, DEFAULT_TIMEOUT


class TestOpenAICompatErrorHandling(unittest.TestCase):
    """网络异常统一包装为 LLMError（决策层回退链依赖此契约）。"""

    def _make_provider(self):
        """构造一个 available=True 的 provider（base_url 已设置）。"""
        return OpenAICompatProvider(
            base_url="https://fake.example.com/v1",
            api_key="fake-key",
            model="fake-model",
        )

    def test_timeout_error_wrapped_as_llm_error(self):
        """TimeoutError（OSError 子类，非 URLError 子类）→ LLMError。"""
        provider = self._make_provider()
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = TimeoutError("read timed out")
            with self.assertRaises(LLMError) as ctx:
                provider.chat([{"role": "user", "content": "hi"}])
            self.assertIn("LLM request failed", str(ctx.exception))

    def test_url_error_still_wrapped(self):
        """URLError（OSError 子类）→ LLMError（向后兼容）。"""
        import urllib.error
        provider = self._make_provider()
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.URLError("connection refused")
            with self.assertRaises(LLMError):
                provider.chat([{"role": "user", "content": "hi"}])

    def test_connection_error_wrapped(self):
        """ConnectionError（OSError 子类）→ LLMError。"""
        provider = self._make_provider()
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = ConnectionError("network unreachable")
            with self.assertRaises(LLMError):
                provider.chat([{"role": "user", "content": "hi"}])

    def test_default_timeout_is_60(self):
        """超时从 30s 提升到 60s（真实 LLM 端点慢，30s 边界过紧）。"""
        self.assertEqual(DEFAULT_TIMEOUT, 60)

    def test_unavailable_raises_llm_error(self):
        """无 base_url → available=False → chat 直接 LLMError。"""
        provider = OpenAICompatProvider(
            base_url="", api_key="k", model="m")
        self.assertFalse(provider.available)
        with self.assertRaises(LLMError):
            provider.chat([{"role": "user", "content": "hi"}])


if __name__ == "__main__":
    unittest.main()
