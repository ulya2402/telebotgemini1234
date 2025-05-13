import os
from dotenv import load_dotenv

load_dotenv()

def get_boolean_env_var(var_name: str, default: bool = False) -> bool:
    value = os.getenv(var_name, '').lower()
    if value in ['true', '1', 'yes', 'on']:
        return True
    if value in ['false', '0', 'no', 'off']:
        return False
    if var_name not in os.environ:
        return default
    return default

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("Pastikan TELEGRAM_BOT_TOKEN sudah diatur di environment variable atau file .env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_SYSTEM_PROMPT = os.getenv("GEMINI_SYSTEM_PROMPT", "You are a helpful and friendly AI assistant.")


FEATURE_ENABLE_GEMINI = get_boolean_env_var("FEATURE_ENABLE_GEMINI", default=True)
if FEATURE_ENABLE_GEMINI and not GEMINI_API_KEY:
    raise ValueError("Fitur Gemini diaktifkan (FEATURE_ENABLE_GEMINI=true) tetapi GEMINI_API_KEY belum diatur.")

FEATURE_ENABLE_CONVERSATION_HISTORY = get_boolean_env_var("FEATURE_ENABLE_CONVERSATION_HISTORY", default=True)
GEMINI_CONVERSATION_HISTORY_MAX_MESSAGES = int(os.getenv("GEMINI_CONVERSATION_HISTORY_MAX_MESSAGES", "10"))
FEATURE_ENABLE_DAILY_CHAT_LIMIT = get_boolean_env_var("FEATURE_ENABLE_DAILY_CHAT_LIMIT", default=True)
DAILY_CHAT_LIMIT_PER_USER = int(os.getenv("DAILY_CHAT_LIMIT_PER_USER", "20"))

FEATURE_ENABLE_IMAGE_UNDERSTANDING = get_boolean_env_var("FEATURE_ENABLE_IMAGE_UNDERSTANDING", default=True)
MAX_IMAGES_PER_ALBUM = int(os.getenv("MAX_IMAGES_PER_ALBUM", "5"))

FEATURE_ENABLE_AUDIO_UNDERSTANDING = get_boolean_env_var("FEATURE_ENABLE_AUDIO_UNDERSTANDING", default=True)
MAX_AUDIO_FILE_SIZE_BYTES_BOT = int(os.getenv("MAX_AUDIO_FILE_SIZE_BYTES_BOT", "18874368"))
SUPPORTED_AUDIO_MIME_TYPES_GEMINI = [
    "audio/wav", "audio/wave", "audio/x-wav",
    "audio/mp3", "audio/mpeg",
    "audio/aiff", "audio/x-aiff",
    "audio/aac",
    "audio/ogg", "audio/vorbis",
    "audio/flac"
]

FEATURE_ENABLE_DOCUMENT_UNDERSTANDING = get_boolean_env_var("FEATURE_ENABLE_DOCUMENT_UNDERSTANDING", default=True)
MAX_DOCUMENT_FILE_SIZE_BYTES_BOT = int(os.getenv("MAX_DOCUMENT_FILE_SIZE_BYTES_BOT", "18874368")) 
SUPPORTED_DOCUMENT_MIME_TYPES_BOT = ["application/pdf"] 

TELEGRAM_MAX_MESSAGE_LENGTH = 4000

GROUP_TRIGGER_COMMANDS = os.getenv("GROUP_TRIGGER_COMMANDS", "ai,chat,ask,tanya").split(',')
GROUP_TRIGGER_COMMANDS = [cmd.strip() for cmd in GROUP_TRIGGER_COMMANDS if cmd.strip()]
if not GROUP_TRIGGER_COMMANDS:
    GROUP_TRIGGER_COMMANDS = ["ai", "chat", "ask", "tanya"]

AVAILABLE_GEMINI_MODELS = {
    "gemini-1.5-flash-8b": "Gemini 1.5 Flash 8B",
    "gemini-1.5-flash-latest": "Gemini 1.5 Flash",
    "gemini-2.0-flash-lite": "Gemini 2.0 Flash Lite",
    "gemini-2.0-flash": "Gemini 2.0 Flash",
    "gemini-2.5-flash-preview-04-17": "Gemini 2.5 Flash Preview"
}
DEFAULT_GEMINI_MODEL_ID = os.getenv("DEFAULT_GEMINI_MODEL_ID", "gemini-1.5-flash-latest")

if DEFAULT_GEMINI_MODEL_ID not in AVAILABLE_GEMINI_MODELS:
    original_default = DEFAULT_GEMINI_MODEL_ID
    if AVAILABLE_GEMINI_MODELS:
        DEFAULT_GEMINI_MODEL_ID = next(iter(AVAILABLE_GEMINI_MODELS))
        print(f"Peringatan: DEFAULT_GEMINI_MODEL_ID '{original_default}' tidak valid atau tidak ada di AVAILABLE_GEMINI_MODELS. Menggunakan fallback: '{DEFAULT_GEMINI_MODEL_ID}'.")
    else:
        print(f"Peringatan Kritis: DEFAULT_GEMINI_MODEL_ID '{original_default}' tidak valid dan AVAILABLE_GEMINI_MODELS kosong. Model default tidak dapat diatur.")
        DEFAULT_GEMINI_MODEL_ID = "gemini-1.5-flash-latest"
        print(f"Menggunakan fallback keras: {DEFAULT_GEMINI_MODEL_ID}")

AVAILABLE_LANGUAGES = {
    "en": "🇺🇲 English",
    "id": "🇮🇩 Bahasa Indonesia",
    "ru": "🇷🇺 Русский"
}
DEFAULT_LANGUAGE = os.getenv("DEFAULT_LANGUAGE", "id").lower()
if DEFAULT_LANGUAGE not in AVAILABLE_LANGUAGES:
    print(f"Peringatan: DEFAULT_LANGUAGE '{DEFAULT_LANGUAGE}' tidak ada dalam AVAILABLE_LANGUAGES. Menggunakan 'en' sebagai fallback.")
    DEFAULT_LANGUAGE = "en"

FEATURE_ENABLE_DATABASE = get_boolean_env_var("FEATURE_ENABLE_DATABASE", default=False)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if FEATURE_ENABLE_DATABASE:
    if not SUPABASE_URL:
        raise ValueError("Fitur Database diaktifkan (FEATURE_ENABLE_DATABASE=true) tetapi SUPABASE_URL belum diatur.")
    if not SUPABASE_KEY:
        raise ValueError("Fitur Database diaktifkan (FEATURE_ENABLE_DATABASE=true) tetapi SUPABASE_KEY belum diatur.")

print("-" * 30)
print("Konfigurasi Dimuat:")
print(f"  Fitur Limit Chat Harian: {FEATURE_ENABLE_DAILY_CHAT_LIMIT}")
if FEATURE_ENABLE_DAILY_CHAT_LIMIT:
    print(f"  Limit Chat Harian per User: {DAILY_CHAT_LIMIT_PER_USER}")
print(f"  Token Bot Telegram: {'Ada' if TELEGRAM_BOT_TOKEN else 'TIDAK ADA'}")
print(f"  Fitur Gemini Aktif: {FEATURE_ENABLE_GEMINI}")
if FEATURE_ENABLE_GEMINI:
    print(f"  Perintah Pemicu Grup: {GROUP_TRIGGER_COMMANDS}")
    print(f"  API Key Gemini: {'Ada' if GEMINI_API_KEY else 'TIDAK ADA (WAJIB jika Fitur Gemini aktif)'}")
    print(f"  Model Gemini Tersedia: {list(AVAILABLE_GEMINI_MODELS.keys())}")
    print(f"  Model Gemini Default: {DEFAULT_GEMINI_MODEL_ID}")
print(f"  Fitur Riwayat Percakapan: {FEATURE_ENABLE_CONVERSATION_HISTORY}")
if FEATURE_ENABLE_CONVERSATION_HISTORY:
    print(f"  Max Pesan Riwayat Gemini: {GEMINI_CONVERSATION_HISTORY_MAX_MESSAGES}")
print(f"  Fitur Pemahaman Gambar: {FEATURE_ENABLE_IMAGE_UNDERSTANDING}")
if FEATURE_ENABLE_IMAGE_UNDERSTANDING:
    print(f"  Max Gambar per Album: {MAX_IMAGES_PER_ALBUM}")
print(f"  Fitur Pemahaman Audio: {FEATURE_ENABLE_AUDIO_UNDERSTANDING}")
if FEATURE_ENABLE_AUDIO_UNDERSTANDING:
    print(f"  Max Ukuran File Audio Bot (MB): {MAX_AUDIO_FILE_SIZE_BYTES_BOT / (1024*1024)}")
    print(f"  MIME Audio Didukung Gemini: {SUPPORTED_AUDIO_MIME_TYPES_GEMINI}")
print(f"  Fitur Pemahaman Dokumen: {FEATURE_ENABLE_DOCUMENT_UNDERSTANDING}")
if FEATURE_ENABLE_DOCUMENT_UNDERSTANDING:
    print(f"  Max Ukuran File Dokumen Bot (MB): {MAX_DOCUMENT_FILE_SIZE_BYTES_BOT / (1024*1024)}")
    print(f"  MIME Dokumen Didukung Bot: {SUPPORTED_DOCUMENT_MIME_TYPES_BOT}")
print(f"  Bahasa Tersedia: {', '.join(AVAILABLE_LANGUAGES.keys())}")
print(f"  Bahasa Default: {DEFAULT_LANGUAGE}")
print(f"  Fitur Database Aktif: {FEATURE_ENABLE_DATABASE}")
if FEATURE_ENABLE_DATABASE:
    print(f"  Supabase URL: {'Ada' if SUPABASE_URL else 'TIDAK ADA'}")
    print(f"  Supabase Key: {'Ada' if SUPABASE_KEY else 'TIDAK ADA'}")
print("-" * 30)
