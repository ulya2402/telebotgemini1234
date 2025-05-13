import logging
from datetime import date
import io
import asyncio
import os

from aiogram import Bot, Router, F, types
from aiogram.filters import CommandStart, Command
from aiogram.filters.command import CommandObject
from aiogram.types import Message, CallbackQuery, PhotoSize, Audio, Voice, Document 
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.enums import ParseMode

from typing import List, Dict, Optional, Tuple, Union, Set, Any, TypedDict

from .utils import local_escape_markdown_v1, ManualChatTypeFilter, split_long_message, ensure_valid_markdown

from core.config import (
    TELEGRAM_MAX_MESSAGE_LENGTH,
    FEATURE_ENABLE_AUDIO_UNDERSTANDING,
    MAX_AUDIO_FILE_SIZE_BYTES_BOT,
    SUPPORTED_AUDIO_MIME_TYPES_GEMINI,
    FEATURE_ENABLE_DOCUMENT_UNDERSTANDING, 
    MAX_DOCUMENT_FILE_SIZE_BYTES_BOT,   
    SUPPORTED_DOCUMENT_MIME_TYPES_BOT   
)

try:
    from google.generativeai.types import ContentDict
except ImportError:
    logging.warning("google.generativeai.types.ContentDict tidak ditemukan.")
    class ContentDict(TypedDict): role: str; parts: List[Dict[str,str]]


from core.localization import _, load_translations
from core.config import (
    AVAILABLE_LANGUAGES, DEFAULT_LANGUAGE,
    FEATURE_ENABLE_GEMINI, FEATURE_ENABLE_DATABASE,
    FEATURE_ENABLE_CONVERSATION_HISTORY, GEMINI_CONVERSATION_HISTORY_MAX_MESSAGES,
    AVAILABLE_GEMINI_MODELS, DEFAULT_GEMINI_MODEL_ID,
    FEATURE_ENABLE_DAILY_CHAT_LIMIT, DAILY_CHAT_LIMIT_PER_USER,
    GROUP_TRIGGER_COMMANDS, FEATURE_ENABLE_IMAGE_UNDERSTANDING, MAX_IMAGES_PER_ALBUM
)
from core.gemini import get_gemini_response
from core.database import (
    get_user_language_from_db, set_user_language_in_db, add_message_to_history,
    get_conversation_history, clear_user_conversation_history,
    get_user_selected_model, set_user_selected_model, check_and_update_chat_limit,
    get_user_chat_status_info
)

for lang_code_init in AVAILABLE_LANGUAGES.keys(): load_translations(lang_code_init)
load_translations(DEFAULT_LANGUAGE)

class GroupMessageDebugMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data: dict ):
        if isinstance(event, Message) and event.chat.type in ["group", "supergroup"]:
            user_info = f"User ID: {event.from_user.id}" if event.from_user else "User: N/A"
            reply_info = ""
            if event.reply_to_message:
                reply_to_user_info = f"ReplyToUser ID: {event.reply_to_message.from_user.id}" if event.reply_to_message.from_user else "ReplyToUser: N/A"
                replied_text_content = event.reply_to_message.text or event.reply_to_message.caption or '[Non-Text Content]'
                reply_info = (f", ReplyToMsgID: {event.reply_to_message.message_id}, "
                              f"{reply_to_user_info}, ReplyToText: '{replied_text_content}'")
            logging.warning(f"[GROUP_MSG_DEBUG] ChatID: {event.chat.id}, ThreadID: {event.message_thread_id}, {user_info}, MsgText: '{event.text or event.caption}', Entities: {event.entities or event.caption_entities}{reply_info}")
        return await handler(event, data)

router = Router()
router.message.outer_middleware(GroupMessageDebugMiddleware())
user_languages_cache: Dict[int, str] = {}
user_selected_model_cache: Dict[int, str] = {}
media_group_cache: Dict[str, Dict[str, Any]] = {}
ALBUM_PROCESSING_TIMEOUT: float = 2.5

async def get_user_language(user_id: int, telegram_lang_code: Optional[str] = None) -> str:
    cached_lang = user_languages_cache.get(user_id);
    if cached_lang: return cached_lang
    if FEATURE_ENABLE_DATABASE:
        db_lang = await get_user_language_from_db(user_id)
        if db_lang and db_lang in AVAILABLE_LANGUAGES: user_languages_cache[user_id] = db_lang; return db_lang
    if telegram_lang_code:
        lang_prefix = telegram_lang_code.split('-')[0]
        if lang_prefix in AVAILABLE_LANGUAGES: user_languages_cache[user_id] = lang_prefix; return lang_prefix
    return DEFAULT_LANGUAGE

async def get_active_gemini_model_for_user(user_id: int) -> str:
    cached_model = user_selected_model_cache.get(user_id)
    if cached_model and cached_model in AVAILABLE_GEMINI_MODELS: return cached_model
    if FEATURE_ENABLE_DATABASE:
        db_model = await get_user_selected_model(user_id)
        if db_model and db_model in AVAILABLE_GEMINI_MODELS: user_selected_model_cache[user_id] = db_model; return db_model
    if DEFAULT_GEMINI_MODEL_ID in AVAILABLE_GEMINI_MODELS :
        user_selected_model_cache[user_id] = DEFAULT_GEMINI_MODEL_ID; return DEFAULT_GEMINI_MODEL_ID
    if AVAILABLE_GEMINI_MODELS:
        fallback_model = next(iter(AVAILABLE_GEMINI_MODELS))
        user_selected_model_cache[user_id] = fallback_model
        return fallback_model
    logging.error("AVAILABLE_GEMINI_MODELS kosong atau DEFAULT_GEMINI_MODEL_ID tidak valid!"); raise ValueError("Tidak ada model Gemini yang valid.")

@router.message(CommandStart())
async def handle_start(message: Message):
    user_id = message.from_user.id; user_lang = await get_user_language(user_id, message.from_user.language_code)
    logging.info(f"User {user_id} di chat {message.chat.id} memulai bot. Bahasa: {user_lang}")
    if FEATURE_ENABLE_DAILY_CHAT_LIMIT and FEATURE_ENABLE_DATABASE: await check_and_update_chat_limit(user_id, DAILY_CHAT_LIMIT_PER_USER)
    await message.answer(_("welcome_message", user_lang))
    await message.answer(_("language_suggestion", user_lang, default_return_key_on_missing=True))

@router.message(Command("help", "bantuan"))
async def handle_help_command(message: Message, bot: Bot):
    user_id = message.from_user.id
    user_lang = await get_user_language(user_id, message.from_user.language_code)
    bot_info = await bot.get_me()
    bot_username = bot_info.username

    help_text_parts = [
        _("help_title", user_lang),
        _("help_command_start", user_lang),
        _("help_command_help", user_lang),
        _("help_command_lang", user_lang),
        _("help_command_settings", user_lang),
        _("help_command_status", user_lang),
        _("help_command_newchat", user_lang),
        _("help_interaction_intro", user_lang),
        _("help_interaction_text", user_lang)
    ]
    if FEATURE_ENABLE_IMAGE_UNDERSTANDING:
        help_text_parts.append(_("help_interaction_image", user_lang))
    if FEATURE_ENABLE_AUDIO_UNDERSTANDING:
        help_text_parts.append(_("help_interaction_audio", user_lang))
    if FEATURE_ENABLE_DOCUMENT_UNDERSTANDING:
        help_text_parts.append(_("help_interaction_document", user_lang))

    help_text_parts.append(_("help_footer", user_lang))

    help_text_markdown = "\n".join(help_text_parts)

    builder = InlineKeyboardBuilder()
    builder.button(
        text=_("help_add_to_group_button", user_lang),
        url=f"https://t.me/{bot_username}?startgroup=true"
    )

    await message.answer(
        help_text_markdown,
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True
    )

