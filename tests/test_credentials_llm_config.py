"""Credential env-fallback and per-account LLM provider tests.

Hand-verified expectations:
- YAML credential value wins over the environment variable.
- A missing or REPLACE_ME credential falls back to MT5_* env vars.
- llm_provider is validated against the allowed set.
- build_llm_brain respects the account's provider/model/url settings.
- provider="off" forces the deterministic local reasoner even when
  Ollama is reachable and an OpenAI key is present.
- provider="openai" without a key degrades to local reasoning with a
  note, never crashes.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config_loader import (AccountConfig, AccountConfigError,
                                build_llm_brain, load_account,
                                llm_settings)


def _write_account(user, text):
    d = Path(tempfile.mkdtemp()) / "accounts"
    d.mkdir(exist_ok=True)
    (d / ("%s.yaml" % user)).write_text(text)
    return str(d)


class CredentialFallbackTests(unittest.TestCase):
    def test_yaml_value_wins_over_env(self):
        accounts = _write_account(
            "u1", 'broker: mt5\nmode: paper\n'
                       'mt5_login: "from_file"\nmt5_password: "fp"\n'
                       'mt5_server: "fs"\n')
        with mock.patch.dict(os.environ, {"MT5_LOGIN": "from_env",
                                          "MT5_PASSWORD": "ep",
                                          "MT5_SERVER": "es"}):
            acct = load_account("u1", accounts_dir=accounts)
        self.assertEqual(acct.mt5_login, "from_file")
        self.assertEqual(acct.mt5_password, "fp")
        self.assertEqual(acct.mt5_server, "fs")

    def test_replace_me_falls_back_to_env(self):
        accounts = _write_account(
            "u2", 'broker: mt5\nmode: paper\n'
                       'mt5_login: "REPLACE_ME"\n'
                       'mt5_password: "REPLACE_ME"\n'
                       'mt5_server: "REPLACE_ME"\n')
        with mock.patch.dict(os.environ, {"MT5_LOGIN": "env_login",
                                          "MT5_PASSWORD": "env_pw",
                                          "MT5_SERVER": "env_srv"}):
            acct = load_account("u2", accounts_dir=accounts)
        self.assertEqual(acct.mt5_login, "env_login")
        self.assertEqual(acct.mt5_password, "env_pw")
        self.assertEqual(acct.mt5_server, "env_srv")

    def test_missing_stays_none_without_env(self):
        accounts = _write_account("u3", 'broker: mt5\nmode: paper\n')
        env = {k: v for k, v in os.environ.items()
               if k not in ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER")}
        with mock.patch.dict(os.environ, env, clear=True):
            acct = load_account("u3", accounts_dir=accounts)
        self.assertIsNone(acct.mt5_login)
        self.assertIsNone(acct.mt5_password)
        self.assertIsNone(acct.mt5_server)


class LLMSettingsTests(unittest.TestCase):
    def _acct(self, extra):
        return AccountConfig(user_id="u", extra=extra)

    def test_defaults_to_ollama_local(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("OLLAMA_MODEL", "OLLAMA_URL")}
        with mock.patch.dict(os.environ, env, clear=True):
            settings = llm_settings(self._acct({}))
        self.assertEqual(settings["provider"], "ollama")
        self.assertEqual(settings["model"], "llama3.2")
        self.assertEqual(settings["url"], "http://localhost:11434")

    def test_account_keys_override_env(self):
        with mock.patch.dict(os.environ, {"OLLAMA_MODEL": "env_model",
                                          "OLLAMA_URL": "http://env:1"}):
            settings = llm_settings(self._acct(
                {"llm_provider": "off", "llm_model": "qwen2.5",
                 "llm_url": "http://x:11434"}))
        self.assertEqual(settings["provider"], "off")
        self.assertEqual(settings["model"], "qwen2.5")
        self.assertEqual(settings["url"], "http://x:11434")

    def test_env_used_when_account_key_absent(self):
        with mock.patch.dict(os.environ, {"OLLAMA_MODEL": "env_model",
                                          "OLLAMA_URL": "http://env:1"}):
            settings = llm_settings(self._acct({}))
        self.assertEqual(settings["model"], "env_model")
        self.assertEqual(settings["url"], "http://env:1")

    def test_invalid_provider_rejected(self):
        with self.assertRaises(AccountConfigError):
            llm_settings(self._acct({"llm_provider": "claude"}))

    def test_build_llm_brain_respects_account(self):
        brain = build_llm_brain(self._acct(
            {"llm_provider": "off", "llm_model": "m",
             "llm_url": "http://y:2"}))
        self.assertEqual(brain.provider, "off")
        self.assertEqual(brain.ollama_model, "m")
        self.assertEqual(brain.ollama_url, "http://y:2")


class ProviderDispatchTests(unittest.TestCase):
    PROPOSAL = {
        "signal_id": "s1",
        "instrument": "BTC_USD",
        "direction": "long",
        "entry": 100.0,
        "stop": 95.0,
        "target": 120.0,
        "score": 0.6,
        "reasons": ["trend up", "regime: risk_on"],
    }

    def test_off_ignores_reachable_ollama_and_key(self):
        from core.llm_brain import LLMBrain
        brain = LLMBrain(provider="off", api_key="sk-x")
        brain._ollama_probe = True  # pretend Ollama is reachable
        ann = brain.annotate(self.PROPOSAL)
        self.assertEqual(ann["source"], "local_deterministic")
        self.assertIn("advisory", ann)
        self.assertTrue(ann["advisory"])

    def test_off_ignores_reachable_ollama(self):
        from core.llm_brain import LLMBrain
        brain = LLMBrain(provider="off")
        brain._ollama_probe = True
        ann = brain.annotate(self.PROPOSAL)
        self.assertEqual(ann["source"], "local_deterministic")

    def test_openai_without_key_degrades_with_note(self):
        from core.llm_brain import LLMBrain
        env = {k: v for k, v in os.environ.items()
               if k != "OPENAI_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            brain = LLMBrain(provider="openai")
        brain._ollama_probe = True  # ollama IS up, but provider=openai
        ann = brain.annotate(self.PROPOSAL)
        self.assertEqual(ann["source"], "local_deterministic")
        self.assertTrue(any("OPENAI_API_KEY" in n for n in ann["notes"]))

    def test_invalid_provider_rejected_in_brain(self):
        from core.llm_brain import LLMBrain
        with self.assertRaises(ValueError):
            LLMBrain(provider="bogus")


if __name__ == "__main__":
    unittest.main()
