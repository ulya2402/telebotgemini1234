import logging
from datetime import date 
import io
import asyncio

from aiogram import Bot, Router, F, types 
from aiogram.filters import CommandStart, Command 
from aiogram.filters.command import CommandObject 
from aiogram.types import Message, CallbackQuery, PhotoSize 
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from aiogram.dispatcher.middlewares.base import BaseMiddleware 


from typing import List, Dict, Optional, Tuple, Union, Set, Any, TypedDict

from .utils import local_escape_markdown_v1, ManualChatTypeFilter, split_long_message 

from core.config import TELEGRAM_MAX_MESSAGE_LENGTH

class FallbackPartDict(TypedDict): text: str
class FallbackContentDict(TypedDict): role: str; parts: List[FallbackPartDict]

try:
    from google.generativeai.types import ContentDict
except ImportError:
    logging.warning("google.generativeai.types.ContentDict tidak ditemukan, menggunakan fallback TypedDict.")
    ContentDict = FallbackContentDict # type: ignore

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
                replied_text_content = event.reply_to_message.text or event.reply_to_message.caption or '[Non-Text Content] رأي'
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

async def get_user_language(user_id: int, telegram_lang_code: Optional[str] = None) -> str: # Definisi Lengkap
    cached_lang = user_languages_cache.get(user_id);
    if cached_lang: return cached_lang
    if FEATURE_ENABLE_DATABASE:
        db_lang = await get_user_language_from_db(user_id)
        if db_lang and db_lang in AVAILABLE_LANGUAGES: user_languages_cache[user_id] = db_lang; return db_lang
    if telegram_lang_code:
        lang_prefix = telegram_lang_code.split('-')[0]
        if lang_prefix in AVAILABLE_LANGUAGES: user_languages_cache[user_id] = lang_prefix; return lang_prefix
    return DEFAULT_LANGUAGE

async def get_active_gemini_model_for_user(user_id: int) -> str: # Definisi Lengkap
    cached_model = user_selected_model_cache.get(user_id)
    if cached_model and cached_model in AVAILABLE_GEMINI_MODELS: return cached_model
    if FEATURE_ENABLE_DATABASE:
        db_model = await get_user_selected_model(user_id)
        if db_model and db_model in AVAILABLE_GEMINI_MODELS: user_selected_model_cache[user_id] = db_model; return db_model
    if DEFAULT_GEMINI_MODEL_ID: user_selected_model_cache[user_id] = DEFAULT_GEMINI_MODEL_ID; return DEFAULT_GEMINI_MODEL_ID
    try: fallback_model = next(iter(AVAILABLE_GEMINI_MODELS)); user_selected_model_cache[user_id] = fallback_model; return fallback_model
    except StopIteration: logging.error("AVAILABLE_GEMINI_MODELS kosong!"); raise ValueError("Tidak ada model Gemini.")


@router.message(CommandStart())
async def handle_start(message: Message): 
    user_id = message.from_user.id; user_lang = await get_user_language(user_id, message.from_user.language_code)
    logging.info(f"User {user_id} di chat {message.chat.id} memulai bot. Bahasa: {user_lang}")
    if FEATURE_ENABLE_DAILY_CHAT_LIMIT and FEATURE_ENABLE_DATABASE: await check_and_update_chat_limit(user_id, DAILY_CHAT_LIMIT_PER_USER)
    await message.answer(_("welcome_message", user_lang)); await message.answer(_("language_suggestion", user_lang, default_return_key_on_missing=True))

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
    current_model_name = AVAILABLE_GEMINI_MODELS.get(current_model_id, current_model_id)
    text = _("settings_title", user_lang) + "\n\n" + _("settings_select_model_prompt", user_lang, current_model_name=local_escape_markdown_v1(current_model_name))
    builder = InlineKeyboardBuilder()
    for model_id, friendly_name in AVAILABLE_GEMINI_MODELS.items():
        display_name = f"✅ {friendly_name}" if model_id == current_model_id else friendly_name
        builder.button(text=display_name, callback_data=f"select_model:{model_id}")
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
        current_model_name_after_change = AVAILABLE_GEMINI_MODELS.get(current_model_id_after_change, current_model_id_after_change)
        new_text = _("settings_title", user_lang) + "\n\n" + _("settings_select_model_prompt", user_lang, current_model_name=local_escape_markdown_v1(current_model_name_after_change))
        builder = InlineKeyboardBuilder()
        for mid_loop, fname_loop in AVAILABLE_GEMINI_MODELS.items(): 
            display_name = f"✅ {fname_loop}" if mid_loop == current_model_id_after_change else fname_loop
            builder.button(text=display_name, callback_data=f"select_model:{mid_loop}")
        builder.adjust(1)
        try: await callback_query.message.edit_text(new_text, reply_markup=builder.as_markup())
        except Exception as e: logging.info(f"Gagal edit pesan settings: {e}")
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
        model_friendly_name = AVAILABLE_GEMINI_MODELS.get(active_model_id, active_model_id)
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


