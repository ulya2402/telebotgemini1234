import google.generativeai as genai
from google.generativeai import types as genai_types
from typing import List, Optional, Dict, Any
import logging
import base64

from core.config import (
    GEMINI_API_KEY, FEATURE_ENABLE_GEMINI, DEFAULT_LANGUAGE,
    SUPPORTED_AUDIO_MIME_TYPES_GEMINI, GEMINI_SYSTEM_PROMPT,
    SUPPORTED_DOCUMENT_MIME_TYPES_BOT # Impor konstanta baru
)
from core.localization import _

if FEATURE_ENABLE_GEMINI and GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        logging.info("Konfigurasi Gemini API Key berhasil.")
    except Exception as e:
        logging.error(f"Gagal melakukan genai.configure: {e}")
elif FEATURE_ENABLE_GEMINI and not GEMINI_API_KEY:
    logging.warning("Fitur Gemini diaktifkan tetapi GEMINI_API_KEY tidak tersedia. Fungsi Gemini tidak akan bekerja.")
else:
    logging.info("Fitur Gemini dinonaktifkan via konfigurasi.")

SAFETY_SETTINGS = {
    genai_types.HarmCategory.HARM_CATEGORY_HARASSMENT: genai_types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    genai_types.HarmCategory.HARM_CATEGORY_HATE_SPEECH: genai_types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    genai_types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: genai_types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    genai_types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: genai_types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
}