@router.message(Command("lang"))
async def handle_language_command(message: Message):
    user_id = message.from_user.id; user_lang = await get_user_language(user_id, message.from_user.language_code)
    logging.info(f"User {user_id} di chat {message.chat.id} /lang. Bahasa: {user_lang}")
    builder = InlineKeyboardBuilder();
    for code, name in AVAILABLE_LANGUAGES.items(): builder.button(text=name, callback_data=f"set_lang:{code}")
    builder.adjust(1); await message.answer(_("ask_language", user_lang), reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("set_lang:"))
async def process_language_selection(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id; selected_lang_code = callback_query.data.split(":")[1]
    logging.info(f"User {user_id} di chat {callback_query.message.chat.id} pilih bahasa: {selected_lang_code}")
    if selected_lang_code in AVAILABLE_LANGUAGES:
        user_languages_cache[user_id] = selected_lang_code
        if FEATURE_ENABLE_DATABASE:
            if not await set_user_language_in_db(user_id, selected_lang_code): logging.warning(f"Gagal simpan bahasa DB user {user_id}.")
        await callback_query.answer(_("language_changed", selected_lang_code), show_alert=True)
        try: await callback_query.message.edit_text(_("language_changed", selected_lang_code) + "\n" + _("welcome_message", selected_lang_code))
        except Exception as e: logging.info(f"Gagal edit pesan ganti bahasa: {e}."); await callback_query.message.answer(_("language_changed", selected_lang_code))
    else: await callback_query.answer("Error: Invalid language selected.", show_alert=True)

@router.message(Command("newchat", "mulaiulang"))
async def handle_new_chat_command(message: Message):
    user_id = message.from_user.id; user_lang = await get_user_language(user_id, message.from_user.language_code)
    logging.info(f"User {user_id} di chat {message.chat.id} /newchat: {message.text}")
    if not (FEATURE_ENABLE_CONVERSATION_HISTORY and FEATURE_ENABLE_DATABASE): await message.reply(_("history_feature_disabled", user_lang)); return
    if await clear_user_conversation_history(user_id): await message.reply(_("new_chat_started", user_lang))
    else: await message.reply(_("new_chat_failed", user_lang))

@router.message(Command("settings", "pengaturan"))
async def handle_settings_command(message: Message):
    user_id = message.from_user.id; user_lang = await get_user_language(user_id, message.from_user.language_code)
    current_model_id = await get_active_gemini_model_for_user(user_id)
    current_model_name = AVAILABLE_GEMINI_MODELS.get(current_model_id, _("settings_no_model_selected", user_lang, default_model_name=DEFAULT_GEMINI_MODEL_ID))
    text = _("settings_title", user_lang) + "\n\n" + _("settings_select_model_prompt", user_lang, current_model_name=local_escape_markdown_v1(current_model_name))
    builder = InlineKeyboardBuilder()
    for model_id_loop, friendly_name in AVAILABLE_GEMINI_MODELS.items():
        display_name = f"✅ {friendly_name}" if model_id_loop == current_model_id else friendly_name
        builder.button(text=display_name, callback_data=f"select_model:{model_id_loop}")
    builder.adjust(1); await message.answer(text, reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("select_model:"))
async def process_model_selection(callback_query: CallbackQuery):
    user_id = callback_query.from_user.id; user_lang = await get_user_language(user_id, callback_query.from_user.language_code)
    try: selected_model_id = callback_query.data.split(":")[1]
    except IndexError: logging.warning(f"Callback model tidak valid: {callback_query.data} user {user_id}"); await callback_query.answer(_("settings_model_invalid", user_lang), show_alert=True); return
    logging.info(f"User {user_id} di chat {callback_query.message.chat.id} pilih model: {selected_model_id}")
    if selected_model_id not in AVAILABLE_GEMINI_MODELS: await callback_query.answer(_("settings_model_invalid", user_lang), show_alert=True); return
    success_db_set = False
    if FEATURE_ENABLE_DATABASE: success_db_set = await set_user_selected_model(user_id, selected_model_id)
    if success_db_set or not FEATURE_ENABLE_DATABASE:
        user_selected_model_cache[user_id] = selected_model_id
        new_model_friendly_name = AVAILABLE_GEMINI_MODELS.get(selected_model_id, selected_model_id)
        await callback_query.answer(_("settings_model_changed_success", user_lang, new_model_name=local_escape_markdown_v1(new_model_friendly_name)),show_alert=True)
        current_model_id_after_change = await get_active_gemini_model_for_user(user_id)
        current_model_name_after_change = AVAILABLE_GEMINI_MODELS.get(current_model_id_after_change, _("settings_no_model_selected", user_lang, default_model_name=DEFAULT_GEMINI_MODEL_ID))
        new_text = _("settings_title", user_lang) + "\n\n" + _("settings_select_model_prompt", user_lang, current_model_name=local_escape_markdown_v1(current_model_name_after_change))
        builder = InlineKeyboardBuilder()
        for mid_loop, fname_loop in AVAILABLE_GEMINI_MODELS.items():
            display_name = f"✅ {fname_loop}" if mid_loop == current_model_id_after_change else fname_loop
            builder.button(text=display_name, callback_data=f"select_model:{mid_loop}")
        builder.adjust(1)
        try: await callback_query.message.edit_text(new_text, reply_markup=builder.as_markup())
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower(): logging.info(f"Pesan settings tidak diubah, konten sama.")
            else: logging.info(f"Gagal edit pesan settings: {e}")
        except Exception as e_general: logging.info(f"Gagal edit pesan settings (umum): {e_general}")
    else: await callback_query.answer(_("settings_model_selection_failed", user_lang), show_alert=True)

@router.message(Command("status", "info"))
async def handle_status_command(message: Message):
    user_id = message.from_user.id; user_lang = await get_user_language(user_id, message.from_user.language_code)
    logging.info(f"User {user_id} di chat {message.chat.id} /status.")
    status_parts = [_("user_status_title", user_lang), ""]
    lang_name = AVAILABLE_LANGUAGES.get(user_lang, user_lang)
    status_parts.append(_("status_language", user_lang, language_name=lang_name))
    if FEATURE_ENABLE_GEMINI:
        active_model_id = await get_active_gemini_model_for_user(user_id)
        model_friendly_name = AVAILABLE_GEMINI_MODELS.get(active_model_id, active_model_id if active_model_id else _("settings_no_model_selected", user_lang, default_model_name=DEFAULT_GEMINI_MODEL_ID))
        status_parts.append(_("status_active_model", user_lang, model_name=local_escape_markdown_v1(model_friendly_name)))
    if FEATURE_ENABLE_DAILY_CHAT_LIMIT and FEATURE_ENABLE_DATABASE:
        chat_stats = await get_user_chat_status_info(user_id)
        if chat_stats:
            count_from_db = chat_stats.get("daily_chat_count", 0); last_reset_db = chat_stats.get("last_chat_reset_date")
            today_date = date.today(); chats_used_today = 0
            if last_reset_db and last_reset_db == today_date: chats_used_today = count_from_db
            remaining_chats = DAILY_CHAT_LIMIT_PER_USER - chats_used_today
            if remaining_chats < 0: remaining_chats = 0
            if chats_used_today >= DAILY_CHAT_LIMIT_PER_USER:
                status_parts.append(_("status_daily_chats_limit_reached", user_lang, chats_used=chats_used_today, limit_count=DAILY_CHAT_LIMIT_PER_USER))
            else:
                status_parts.append(_("status_daily_chats_info_with_limit", user_lang, chats_used=chats_used_today, remaining_chats=remaining_chats, limit_count=DAILY_CHAT_LIMIT_PER_USER))
        else:
             status_parts.append(_("status_daily_chats_info_with_limit", user_lang, chats_used=0, remaining_chats=DAILY_CHAT_LIMIT_PER_USER, limit_count=DAILY_CHAT_LIMIT_PER_USER))
             logging.info(f"Data chat_stats tidak ada/error user {user_id} pada /status, tampil default.")
    elif FEATURE_ENABLE_GEMINI: status_parts.append(_("status_daily_chats_unlimited", user_lang))
    status_message_text = "\n".join(status_parts)
    try: await message.answer(status_message_text)
    except Exception as e: logging.error(f"Gagal kirim status user {user_id}: {e}"); await message.answer(_("status_failed_to_fetch", user_lang), parse_mode=None)

async def send_text_response_possibly_chunked(message: Message, user_lang: str, text_response: str, chat_type_log_prefix: str = ""):
    if len(text_response) > TELEGRAM_MAX_MESSAGE_LENGTH:
        logging.info(f"{chat_type_log_prefix}Respons teks AI terlalu panjang ({len(text_response)} karakter), akan dipecah.")
        chunks = split_long_message(text_response, TELEGRAM_MAX_MESSAGE_LENGTH)
        logging.info(f"{chat_type_log_prefix}Jumlah chunk: {len(chunks)}")
        for i, chunk_original in enumerate(chunks):
            chunk_to_send = ensure_valid_markdown(chunk_original)
            logging.debug(f"{chat_type_log_prefix}Memproses chunk {i+1}/{len(chunks)}. Panjang asli: {len(chunk_original)}, Panjang setelah ensure_valid_markdown: {len(chunk_to_send)}. Isi (awal): '{chunk_to_send[:100]}'")
            if not chunk_to_send.strip():
                logging.info(f"{chat_type_log_prefix}Chunk {i+1} kosong setelah validasi markdown, dilewati.")
                continue
            try:
                logging.debug(f"{chat_type_log_prefix}Mengirim chunk {i+1}/{len(chunks)} dengan Markdown.")
                send_method = message.answer
                if i == 0:
                    if not message.reply_to_message or message.reply_to_message.from_user.id != message.get_bot().id:
                        send_method = message.reply
                await send_method(chunk_to_send, parse_mode=ParseMode.MARKDOWN)
                if i < len(chunks) - 1:
                    await asyncio.sleep(0.5)
            except TelegramBadRequest:
                logging.warning(f"{chat_type_log_prefix}Gagal kirim chunk {i+1}/{len(chunks)} dengan Markdown, fallback ke teks biasa.")
                try:
                    send_method_fallback = message.answer
                    if i == 0:
                        if not message.reply_to_message or message.reply_to_message.from_user.id != message.get_bot().id:
                            send_method_fallback = message.reply
                    await send_method_fallback(chunk_original, parse_mode=None)
                except Exception as e_chunk_fallback:
                    logging.error(f"{chat_type_log_prefix}Gagal total kirim chunk {i+1}/{len(chunks)} (fallback): {e_chunk_fallback}", exc_info=True)
                    await message.answer(_("gemini_error_sending_response", user_lang))
                    break
            except Exception as e_chunk_general:
                logging.error(f"{chat_type_log_prefix}Error umum kirim chunk {i+1}/{len(chunks)}: {e_chunk_general}", exc_info=True)
                await message.answer(_("gemini_error_sending_response", user_lang))
                break
    else:
        text_to_send = ensure_valid_markdown(text_response)
        if not text_to_send.strip():
             logging.info(f"{chat_type_log_prefix}Respons teks kosong setelah validasi markdown.")
             await message.reply(_("gemini_empty_response", user_lang), parse_mode=None)
             return
        try:
            await message.reply(text_to_send, parse_mode=ParseMode.MARKDOWN)
        except TelegramBadRequest:
            logging.warning(f"{chat_type_log_prefix}Gagal kirim balasan AI dengan Markdown, fallback ke teks biasa.")
            try:
                await message.reply(text_response, parse_mode=None)
            except Exception as e_fallback:
                logging.error(f"{chat_type_log_prefix}Gagal total kirim balasan AI (fallback): {e_fallback}", exc_info=True)
                await message.reply(_("gemini_error_sending_response", user_lang))
        except Exception as e_general:
            logging.error(f"{chat_type_log_prefix}Error umum kirim AI response: {e_general}", exc_info=True)
            await message.reply(_("gemini_error_sending_response", user_lang))

async def process_ai_interaction(
    message: Message, bot: Bot, user_id: int, user_lang: str,
    active_model_id: str, prompt_text: Optional[str],
    chat_type_log_prefix: str = "",
    image_data_list_for_input: Optional[List[bytes]] = None,
    image_mime_types_list_for_input: Optional[List[str]] = None,
    audio_file_bytes_for_input: Optional[bytes] = None,
    audio_mime_type_for_input: Optional[str] = None,
    doc_file_bytes_for_input: Optional[bytes] = None, # Parameter baru
    doc_mime_type_for_input: Optional[str] = None    # Parameter baru
):
    if FEATURE_ENABLE_DAILY_CHAT_LIMIT and FEATURE_ENABLE_DATABASE:
        can_chat, remaining_chats = await check_and_update_chat_limit(user_id, DAILY_CHAT_LIMIT_PER_USER)
        if not can_chat:
            await message.reply(_("chat_limit_reached", user_lang, limit_count=DAILY_CHAT_LIMIT_PER_USER))
            log_prompt_display = prompt_text or \
                                 (_("default_image_prompt", user_lang) if image_data_list_for_input else \
                                 (_("default_audio_prompt_describe", user_lang) if audio_file_bytes_for_input else \
                                 (_("default_document_prompt_summarize", user_lang) if doc_file_bytes_for_input else \
                                 "[Konten tidak ada]")))
            logging.info(f"{chat_type_log_prefix}User {user_id} (chat {message.chat.id}) limit chat. Pesan ('{log_prompt_display[:30]}...') diblokir.")
            return

    if not FEATURE_ENABLE_GEMINI:
        await message.reply(_("ai_feature_disabled", user_lang)); return

    actual_prompt_text_for_gemini = prompt_text if prompt_text and prompt_text.strip() else None

    if image_data_list_for_input and FEATURE_ENABLE_IMAGE_UNDERSTANDING and not actual_prompt_text_for_gemini:
        actual_prompt_text_for_gemini = _("default_image_prompt", user_lang)

    has_text_input = bool(actual_prompt_text_for_gemini)
    has_image_input = bool(image_data_list_for_input and FEATURE_ENABLE_IMAGE_UNDERSTANDING)
    has_audio_input = bool(audio_file_bytes_for_input and FEATURE_ENABLE_AUDIO_UNDERSTANDING)
    has_doc_input = bool(doc_file_bytes_for_input and FEATURE_ENABLE_DOCUMENT_UNDERSTANDING)

    if not has_text_input and not has_image_input and not has_audio_input and not has_doc_input:
        logging.warning(f"{chat_type_log_prefix}Tidak ada prompt/media valid untuk user {user_id} model {active_model_id}")
        await message.reply(_("gemini_no_content_to_send", user_lang), parse_mode=None); return

    processing_key = "gemini_processing"
    if has_doc_input:
        processing_key = "processing_document_prompt"
    elif has_audio_input :
        processing_key = "processing_audio_prompt"
    elif has_image_input :
        processing_key = "processing_image_prompt"

    processing_text_str = _(processing_key, user_lang, default_return_key_on_missing=True)
    if processing_text_str == processing_key :
        if has_doc_input: processing_text_str = _("processing_document_prompt", user_lang, default_return_key_on_missing=True)
        elif has_audio_input: processing_text_str = _("processing_audio_prompt", user_lang, default_return_key_on_missing=True)
        elif has_image_input: processing_text_str = _("processing_image_prompt", user_lang, default_return_key_on_missing=True)
        else: processing_text_str = _("gemini_processing", user_lang, default_return_key_on_missing=True)
        if processing_text_str.startswith("TR_MISSING") or processing_text_str == processing_key:
             processing_text_str = "🧠 Thinking..."

    processing_msg = None
    try: processing_msg = await message.reply(processing_text_str, parse_mode=None)
    except Exception as e: logging.error(f"{chat_type_log_prefix}Error send processing: {e}", exc_info=True)

    gemini_history_for_api: List[ContentDict] = []
    if FEATURE_ENABLE_CONVERSATION_HISTORY and FEATURE_ENABLE_DATABASE:
        raw_db_history = await get_conversation_history(user_id, limit=GEMINI_CONVERSATION_HISTORY_MAX_MESSAGES)
        if raw_db_history:
            for msg_from_db in raw_db_history: gemini_history_for_api.append({"role": msg_from_db["role"], "parts": [{"text": msg_from_db["content"]}]})

    ai_response_raw_str: str = await get_gemini_response(
        prompt_text=actual_prompt_text_for_gemini, model_id=active_model_id, user_lang=user_lang,
        history=gemini_history_for_api if gemini_history_for_api else None,
        image_bytes_list=image_data_list_for_input,
        image_mime_types_list=image_mime_types_list_for_input,
        audio_file_bytes=audio_file_bytes_for_input,
        audio_mime_type=audio_mime_type_for_input,
        doc_file_bytes=doc_file_bytes_for_input, # Teruskan data dokumen
        doc_mime_type=doc_mime_type_for_input   # Teruskan tipe MIME dokumen
    )
    logging.info(f"{chat_type_log_prefix}Respons Gemini (string) model '{active_model_id}' user {user_id}. Panjang: {len(ai_response_raw_str or '')}")

    if processing_msg:
        try: await processing_msg.delete()
        except Exception: pass

    possible_error_keys_with_vars = {
        "gemini_request_blocked": ["reasons"],
        "gemini_model_not_found": ["model_id"],
        "gemini_error_contacting": ["error_message"],
        "audio_format_not_supported_gemini": ["mime_type"],
        "document_format_not_supported": ["mime_type"], # Kunci baru
        "audio_too_large": ["max_size_mb"], # Kunci yang sudah ada
        "document_too_large": ["max_size_mb"] # Kunci baru
    }
    possible_error_keys_no_vars = [
        "ai_feature_disabled", "gemini_api_key_not_configured", "gemini_no_content_to_send",
        "gemini_no_valid_response", "gemini_empty_response", "error_processing_audio_data",
        "error_determining_audio_mime", "error_processing_document_data" # Kunci baru
    ]

    is_error_response = False
    translated_error_test_values = {}
    # Dapatkan semua kemungkinan string error yang sudah diterjemahkan
    for err_key, var_names in possible_error_keys_with_vars.items():
        dummy_vars = {var_name: "..." for var_name in var_names}
        translated_error_test_values[err_key] = _(err_key, user_lang, **dummy_vars, default_return_key_on_missing=True)
        # Juga tambahkan versi tanpa variabel jika terjemahan mungkin tidak memiliki placeholder
        translated_error_test_values[f"{err_key}_no_vars"] = _(err_key, user_lang, default_return_key_on_missing=True)

    for err_key in possible_error_keys_no_vars:
        translated_error_test_values[err_key] = _(err_key, user_lang, default_return_key_on_missing=True)

    if ai_response_raw_str in translated_error_test_values.values():
        is_error_response = True

    if is_error_response:
        await message.reply(ai_response_raw_str, parse_mode=None)
        return

    if FEATURE_ENABLE_CONVERSATION_HISTORY and FEATURE_ENABLE_DATABASE:
        prompt_saved_to_history = actual_prompt_text_for_gemini
        if image_data_list_for_input and FEATURE_ENABLE_IMAGE_UNDERSTANDING:
            img_count = len(image_data_list_for_input)
            base_prompt = prompt_text if prompt_text else _('default_image_prompt', user_lang)
            prompt_saved_to_history = f"{base_prompt} [{img_count} image(s) processed]"
        elif audio_file_bytes_for_input and FEATURE_ENABLE_AUDIO_UNDERSTANDING:
            base_prompt = prompt_text if prompt_text else _('default_audio_prompt_describe', user_lang)
            prompt_saved_to_history = f"{base_prompt} [audio processed]"
        elif doc_file_bytes_for_input and FEATURE_ENABLE_DOCUMENT_UNDERSTANDING:
            base_prompt = prompt_text if prompt_text else _('default_document_prompt_summarize', user_lang)
            prompt_saved_to_history = f"{base_prompt} [document processed]"


        if prompt_saved_to_history: await add_message_to_history(user_id, role="user", content=prompt_saved_to_history)
        if ai_response_raw_str: await add_message_to_history(user_id, role="model", content=ai_response_raw_str)

    await send_text_response_possibly_chunked(message, user_lang, ai_response_raw_str, chat_type_log_prefix)


@router.message(F.document, ManualChatTypeFilter(chat_type=["private", "group", "supergroup"]))
async def handle_document_message(message: Message, bot: Bot):
    if not message.from_user: return
    user_id = message.from_user.id
    user_lang = await get_user_language(user_id, message.from_user.language_code)

    if not FEATURE_ENABLE_DOCUMENT_UNDERSTANDING:
        logging.info(f"Fitur pemahaman dokumen dinonaktifkan. Dokumen dari user {user_id} diabaikan.")
        await message.reply(_("document_understanding_disabled", user_lang))
        return

    if not message.document:
        logging.warning(f"Tidak ada objek dokumen pada pesan dari user {user_id} meskipun filter lolos.")
        return

    doc_obj: Document = message.document
    actual_doc_mime_type: Optional[str] = doc_obj.mime_type

    logging.info(f"Menerima dokumen dari user {user_id}. File ID: {doc_obj.file_id}, Nama: {doc_obj.file_name}, MIME: {actual_doc_mime_type}, Size: {doc_obj.file_size}")

    if actual_doc_mime_type not in SUPPORTED_DOCUMENT_MIME_TYPES_BOT:
        logging.warning(f"Format dokumen '{actual_doc_mime_type}' tidak didukung oleh bot saat ini (hanya PDF).")
        await message.reply(_("document_format_not_supported", user_lang, mime_type=actual_doc_mime_type or "tidak diketahui"))
        return

    if doc_obj.file_size > MAX_DOCUMENT_FILE_SIZE_BYTES_BOT:
        logging.warning(f"File dokumen dari user {user_id} terlalu besar: {doc_obj.file_size} bytes. Batas: {MAX_DOCUMENT_FILE_SIZE_BYTES_BOT} bytes.")
        await message.reply(_("document_too_large", user_lang, max_size_mb=int(MAX_DOCUMENT_FILE_SIZE_BYTES_BOT / (1024*1024))))
        return

    doc_file_bytes: Optional[bytes] = None
    try:
        file_info = await bot.get_file(doc_obj.file_id)
        downloaded_bytes_io: io.BytesIO = await bot.download_file(file_info.file_path)
        doc_file_bytes = downloaded_bytes_io.read()
        downloaded_bytes_io.close()
    except Exception as e:
        logging.error(f"Gagal download dokumen user {user_id}: {e}", exc_info=True)
        await message.reply(_("error_downloading_document", user_lang))
        return

    if not doc_file_bytes:
        logging.error(f"Byte dokumen kosong setelah download untuk user {user_id}.")
        await message.reply(_("error_processing_document_data", user_lang))
        return

    prompt_text = message.caption.strip() if message.caption else None
    if not prompt_text:
        prompt_text = _("default_document_prompt_summarize", user_lang)

    active_model_id = await get_active_gemini_model_for_user(user_id)
    logging.info(f"[DOC] User {user_id} (model: {active_model_id}). Prompt: '{prompt_text}'. Dokumen MIME: {actual_doc_mime_type}")

    await process_ai_interaction(
        message, bot, user_id, user_lang, active_model_id,
        prompt_text=prompt_text,
        chat_type_log_prefix="[DOC] ",
        doc_file_bytes_for_input=doc_file_bytes,
        doc_mime_type_for_input=actual_doc_mime_type
    )

@router.message(F.audio | F.voice, ManualChatTypeFilter(chat_type=["private", "group", "supergroup"]))
async def handle_audio_message(message: Message, bot: Bot):
    if not message.from_user: return
    user_id = message.from_user.id
    user_lang = await get_user_language(user_id, message.from_user.language_code)

    if not FEATURE_ENABLE_AUDIO_UNDERSTANDING:
        logging.info(f"Fitur pemahaman audio dinonaktifkan. Pesan audio dari user {user_id} diabaikan.")
        await message.reply(_("audio_understanding_disabled", user_lang))
        return

    active_model_id = await get_active_gemini_model_for_user(user_id)
    audio_obj: Optional[Union[Audio, Voice]] = message.audio or message.voice

    if not audio_obj:
        logging.warning(f"Tidak ada objek audio/voice pada pesan dari user {user_id} meskipun filter lolos.")
        return 

    if audio_obj.file_size > MAX_AUDIO_FILE_SIZE_BYTES_BOT:
        logging.warning(f"File audio dari user {user_id} terlalu besar: {audio_obj.file_size} bytes. Batas: {MAX_AUDIO_FILE_SIZE_BYTES_BOT} bytes.")
        await message.reply(_("audio_too_large", user_lang, max_size_mb=int(MAX_AUDIO_FILE_SIZE_BYTES_BOT / (1024*1024))))
        return

    audio_file_bytes: Optional[bytes] = None
    actual_audio_mime_type: Optional[str] = audio_obj.mime_type

    logging.info(f"Menerima audio dari user {user_id}. File ID: {audio_obj.file_id}, MIME awal: {actual_audio_mime_type}, Size: {audio_obj.file_size}, Duration: {audio_obj.duration}s")

    try:
        file_info = await bot.get_file(audio_obj.file_id)
        if not actual_audio_mime_type and file_info.file_path:
            ext = os.path.splitext(file_info.file_path)[1].lower()
            if ext in [".oga", ".ogg"]: actual_audio_mime_type = "audio/ogg"
            elif ext == ".mp3": actual_audio_mime_type = "audio/mpeg" 
            elif ext == ".wav": actual_audio_mime_type = "audio/wav"
            elif ext == ".m4a": actual_audio_mime_type = "audio/aac"
            elif ext == ".flac": actual_audio_mime_type = "audio/flac"
            elif ext == ".aiff": actual_audio_mime_type = "audio/aiff"
            logging.info(f"Inferensi MIME type dari ekstensi file: {ext} -> {actual_audio_mime_type}")

        if not actual_audio_mime_type and isinstance(audio_obj, Voice):
             actual_audio_mime_type = "audio/ogg"
             logging.info(f"Pesan suara tanpa MIME, diasumsikan: {actual_audio_mime_type}")

        if not actual_audio_mime_type:
            logging.warning(f"Tidak bisa menentukan MIME type untuk audio dari user {user_id}")
            await message.reply(_("error_determining_audio_mime", user_lang))
            return

        downloaded_bytes_io: io.BytesIO = await bot.download_file(file_info.file_path)
        audio_file_bytes = downloaded_bytes_io.read()
        downloaded_bytes_io.close()
    except Exception as e:
        logging.error(f"Gagal download audio user {user_id}: {e}", exc_info=True)
        await message.reply(_("error_downloading_audio", user_lang))
        return

    if not audio_file_bytes:
        logging.error(f"Byte audio kosong setelah download untuk user {user_id}.")
        await message.reply(_("error_processing_audio_data", user_lang))
        return

    prompt_text = message.caption.strip() if message.caption else None
    if not prompt_text:
        prompt_text = _("default_audio_prompt_describe", user_lang) 

    normalized_mime_check = actual_audio_mime_type.lower()
    if normalized_mime_check == "audio/mpeg": normalized_mime_check = "audio/mp3"

    if normalized_mime_check not in SUPPORTED_AUDIO_MIME_TYPES_GEMINI:
        logging.warning(f"Format audio '{actual_audio_mime_type}' (normal: '{normalized_mime_check}') tidak didukung oleh Gemini (daftar: {SUPPORTED_AUDIO_MIME_TYPES_GEMINI}).")
        await message.reply(_("audio_format_not_supported_gemini", user_lang, mime_type=actual_audio_mime_type))
        return

    logging.info(f"[AUDIO] User {user_id} (model: {active_model_id}). Prompt: '{prompt_text}'. Audio MIME final: {actual_audio_mime_type}")

    await process_ai_interaction(
        message, bot, user_id, user_lang, active_model_id,
        prompt_text=prompt_text,
        chat_type_log_prefix="[AUDIO] ",
        audio_file_bytes_for_input=audio_file_bytes,
        audio_mime_type_for_input=actual_audio_mime_type
    )

async def process_complete_album(media_group_id: str, bot: Bot):
    await asyncio.sleep(ALBUM_PROCESSING_TIMEOUT)
    if media_group_id not in media_group_cache: return
    album_data = media_group_cache.pop(media_group_id)
    messages_in_album: List[Message] = album_data.get("messages", [])
    user_id = album_data.get("user_id")
    initial_message = album_data.get("initial_message_for_reply")

    if not messages_in_album or not initial_message or not user_id or not initial_message.from_user :
        logging.warning(f"Album {media_group_id} data tidak lengkap atau initial_message/user_id tidak valid. Mengabaikan.")
        return

    user_lang = await get_user_language(user_id, initial_message.from_user.language_code)
    active_model_id = await get_active_gemini_model_for_user(user_id)

    album_caption: Optional[str] = None
    for msg_in_album in messages_in_album:
        if msg_in_album.caption: album_caption = msg_in_album.caption.strip(); break

    images_to_process_from_album: List[Message] = []
    for msg in messages_in_album:
        if msg.photo: images_to_process_from_album.append(msg)

    original_photo_count = len(images_to_process_from_album)
    notified_limit = False
    if original_photo_count > MAX_IMAGES_PER_ALBUM:
        logging.info(f"Album {media_group_id} ({original_photo_count} gbr) > batas {MAX_IMAGES_PER_ALBUM}. Proses {MAX_IMAGES_PER_ALBUM} gbr.")
        try:
            await initial_message.reply(_("album_image_limit_notice", user_lang, max_images=MAX_IMAGES_PER_ALBUM), parse_mode=None)
            notified_limit = True
        except Exception as e_notify: logging.error(f"Gagal kirim notif batas album: {e_notify}")
        images_to_process_from_album = images_to_process_from_album[:MAX_IMAGES_PER_ALBUM]

    logging.info(f"Proses album {media_group_id} user {user_id} dengan {len(images_to_process_from_album)} media (setelah limit).")

    downloaded_image_bytes_list: List[bytes] = []
    downloaded_mime_types_list: List[str] = []

    async def download_photo_task(photo_size: PhotoSize):
        try:
            file_info = await bot.get_file(photo_size.file_id)
            actual_mime_type = "image/jpeg" 
            if hasattr(file_info, 'mime_type') and file_info.mime_type:
                actual_mime_type = file_info.mime_type
            elif file_info.file_path:
                ext_map = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
                file_ext = os.path.splitext(file_info.file_path)[1].lower()
                actual_mime_type = ext_map.get(file_ext, actual_mime_type)
            downloaded_bytes_io: io.BytesIO = await bot.download_file(file_info.file_path)
            image_bytes = downloaded_bytes_io.read(); downloaded_bytes_io.close()
            return {"bytes": image_bytes, "mime_type": actual_mime_type}
        except Exception as e: logging.error(f"Gagal download foto album (file_id: {photo_size.file_id}): {e}"); return None

    download_tasks = []
    for msg_with_photo in images_to_process_from_album:
        if msg_with_photo.photo: download_tasks.append(download_photo_task(msg_with_photo.photo[-1]))

    downloaded_image_results = await asyncio.gather(*download_tasks)
    for result in downloaded_image_results:
        if result:
            downloaded_image_bytes_list.append(result['bytes'])
            downloaded_mime_types_list.append(result['mime_type'])

    if not downloaded_image_bytes_list:
        logging.warning(f"Tidak ada gambar di-download untuk album {media_group_id} user {user_id}.")
        if not notified_limit or original_photo_count == 0 :
            await initial_message.reply(_("error_downloading_image", user_lang))
        return

    await process_ai_interaction(
        message=initial_message, bot=bot, user_id=user_id, user_lang=user_lang,
        active_model_id=active_model_id, prompt_text=album_caption,
        chat_type_log_prefix="[ALBUM] ",
        image_data_list_for_input=downloaded_image_bytes_list,
        image_mime_types_list_for_input=downloaded_mime_types_list
    )

@router.message((F.photo | F.video) & F.media_group_id, ManualChatTypeFilter(chat_type=["private", "group", "supergroup"]))
async def handle_media_album_part(message: Message, bot: Bot):
    if not FEATURE_ENABLE_IMAGE_UNDERSTANDING: return
    if not message.from_user: return

    media_group_id = str(message.media_group_id)
    user_id = message.from_user.id
    if media_group_id not in media_group_cache:
        logging.debug(f"Buffer baru album {media_group_id} user {user_id}")
        media_group_cache[media_group_id] = {
            "messages": [], "timer": None, "user_id": user_id,
            "initial_message_for_reply": message
        }
    if message.photo: media_group_cache[media_group_id]["messages"].append(message)
    elif message.video and FEATURE_ENABLE_IMAGE_UNDERSTANDING:
        logging.info(f"Menerima video di album {media_group_id}, tidak diproses untuk Gemini.")

    if media_group_cache[media_group_id]["timer"]:
        media_group_cache[media_group_id]["timer"].cancel()
    media_group_cache[media_group_id]["timer"] = asyncio.create_task(
        process_complete_album(media_group_id, bot)
    )

@router.message(F.photo & ~F.media_group_id, ManualChatTypeFilter(chat_type=["private", "group", "supergroup"]))
async def handle_single_photo_message(message: Message, bot: Bot):
    if not message.from_user: return
    user_id = message.from_user.id
    user_lang = await get_user_language(user_id, message.from_user.language_code)
    active_model_id = await get_active_gemini_model_for_user(user_id)

    if not FEATURE_ENABLE_IMAGE_UNDERSTANDING:
        if message.caption: await message.reply(_("image_understanding_disabled", user_lang))
        return

    image_bytes_for_understanding: Optional[bytes] = None
    mime_type_for_understanding: Optional[str] = "image/jpeg"
    if message.photo:
        logging.info(f"Menerima foto tunggal user {user_id} untuk pemahaman. Download...")
        try:
            photo_size: PhotoSize = message.photo[-1]
            file_info = await bot.get_file(photo_size.file_id)
            downloaded_bytes_io: io.BytesIO = await bot.download_file(file_info.file_path)
            image_bytes_for_understanding = downloaded_bytes_io.read(); downloaded_bytes_io.close()
            if hasattr(file_info, 'mime_type') and file_info.mime_type: mime_type_for_understanding = file_info.mime_type
        except Exception as e:
            logging.error(f"Gagal download foto tunggal user {user_id} (pemahaman): {e}", exc_info=True)
            await message.reply(_("error_downloading_image", user_lang)); return

    if not image_bytes_for_understanding: return
    prompt_text = message.caption.strip() if message.caption else None
    logging.info(f"[FOTO_SINGLE_UNDERSTAND] User {user_id} (model: {active_model_id}) foto. Caption: '{prompt_text[:70] if prompt_text else '[NoCap]'}'")

    await process_ai_interaction(
        message, bot, user_id, user_lang, active_model_id, prompt_text=prompt_text,
        chat_type_log_prefix="[FOTO_SINGLE_UNDERSTAND] ",
        image_data_list_for_input=[image_bytes_for_understanding],
        image_mime_types_list_for_input=[mime_type_for_understanding]
    )

@router.message(F.text & ~F.text.startswith('/'), ManualChatTypeFilter(chat_type=["private"]))
async def handle_private_text_message(message: Message, bot: Bot):
    user_id = message.from_user.id; user_input_text = message.text
    user_lang = await get_user_language(user_id, message.from_user.language_code)
    active_model_id = await get_active_gemini_model_for_user(user_id)

    logging.info(f"[PM] User {user_id} (model: {active_model_id}) mengirim: '{user_input_text}'")
    if not user_input_text: return

    image_data_list_for_input: Optional[List[bytes]] = None
    image_mime_list_for_input: Optional[List[str]] = None
    audio_file_bytes_for_input: Optional[bytes] = None
    audio_mime_type_for_input: Optional[str] = None
    doc_file_bytes_for_input: Optional[bytes] = None
    doc_mime_type_for_input: Optional[str] = None

    if message.reply_to_message:
        reply = message.reply_to_message
        if FEATURE_ENABLE_IMAGE_UNDERSTANDING and reply.photo and not reply.media_group_id:
            logging.info(f"[PM] Reply ke foto tunggal untuk pemahaman. Download...")
            try:
                photo_to_download = reply.photo[-1]
                file_info = await bot.get_file(photo_to_download.file_id)
                mime_type = "image/jpeg"
                if hasattr(file_info, 'mime_type') and file_info.mime_type: mime_type = file_info.mime_type
                downloaded_bytes_io = await bot.download_file(file_info.file_path)
                image_data_list_for_input = [downloaded_bytes_io.read()]
                image_mime_list_for_input = [mime_type]
                downloaded_bytes_io.close()
            except Exception as e:
                logging.error(f"[PM] Gagal download foto reply (pemahaman): {e}", exc_info=True)
                await message.reply(_("error_downloading_image", user_lang))

        elif FEATURE_ENABLE_AUDIO_UNDERSTANDING and (reply.audio or reply.voice):
            logging.info(f"[PM] Reply ke audio. Download...")
            replied_audio_obj = reply.audio or reply.voice
            if replied_audio_obj.file_size > MAX_AUDIO_FILE_SIZE_BYTES_BOT:
                await message.reply(_("audio_too_large", user_lang, max_size_mb=int(MAX_AUDIO_FILE_SIZE_BYTES_BOT / (1024*1024))))
            else:
                # ... (logika download audio reply, sama seperti di handle_group_ai_command) ...
                try:
                    file_info = await bot.get_file(replied_audio_obj.file_id)
                    actual_audio_mime_type = replied_audio_obj.mime_type
                    if not actual_audio_mime_type and file_info.file_path:
                        ext = os.path.splitext(file_info.file_path)[1].lower()
                        if ext in [".oga", ".ogg"]: actual_audio_mime_type = "audio/ogg"
                        elif ext == ".mp3": actual_audio_mime_type = "audio/mpeg"
                        elif ext == ".wav": actual_audio_mime_type = "audio/wav"
                        elif ext == ".m4a": actual_audio_mime_type = "audio/aac"
                        elif ext == ".flac": actual_audio_mime_type = "audio/flac"
                        elif ext == ".aiff": actual_audio_mime_type = "audio/aiff"
                    if not actual_audio_mime_type and isinstance(replied_audio_obj, Voice): actual_audio_mime_type = "audio/ogg"

                    if not actual_audio_mime_type: await message.reply(_("error_determining_audio_mime", user_lang))
                    else:
                        normalized_mime_check = actual_audio_mime_type.lower()
                        if normalized_mime_check == "audio/mpeg": normalized_mime_check = "audio/mp3"
                        if normalized_mime_check not in SUPPORTED_AUDIO_MIME_TYPES_GEMINI:
                            await message.reply(_("audio_format_not_supported_gemini", user_lang, mime_type=actual_audio_mime_type))
                        else:
                            downloaded_bytes_io = await bot.download_file(file_info.file_path)
                            audio_file_bytes_for_input = downloaded_bytes_io.read()
                            audio_mime_type_for_input = actual_audio_mime_type
                            downloaded_bytes_io.close()
                except Exception as e: await message.reply(_("error_downloading_audio", user_lang))

        elif FEATURE_ENABLE_DOCUMENT_UNDERSTANDING and reply.document:
            logging.info(f"[PM] Reply ke dokumen. Download...")
            replied_doc_obj = reply.document
            if replied_doc_obj.mime_type not in SUPPORTED_DOCUMENT_MIME_TYPES_BOT:
                await message.reply(_("document_format_not_supported", user_lang, mime_type=replied_doc_obj.mime_type or "tidak diketahui"))
            elif replied_doc_obj.file_size > MAX_DOCUMENT_FILE_SIZE_BYTES_BOT:
                await message.reply(_("document_too_large", user_lang, max_size_mb=int(MAX_DOCUMENT_FILE_SIZE_BYTES_BOT/(1024*1024))))
            else:
                try:
                    file_info = await bot.get_file(replied_doc_obj.file_id)
                    downloaded_bytes_io = await bot.download_file(file_info.file_path)
                    doc_file_bytes_for_input = downloaded_bytes_io.read()
                    doc_mime_type_for_input = replied_doc_obj.mime_type # Seharusnya application/pdf
                    downloaded_bytes_io.close()
                except Exception as e: await message.reply(_("error_downloading_document", user_lang))


    await process_ai_interaction(message, bot, user_id, user_lang, active_model_id, user_input_text,
                                 chat_type_log_prefix="[PM] ",
                                 image_data_list_for_input=image_data_list_for_input,
                                 image_mime_types_list_for_input=image_mime_list_for_input,
                                 audio_file_bytes_for_input=audio_file_bytes_for_input,
                                 audio_mime_type_for_input=audio_mime_type_for_input,
                                 doc_file_bytes_for_input=doc_file_bytes_for_input,
                                 doc_mime_type_for_input=doc_mime_type_for_input
                                 )

@router.message(Command(commands=GROUP_TRIGGER_COMMANDS), ManualChatTypeFilter(chat_type=["group", "supergroup"]))
async def handle_group_ai_command(message: Message, command: CommandObject, bot: Bot):
    user_id = message.from_user.id
    if not message.from_user : logging.info(f"[GRUP] Pesan anon di grup {message.chat.id}. Abaikan."); return
    user_lang = await get_user_language(user_id, message.from_user.language_code)
    active_model_id = await get_active_gemini_model_for_user(user_id)

    prompt_from_args = command.args.strip() if command.args else None
    final_prompt_text = ""

    image_data_list_for_input: Optional[List[bytes]] = None
    image_mime_list_for_input: Optional[List[str]] = None
    audio_file_bytes_for_input: Optional[bytes] = None
    audio_mime_type_for_input: Optional[str] = None
    doc_file_bytes_for_input: Optional[bytes] = None
    doc_mime_type_for_input: Optional[str] = None

    if message.reply_to_message:
        reply = message.reply_to_message
        if reply.text:
            final_prompt_text = reply.text.strip()
        elif FEATURE_ENABLE_IMAGE_UNDERSTANDING and reply.photo and not reply.media_group_id:
            # ... (logika download gambar reply) ...
             try:
                photo_to_download = reply.photo[-1]
                # ... (kode download gambar sama seperti di handle_private_text_message) ...
                file_info = await bot.get_file(photo_to_download.file_id)
                mime_type = "image/jpeg"
                if hasattr(file_info, 'mime_type') and file_info.mime_type: mime_type = file_info.mime_type
                downloaded_bytes_io = await bot.download_file(file_info.file_path)
                image_data_list_for_input = [downloaded_bytes_io.read()]
                image_mime_list_for_input = [mime_type]
                downloaded_bytes_io.close()
             except Exception as e: await message.reply(_("error_downloading_image", user_lang))

        elif FEATURE_ENABLE_AUDIO_UNDERSTANDING and (reply.audio or reply.voice):
            # ... (logika download audio reply, sama seperti di handle_private_text_message) ...
            replied_audio_obj = reply.audio or reply.voice
            if replied_audio_obj.file_size > MAX_AUDIO_FILE_SIZE_BYTES_BOT:
                await message.reply(_("audio_too_large", user_lang, max_size_mb=int(MAX_AUDIO_FILE_SIZE_BYTES_BOT/(1024*1024))))
            else:
                # ... (kode download audio sama) ...
                try:
                    file_info = await bot.get_file(replied_audio_obj.file_id)
                    actual_audio_mime_type = replied_audio_obj.mime_type
                    # ... (inferensi mime) ...
                    if not actual_audio_mime_type and isinstance(replied_audio_obj, Voice): actual_audio_mime_type = "audio/ogg"

                    if not actual_audio_mime_type: await message.reply(_("error_determining_audio_mime", user_lang))
                    else:
                        # ... (cek support & download) ...
                        normalized_mime_check = actual_audio_mime_type.lower()
                        if normalized_mime_check == "audio/mpeg": normalized_mime_check = "audio/mp3"
                        if normalized_mime_check not in SUPPORTED_AUDIO_MIME_TYPES_GEMINI:
                            await message.reply(_("audio_format_not_supported_gemini", user_lang, mime_type=actual_audio_mime_type))
                        else:
                            downloaded_bytes_io = await bot.download_file(file_info.file_path)
                            audio_file_bytes_for_input = downloaded_bytes_io.read()
                            audio_mime_type_for_input = actual_audio_mime_type
                            downloaded_bytes_io.close()
                except Exception as e: await message.reply(_("error_downloading_audio", user_lang))

        elif FEATURE_ENABLE_DOCUMENT_UNDERSTANDING and reply.document:
            # ... (logika download dokumen reply, sama seperti di handle_private_text_message) ...
            replied_doc_obj = reply.document
            if replied_doc_obj.mime_type not in SUPPORTED_DOCUMENT_MIME_TYPES_BOT:
                await message.reply(_("document_format_not_supported", user_lang, mime_type=replied_doc_obj.mime_type or "tidak diketahui"))
            elif replied_doc_obj.file_size > MAX_DOCUMENT_FILE_SIZE_BYTES_BOT:
                await message.reply(_("document_too_large", user_lang, max_size_mb=int(MAX_DOCUMENT_FILE_SIZE_BYTES_BOT/(1024*1024))))
            else:
                # ... (kode download dokumen) ...
                try:
                    file_info = await bot.get_file(replied_doc_obj.file_id)
                    downloaded_bytes_io = await bot.download_file(file_info.file_path)
                    doc_file_bytes_for_input = downloaded_bytes_io.read()
                    doc_mime_type_for_input = replied_doc_obj.mime_type
                    downloaded_bytes_io.close()
                except Exception as e: await message.reply(_("error_downloading_document", user_lang))


    if prompt_from_args:
        if final_prompt_text: final_prompt_text += "\n" + prompt_from_args 
        else: final_prompt_text = prompt_from_args

    if not final_prompt_text.strip() and not image_data_list_for_input and not audio_file_bytes_for_input and not doc_file_bytes_for_input:
        logging.info(f"[GRUP_CMD] Perintah {command.command} user {user_id} tanpa prompt/media. Abaikan.")
        return

    logging.info(f"[GRUP_CMD] User {user_id} (model: {active_model_id}) '{command.command}'. Teks: '{final_prompt_text[:70]}'. Img: {'Ada' if image_data_list_for_input else 'Tidak'}. Audio: {'Ada' if audio_file_bytes_for_input else 'Tidak'}. Dok: {'Ada' if doc_file_bytes_for_input else 'Tidak'}")
    await process_ai_interaction(message, bot, user_id, user_lang, active_model_id, final_prompt_text,
                                 chat_type_log_prefix="[GRUP_CMD] ",
                                 image_data_list_for_input=image_data_list_for_input,
                                 image_mime_types_list_for_input=image_mime_list_for_input,
                                 audio_file_bytes_for_input=audio_file_bytes_for_input,
                                 audio_mime_type_for_input=audio_mime_type_for_input,
                                 doc_file_bytes_for_input=doc_file_bytes_for_input,
                                 doc_mime_type_for_input=doc_mime_type_for_input
                                 )

@router.message(F.reply_to_message, F.text, ~F.text.startswith('/'), ManualChatTypeFilter(chat_type=["group", "supergroup"]))
async def handle_reply_to_bot_in_group(message: Message, bot: Bot):
    if not message.from_user: return
    if not message.reply_to_message.from_user or message.reply_to_message.from_user.id != bot.id: return 
    user_id = message.from_user.id
    final_prompt_text = message.text.strip() if message.text else None
    if not final_prompt_text:
        logging.info(f"[GRUP_REPLY_TO_BOT] Reply ke bot user {user_id} tanpa teks. Abaikan.")
        return

    user_lang = await get_user_language(user_id, message.from_user.language_code)
    active_model_id = await get_active_gemini_model_for_user(user_id)

    logging.info(f"[GRUP_REPLY_TO_BOT] User {user_id} (model: {active_model_id}) me-reply bot. Prompt: '{final_prompt_text[:100]}...'")
    await process_ai_interaction(message, bot, user_id, user_lang, active_model_id, final_prompt_text,
                                 chat_type_log_prefix="[GRUP_REPLY_TO_BOT] ")
