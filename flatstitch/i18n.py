"""Minimal i18n: JSON translation catalogs keyed by short string IDs, with
named-placeholder formatting (safer for translators than positional/f-string
interpolation, since word order legitimately varies across languages).

Usage:
    from .i18n import _
    logger.info(_("pipeline.log.saved", path=dest))

Language resolution order: explicit set_language() call (CLI --lang / GUI
picker) > LC_ALL/LC_MESSAGES/LANG environment variables > English. A
language missing a given key falls back to English, then to the raw key
itself, so a missing translation is always visible rather than crashing.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

LOCALES_DIR = Path(__file__).resolve().parent / "locales"

# name -> (English display name, native display name)
SUPPORTED_LANGUAGES = {
    "en": ("English", "English"),
    "uk": ("Ukrainian", "Українська"),
    "es": ("Spanish", "Español"),
    "fr": ("French", "Français"),
    "de": ("German", "Deutsch"),
    "pt_BR": ("Portuguese (Brazil)", "Português (Brasil)"),
    "it": ("Italian", "Italiano"),
    "pl": ("Polish", "Polski"),
    "zh_CN": ("Chinese (Simplified)", "简体中文"),
    "ja": ("Japanese", "日本語"),
}
DEFAULT_LANGUAGE = "en"

_catalogs: dict[str, dict] = {}
_current_lang = DEFAULT_LANGUAGE


def _load(lang: str) -> dict:
    if lang in _catalogs:
        return _catalogs[lang]
    path = LOCALES_DIR / f"{lang}.json"
    data = {}
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            data = {}
    _catalogs[lang] = data
    return data


def detect_system_language() -> str:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(var, "")
        if not val or val in ("C", "POSIX"):
            continue
        base = val.split(".")[0]  # strip encoding, e.g. uk_UA.UTF-8 -> uk_UA
        if base in SUPPORTED_LANGUAGES:
            return base
        short = base.split("_")[0]
        if short in SUPPORTED_LANGUAGES:
            return short
    return DEFAULT_LANGUAGE


def set_language(lang: str | None) -> str:
    """Set the active language. Passing None re-detects from the
    environment. Returns the language actually applied (falls back to
    English if the requested one isn't supported)."""
    global _current_lang
    if not lang:
        lang = detect_system_language()
    if lang not in SUPPORTED_LANGUAGES:
        lang = DEFAULT_LANGUAGE
    _current_lang = lang
    _load(lang)
    _load(DEFAULT_LANGUAGE)
    return lang


def current_language() -> str:
    return _current_lang


def _(key: str, **kwargs) -> str:
    catalog = _load(_current_lang)
    template = catalog.get(key)
    if template is None:
        template = _load(DEFAULT_LANGUAGE).get(key, key)
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return template
    return template


set_language(None)
