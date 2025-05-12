import json
import os
from typing import Dict, Optional

from core.config import AVAILABLE_LANGUAGES, DEFAULT_LANGUAGE

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALES_DIR = os.path.join(BASE_DIR, "locales")

_translations: Dict[str, Dict[str, str]] = {}

def load_translations(lang_code: str) -> Dict[str, str]:
    if lang_code not in AVAILABLE_LANGUAGES:
        lang_code = DEFAULT_LANGUAGE

    if lang_code not in _translations:
        try:
            file_path = os.path.join(LOCALES_DIR, lang_code, "general.json")
            with open(file_path, "r", encoding="utf-8") as f:
                _translations[lang_code] = json.load(f)
        except FileNotFoundError:
            print(f"ERROR: File terjemahan tidak ditemukan: {file_path}")
            if lang_code != DEFAULT_LANGUAGE:
                return load_translations(DEFAULT_LANGUAGE)
            else:
                _translations[lang_code] = {}
        except json.JSONDecodeError:
            print(f"ERROR: Gagal mem-parse file JSON: {file_path}")
            if lang_code != DEFAULT_LANGUAGE:
                return load_translations(DEFAULT_LANGUAGE)
            else:
                _translations[lang_code] = {}
    return _translations.get(lang_code, {})

for lang in AVAILABLE_LANGUAGES.keys():
    load_translations(lang)
if DEFAULT_LANGUAGE not in _translations:
    load_translations(DEFAULT_LANGUAGE)


def get_translation(key: str, lang_code: Optional[str] = None, default_return_key_on_missing: bool = False, **kwargs) -> str:
    effective_lang_code = lang_code
    if not effective_lang_code or effective_lang_code not in AVAILABLE_LANGUAGES:
        effective_lang_code = DEFAULT_LANGUAGE

    translations = _translations.get(effective_lang_code)
    if not translations and effective_lang_code != DEFAULT_LANGUAGE:
        translations = _translations.get(DEFAULT_LANGUAGE)
    elif not translations and effective_lang_code == DEFAULT_LANGUAGE:
         return f"ERR_LOAD_DEF_LANG:{key}" if not default_return_key_on_missing else key

    text = None
    if translations: 
        text = translations.get(key)

    if text is None and effective_lang_code != DEFAULT_LANGUAGE:
        default_translations = _translations.get(DEFAULT_LANGUAGE, {})
        text = default_translations.get(key)

    if text is None:
        return key if default_return_key_on_missing else f"TR_MISSING:{key}"

    try:
        return text.format(**kwargs)
    except KeyError as e:
        print(f"Error formatting translation for key '{key}' in lang '{effective_lang_code}': Missing placeholder {e}")
        return text 
    except Exception as e_format:
        print(f"General error formatting translation for key '{key}' in lang '{effective_lang_code}': {e_format}")
        return text

def _(key: str, user_language_code: Optional[str] = None, default_return_key_on_missing: bool = False, **kwargs) -> str:
    return get_translation(key, lang_code=user_language_code, default_return_key_on_missing=default_return_key_on_missing, **kwargs)