async def get_gemini_response(
    prompt_text: Optional[str],
    model_id: str,
    user_lang: str = DEFAULT_LANGUAGE,
    history: Optional[List[genai_types.ContentDict]] = None,
    image_bytes_list: Optional[List[bytes]] = None,
    image_mime_types_list: Optional[List[str]] = None,
    audio_file_bytes: Optional[bytes] = None,
    audio_mime_type: Optional[str] = None,
    doc_file_bytes: Optional[bytes] = None, # Parameter baru
    doc_mime_type: Optional[str] = None     # Parameter baru
) -> str:

    if not FEATURE_ENABLE_GEMINI:
        return _("ai_feature_disabled", user_lang)
    if not GEMINI_API_KEY:
        return _("gemini_api_key_not_configured", user_lang)

    try:
        logging.debug(f"Menginisialisasi model Gemini: {model_id}")

        system_instruction_to_use = None
        if GEMINI_SYSTEM_PROMPT and GEMINI_SYSTEM_PROMPT.strip():
            system_instruction_to_use = GEMINI_SYSTEM_PROMPT
            logging.debug(f"Menggunakan instruksi sistem: {system_instruction_to_use[:100]}...")

        current_model = genai.GenerativeModel(
            model_name=model_id,
            safety_settings=SAFETY_SETTINGS,
            system_instruction=system_instruction_to_use
        )
        logging.info(f"Model Gemini '{model_id}' berhasil diinisialisasi untuk permintaan.")

        contents_for_api: List[genai_types.ContentDict] = []

        if history:
            contents_for_api.extend(history)

        current_user_message_parts: List[Dict[str, Any]] = []

        if image_bytes_list and image_mime_types_list:
            processed_image_count = 0
            for img_bytes, mime_type in zip(image_bytes_list, image_mime_types_list):
                if mime_type.startswith("image/"):
                    img_base64 = base64.b64encode(img_bytes).decode('utf-8')
                    image_part_dict = {"inline_data": {"mime_type": mime_type, "data": img_base64}}
                    current_user_message_parts.append(image_part_dict)
                    processed_image_count +=1
                else:
                    logging.warning(f"MIME type gambar tidak valid: {mime_type}. Gambar dilewati.")
            if processed_image_count > 0:
                 logging.info(f"Menambahkan {processed_image_count} gambar valid ke permintaan Gemini.")

        if audio_file_bytes and audio_mime_type:
            normalized_mime_type = audio_mime_type.lower()
            if normalized_mime_type == "audio/mpeg":
                normalized_mime_type = "audio/mp3"

            if normalized_mime_type in SUPPORTED_AUDIO_MIME_TYPES_GEMINI:
                audio_part_dict = {
                    "inline_data": {
                        "mime_type": normalized_mime_type,
                        "data": audio_file_bytes
                    }
                }
                current_user_message_parts.append(audio_part_dict)
                logging.info(f"Menambahkan audio part (dict) ({len(audio_file_bytes)} bytes, MIME: {normalized_mime_type}) ke permintaan Gemini.")
            else:
                logging.warning(f"MIME type audio '{normalized_mime_type}' (dari '{audio_mime_type}') tidak didukung oleh Gemini. Audio dilewati.")
                return _("audio_format_not_supported_gemini", user_lang, mime_type=audio_mime_type)

        if doc_file_bytes and doc_mime_type:
            if doc_mime_type in SUPPORTED_DOCUMENT_MIME_TYPES_BOT: # Cek apakah PDF
                doc_part_dict = {
                    "inline_data": {
                        "mime_type": doc_mime_type, # Seharusnya application/pdf
                        "data": doc_file_bytes
                    }
                }
                current_user_message_parts.append(doc_part_dict)
                logging.info(f"Menambahkan dokumen part (dict) ({len(doc_file_bytes)} bytes, MIME: {doc_mime_type}) ke permintaan Gemini.")
            else:
                logging.warning(f"MIME type dokumen '{doc_mime_type}' tidak didukung oleh bot saat ini (hanya PDF). Dokumen dilewati.")
                return _("document_format_not_supported", user_lang, mime_type=doc_mime_type) # Kunci baru

        if prompt_text and prompt_text.strip():
            current_user_message_parts.append({"text": prompt_text})

        if current_user_message_parts:
            contents_for_api.append({'role': 'user', 'parts': current_user_message_parts})
        elif not history: 
            logging.warning(f"Tidak ada konten (riwayat, teks, media) untuk dikirim ke model '{model_id}'.")
            return _("gemini_no_content_to_send", user_lang)

        if not contents_for_api: 
            logging.warning(f"Konten API kosong sebelum dikirim ke model '{model_id}'.")
            return _("gemini_no_content_to_send", user_lang)

        logging.debug(f"Mengirim permintaan ke Gemini model '{model_id}'. Jumlah item konten: {len(contents_for_api)}")

        response = await current_model.generate_content_async(contents=contents_for_api)

        if response.candidates and response.candidates[0].content.parts:
            all_text_parts = [part.text for part in response.candidates[0].content.parts if hasattr(part, 'text') and part.text]
            if all_text_parts:
                return "\n".join(all_text_parts)
            else: 
                logging.warning(f"Gemini mengembalikan parts tanpa teks untuk model '{model_id}'.")
                return _("gemini_empty_response", user_lang)
        else: 
            reasons_list = []
            if response.prompt_feedback and response.prompt_feedback.block_reason:
                reasons_list.append(f"Alasan pemblokiran: {response.prompt_feedback.block_reason.name}")
            if hasattr(response, 'candidates') and response.candidates:
                for candidate in response.candidates:
                    if candidate.finish_reason != 1: 
                         reasons_list.append(f"Alasan penyelesaian tidak normal: {candidate.finish_reason.name} ({candidate.finish_reason.value})")
            if reasons_list:
                reasons_str = ", ".join(reasons_list)
                logging.warning(f"Permintaan Gemini '{model_id}' diblokir: {reasons_str}")
                return _("gemini_request_blocked", user_lang, reasons=reasons_str)

            logging.warning(f"Tidak ada respons valid dari Gemini '{model_id}'. feedback: {response.prompt_feedback if hasattr(response, 'prompt_feedback') else 'N/A'}")
            return _("gemini_no_valid_response", user_lang)

    except Exception as e:
        error_str = str(e)
        logging.error(f"Error menghubungi Gemini API model '{model_id}': {e}", exc_info=True)
        if "model" in error_str.lower() and ("not found" in error_str.lower() or "does not exist" in error_str.lower() or "permission" in error_str.lower()):
            return _("gemini_model_not_found", user_lang, model_id=model_id)
        if isinstance(e, AttributeError) and 'Part' in error_str:
             logging.error(f"AttributeError terkait 'Part' (kemungkinan besar masalah library): {e}", exc_info=True)
             return _("gemini_error_contacting", user_lang, error_message="Masalah internal dengan library AI (Part).")

        return _("gemini_error_contacting", user_lang, error_message=error_str)
