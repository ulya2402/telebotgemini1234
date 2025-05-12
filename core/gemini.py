import google.generativeai as genai
#from google.generativeai import types as genai_types
from google.generativeai.types import ContentDict, HarmCategory, HarmBlockThreshold
from typing import List, Optional 
import logging 
import base64

from core.config import GEMINI_API_KEY, FEATURE_ENABLE_GEMINI, DEFAULT_LANGUAGE, DEFAULT_GEMINI_MODEL_ID # Impor DEFAULT_GEMINI_MODEL_ID
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
    HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
}



async def get_gemini_response(
    prompt_text: Optional[str], 
    model_id: str,
    user_lang: str = DEFAULT_LANGUAGE,
    history: Optional[List[ContentDict]] = None, 
    image_bytes_list: Optional[List[bytes]] = None,
    image_mime_types_list: Optional[List[str]] = None
) -> str:
    if not FEATURE_ENABLE_GEMINI:
        return _("ai_feature_disabled", user_lang)
    if not GEMINI_API_KEY:
        return _("gemini_api_key_not_configured", user_lang)

    try:
        logging.debug(f"Menginisialisasi model Gemini: {model_id}")
        current_model = genai.GenerativeModel(
            model_name=model_id,
            safety_settings=SAFETY_SETTINGS
        )
        logging.info(f"Model Gemini '{model_id}' berhasil diinisialisasi untuk permintaan.")

        

        contents_for_api: List[ContentDict] = []

        
        if history:
            contents_for_api.extend(history) 

        
        current_user_message_parts: List[Dict[str, Any]] = [] # List of part dictionaries

        if image_bytes_list and image_mime_types_list and len(image_bytes_list) == len(image_mime_types_list):
            for img_bytes, mime_type in zip(image_bytes_list, image_mime_types_list):
                if mime_type.startswith("image/"):
                    # --- BUAT PART GAMBAR SECARA MANUAL ---
                    img_base64 = base64.b64encode(img_bytes).decode('utf-8')
                    image_part_dict = {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": img_base64
                        }
                    }
                    current_user_message_parts.append(image_part_dict)
                    # --- --------------------------------- ---
                else:
                    logging.warning(f"MIME type tidak valid untuk gambar: {mime_type}. Gambar dilewati.")
            logging.info(f"Menambahkan {len(current_user_message_parts)} gambar yang valid ke permintaan Gemini.")

        if prompt_text and prompt_text.strip():
            current_user_message_parts.append({"text": prompt_text})

        if current_user_message_parts:
            contents_for_api.append({'role': 'user', 'parts': current_user_message_parts})
        elif not history: 
            logging.warning(f"Tidak ada konten (history, teks, atau gambar) untuk dikirim ke Gemini model '{model_id}'.")
            return _("gemini_no_content_to_send", user_lang, default_return_key_on_missing=True)

        if not contents_for_api: 
            logging.warning(f"Contents_for_api kosong sebelum mengirim ke API model '{model_id}'.")
            return _("gemini_no_content_to_send", user_lang)

        logging.debug(f"Mengirim permintaan ke Gemini model '{model_id}'. Jumlah item konten: {len(contents_for_api)}")
        # Untuk debugging detail, Anda bisa log sebagian dari contents_for_api jika perlu, tapi hati-hati dengan data gambar base64 yang besar.
        # logging.debug(f"Payload contoh: {str(contents_for_api)[:500]}") 

        response = await current_model.generate_content_async(contents=contents_for_api) # type: ignore

        if response.candidates and response.candidates[0].content.parts:
            return response.candidates[0].content.parts[0].text
        else:
            reasons_list = []
            if response.prompt_feedback and response.prompt_feedback.block_reason:
                reasons_list.append(f"Alasan pemblokiran: {response.prompt_feedback.block_reason.name}")
            if hasattr(response, 'candidates') and response.candidates:
                for candidate in response.candidates:
                    if candidate.finish_reason != 1: 
                         reasons_list.append(f"Alasan penyelesaian tidak normal: {candidate.finish_reason.name} ({candidate.finish_reason.value})")
            if reasons_list:
                reasons_str = ", ".join(reasons_list); logging.warning(f"Permintaan Gemini '{model_id}' diblokir: {reasons_str}")
                return _("gemini_request_blocked", user_lang, reasons=reasons_str)
            logging.warning(f"Tidak ada respons valid dari Gemini '{model_id}'. feedback: {response.prompt_feedback if hasattr(response, 'prompt_feedback') else 'N/A'}")
            return _("gemini_no_valid_response", user_lang)

    except Exception as e:
        if "model" in str(e).lower() and ("not found" in str(e).lower() or "does not exist" in str(e).lower() or "permission" in str(e).lower()):
            logging.error(f"Model Gemini '{model_id}' bermasalah (tidak ditemukan/izin): {e}", exc_info=True)
            return _("gemini_model_not_found", user_lang, model_id=model_id, default_return_key_on_missing=True)
        # Tangkap AttributeError secara spesifik jika masih ada, meskipun seharusnya tidak dari sini lagi
        if isinstance(e, AttributeError) and 'Part' in str(e):
             logging.error(f"Masih ada AttributeError terkait 'Part' dengan model '{model_id}': {e}", exc_info=True)
             return "Terjadi masalah internal dengan library AI (AttributeError Part). Mohon coba lagi atau hubungi admin."
        logging.error(f"Error menghubungi Gemini API model '{model_id}': {e}", exc_info=True)
        return _("gemini_error_contacting", user_lang, error_message=str(e))