async def process_ai_interaction(
    message: Message, bot: Bot, user_id: int, user_lang: str, 
    active_model_id: str, prompt_text: Optional[str], 
    chat_type_log_prefix: str = "",
    image_data_list: Optional[List[Dict[str, Any]]] = None
):
    if FEATURE_ENABLE_DAILY_CHAT_LIMIT and FEATURE_ENABLE_DATABASE:
        can_chat, remaining_chats = await check_and_update_chat_limit(user_id, DAILY_CHAT_LIMIT_PER_USER)
        if not can_chat:
            await message.reply(_("chat_limit_reached", user_lang, limit_count=DAILY_CHAT_LIMIT_PER_USER))
            log_prompt = prompt_text or (_("default_image_prompt", user_lang) if image_data_list else "[Konten tidak ada]")
            logging.info(f"{chat_type_log_prefix}User {user_id} (chat {message.chat.id}) limit chat. Pesan ('{log_prompt[:30]}...') diblokir.")
            return
    if not FEATURE_ENABLE_GEMINI: await message.reply(_("ai_feature_disabled", user_lang)); return
    actual_prompt_text_for_gemini = prompt_text if prompt_text and prompt_text.strip() else None
    if image_data_list and FEATURE_ENABLE_IMAGE_UNDERSTANDING and not actual_prompt_text_for_gemini:
        actual_prompt_text_for_gemini = _("default_image_prompt", user_lang)
    if not actual_prompt_text_for_gemini and not (image_data_list and FEATURE_ENABLE_IMAGE_UNDERSTANDING):
        logging.warning(f"{chat_type_log_prefix}Tidak ada prompt teks atau gambar valid untuk user {user_id}")
        await message.reply(_("gemini_no_content_to_send", user_lang), parse_mode=None); return
    
    processing_key = "gemini_processing"
    if image_data_list and FEATURE_ENABLE_IMAGE_UNDERSTANDING: processing_key = "processing_image_prompt"
    processing_text_str = _(processing_key, user_lang, default_return_key_on_missing=True)
    if processing_text_str == processing_key : 
        processing_text_str = "🖼️ Analyzing..." if image_data_list and FEATURE_ENABLE_IMAGE_UNDERSTANDING else "🧠 Thinking..."
    processing_msg = None
    try: processing_msg = await message.reply(processing_text_str, parse_mode=None)
    except Exception as e: logging.error(f"{chat_type_log_prefix}Error send processing: {e}", exc_info=True) 
    
    gemini_history_for_api: List[ContentDict] = [] 
    if FEATURE_ENABLE_CONVERSATION_HISTORY and FEATURE_ENABLE_DATABASE:
        raw_db_history = await get_conversation_history(user_id, limit=GEMINI_CONVERSATION_HISTORY_MAX_MESSAGES)
        if raw_db_history:
            for msg_from_db in raw_db_history: gemini_history_for_api.append({"role": msg_from_db["role"], "parts": [{"text": msg_from_db["content"]}]}) # type: ignore
    final_image_bytes_list: Optional[List[bytes]] = None; final_image_mime_types_list: Optional[List[str]] = None
    if image_data_list and FEATURE_ENABLE_IMAGE_UNDERSTANDING:
        final_image_bytes_list = [item['bytes'] for item in image_data_list if 'bytes' in item]
        final_image_mime_types_list = [item['mime_type'] for item in image_data_list if 'mime_type' in item]
        if not final_image_bytes_list: final_image_bytes_list = None; final_image_mime_types_list = None

    ai_response_raw = await get_gemini_response(
        prompt_text=actual_prompt_text_for_gemini, model_id=active_model_id, user_lang=user_lang,
        history=gemini_history_for_api if gemini_history_for_api else None,
        image_bytes_list=final_image_bytes_list, image_mime_types_list=final_image_mime_types_list
    )
    logging.info(f"{chat_type_log_prefix}Respons Gemini model '{active_model_id}' user {user_id}. Panjang: {len(ai_response_raw or '')}")

    if processing_msg:
        try: await processing_msg.delete()
        except Exception: pass 

    prompt_saved_to_history = actual_prompt_text_for_gemini
    if image_data_list and FEATURE_ENABLE_IMAGE_UNDERSTANDING: 
        prompt_saved_to_history = f"{prompt_text if prompt_text else ''} [{len(image_data_list)} image(s) processed]" 
        if not prompt_text: prompt_saved_to_history = f"[{_('default_image_prompt', user_lang)}] [{len(image_data_list)} image(s) processed]"

    if FEATURE_ENABLE_CONVERSATION_HISTORY and FEATURE_ENABLE_DATABASE: 
        if prompt_saved_to_history: await add_message_to_history(user_id, role="user", content=prompt_saved_to_history)
        if ai_response_raw: await add_message_to_history(user_id, role="model", content=ai_response_raw)

    if not ai_response_raw:
        await message.reply(_("gemini_empty_response", user_lang, default_return_key_on_missing=True), parse_mode=None); return

    
    if len(ai_response_raw) > TELEGRAM_MAX_MESSAGE_LENGTH:
        logging.info(f"Respons AI terlalu panjang ({len(ai_response_raw)} karakter), akan dipecah.")
        chunks = split_long_message(ai_response_raw, TELEGRAM_MAX_MESSAGE_LENGTH)
        for i, chunk in enumerate(chunks):
            try:
                
                if i == 0:
                    await message.reply(chunk, parse_mode=None) 
                else:
                    await message.answer(chunk, parse_mode=None)
                if i < len(chunks) - 1: 
                    await asyncio.sleep(0.5) 
            except Exception as e_chunk:
                logging.error(f"{chat_type_log_prefix}Gagal mengirim chunk {i+1}/{len(chunks)}: {e_chunk}", exc_info=True)
                await message.answer(_("gemini_error_sending_response_chunk", user_lang, default_return_key_on_missing=True)) # Kunci terjemahan baru
                break 
    else: 
        try: await message.reply(ai_response_raw) 
        except TelegramBadRequest:
            try: await message.reply(local_escape_markdown_v1(ai_response_raw)) 
            except TelegramBadRequest:
                try: await message.reply(ai_response_raw, parse_mode=None)
                except Exception as e_fallback: logging.error(f"{chat_type_log_prefix}Gagal total kirim balasan AI: {e_fallback}", exc_info=True)
        except Exception as e_general: logging.error(f"{chat_type_log_prefix}Error umum kirim AI response: {e_general}", exc_info=True)
    


async def process_complete_album(media_group_id: str, bot: Bot):
    await asyncio.sleep(ALBUM_PROCESSING_TIMEOUT)
    if media_group_id not in media_group_cache: return 
    album_data = media_group_cache.pop(media_group_id) 
    messages_in_album: List[Message] = album_data["messages"]
    user_id = album_data["user_id"]
    initial_message = album_data["initial_message_for_reply"] 
    if not messages_in_album or not initial_message.from_user : return
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
    images_data_list: List[Dict[str, Any]] = []
    download_tasks = []
    async def download_photo_task(photo_size: PhotoSize):
        try:
            file_info = await bot.get_file(photo_size.file_id)
            downloaded_bytes_io: io.BytesIO = await bot.download_file(file_info.file_path)
            image_bytes = downloaded_bytes_io.read(); downloaded_bytes_io.close()
            return {"bytes": image_bytes, "mime_type": "image/jpeg"}
        except Exception as e: logging.error(f"Gagal download foto album (file_id: {photo_size.file_id}): {e}"); return None
    for msg_with_photo in images_to_process_from_album:
        if msg_with_photo.photo: download_tasks.append(download_photo_task(msg_with_photo.photo[-1]))
    downloaded_image_results = await asyncio.gather(*download_tasks)
    for result in downloaded_image_results:
        if result: images_data_list.append(result)
    if not images_data_list:
        logging.warning(f"Tidak ada gambar di-download untuk album {media_group_id} user {user_id}.")
        if not notified_limit or original_photo_count == 0 : # Hanya kirim error jika tidak ada notif limit atau memang tidak ada foto sama sekali
            await initial_message.reply(_("error_downloading_image", user_lang))
        return
    await process_ai_interaction(
        message=initial_message, bot=bot, user_id=user_id, user_lang=user_lang,
        active_model_id=active_model_id, prompt_text=album_caption, 
        chat_type_log_prefix="[ALBUM] ", image_data_list=images_data_list
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
        logging.info(f"Menerima video di album {media_group_id}, saat ini hanya foto yang diproses.")
    if media_group_cache[media_group_id]["timer"]:
        media_group_cache[media_group_id]["timer"].cancel()
    media_group_cache[media_group_id]["timer"] = asyncio.create_task(
        process_complete_album(media_group_id, bot)
    )

@router.message(F.photo & ~F.media_group_id, ManualChatTypeFilter(chat_type=["private", "group", "supergroup"])) 
async def handle_single_photo_message(message: Message, bot: Bot):
    if not FEATURE_ENABLE_IMAGE_UNDERSTANDING:
        if message.caption: await message.reply(_("image_understanding_disabled", await get_user_language(message.from_user.id, message.from_user.language_code)))
        return
    if not message.from_user: return 
    user_id = message.from_user.id; user_lang = await get_user_language(user_id, message.from_user.language_code)
    active_model_id = await get_active_gemini_model_for_user(user_id)
    image_data_for_gemini: Optional[bytes] = None; image_mime_for_gemini: Optional[str] = "image/jpeg" 
    if message.photo:
        logging.info(f"Menerima foto tunggal user {user_id}. Download...")
        try:
            photo_size: PhotoSize = message.photo[-1] 
            file_info = await bot.get_file(photo_size.file_id)
            downloaded_bytes_io: io.BytesIO = await bot.download_file(file_info.file_path)
            image_data_for_gemini = downloaded_bytes_io.read(); downloaded_bytes_io.close()
        except Exception as e:
            logging.error(f"Gagal download foto tunggal user {user_id}: {e}", exc_info=True)
            await message.reply(_("error_downloading_image", user_lang)); return
    if not image_data_for_gemini: return
    prompt_text = message.caption.strip() if message.caption else None 
    logging.info(f"[FOTO_SINGLE] User {user_id} (model: {active_model_id}) foto. Caption: '{prompt_text[:70] if prompt_text else '[NoCap]'}'")
    await process_ai_interaction(
        message, bot, user_id, user_lang, active_model_id, prompt_text=prompt_text, 
        chat_type_log_prefix="[FOTO_SINGLE] ",
        image_data_list=[{"bytes": image_data_for_gemini, "mime_type": image_mime_for_gemini}] if image_data_for_gemini else None
    )

@router.message(F.text & ~F.text.startswith('/'), ManualChatTypeFilter(chat_type=["private"])) 
async def handle_private_text_message(message: Message, bot: Bot):
    user_id = message.from_user.id; user_input_text = message.text 
    user_lang = await get_user_language(user_id, message.from_user.language_code)
    active_model_id = await get_active_gemini_model_for_user(user_id)
    logging.info(f"[PM] User {user_id} (model: {active_model_id}) mengirim: '{user_input_text}'")
    if not user_input_text: return
    image_data_list_for_gemini: Optional[List[Dict[str, Any]]] = None
    if FEATURE_ENABLE_IMAGE_UNDERSTANDING and message.reply_to_message and \
       message.reply_to_message.photo and not message.reply_to_message.media_group_id:
        logging.info(f"[PM] Reply ke foto tunggal. Download...")
        try:
            photo_to_download = message.reply_to_message.photo[-1]
            file_info = await bot.get_file(photo_to_download.file_id)
            downloaded_bytes_io = await bot.download_file(file_info.file_path)
            image_bytes = downloaded_bytes_io.read(); downloaded_bytes_io.close()
            image_data_list_for_gemini = [{"bytes": image_bytes, "mime_type": "image/jpeg"}]
        except Exception as e:
            logging.error(f"[PM] Gagal download foto reply: {e}", exc_info=True)
            await message.reply(_("error_downloading_image", user_lang))
    await process_ai_interaction(message, bot, user_id, user_lang, active_model_id, user_input_text, 
                                 chat_type_log_prefix="[PM] ", image_data_list=image_data_list_for_gemini)

@router.message(Command(commands=GROUP_TRIGGER_COMMANDS), ManualChatTypeFilter(chat_type=["group", "supergroup"])) 
async def handle_group_ai_command(message: Message, command: CommandObject, bot: Bot): 
    user_id = message.from_user.id
    if not message.from_user : logging.info(f"[GRUP] Pesan anon di grup {message.chat.id}. Abaikan."); return
    chat_id = message.chat.id; user_lang = await get_user_language(user_id, message.from_user.language_code)
    active_model_id = await get_active_gemini_model_for_user(user_id)
    prompt_from_args = command.args.strip() if command.args else None
    prompt_from_reply_text: Optional[str] = None
    image_data_list_for_gemini: Optional[List[Dict[str, Any]]] = None
    if message.reply_to_message:
        if message.reply_to_message.text:
            prompt_from_reply_text = message.reply_to_message.text.strip()
        elif FEATURE_ENABLE_IMAGE_UNDERSTANDING and message.reply_to_message.photo and \
             not message.reply_to_message.media_group_id: 
            logging.info(f"[GRUP_CMD] Reply ke foto tunggal. Download...")
            try:
                photo_to_download = message.reply_to_message.photo[-1]
                file_info = await bot.get_file(photo_to_download.file_id)
                downloaded_bytes_io = await bot.download_file(file_info.file_path)
                image_bytes = downloaded_bytes_io.read(); downloaded_bytes_io.close()
                image_data_list_for_gemini = [{"bytes": image_bytes, "mime_type": "image/jpeg"}]
            except Exception as e:
                logging.error(f"[GRUP_CMD] Gagal download foto reply: {e}", exc_info=True)
                await message.reply(_("error_downloading_image", user_lang))
    final_prompt_text = ""
    if prompt_from_reply_text: final_prompt_text = prompt_from_reply_text
    if prompt_from_args:
        if final_prompt_text: final_prompt_text += "\n" + prompt_from_args 
        else: final_prompt_text = prompt_from_args
    if not final_prompt_text.strip() and not image_data_list_for_gemini:
        logging.info(f"[GRUP_CMD] Perintah {command.command} user {user_id} tanpa prompt/gambar. Abaikan.")
        return
    logging.info(f"[GRUP_CMD] User {user_id} (model: {active_model_id}) '{command.command}'. Teks: '{final_prompt_text[:70]}'. Img: {'Ada' if image_data_list_for_gemini else 'Tidak'}")
    await process_ai_interaction(message, bot, user_id, user_lang, active_model_id, final_prompt_text, 
                                 chat_type_log_prefix="[GRUP_CMD] ", image_data_list=image_data_list_for_gemini)

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
