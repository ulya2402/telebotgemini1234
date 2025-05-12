from supabase import create_client, Client
from typing import Optional, Dict, Any, List, Tuple
import logging
from datetime import date, timedelta

from core.config import (
    SUPABASE_URL,
    SUPABASE_KEY,
    FEATURE_ENABLE_DATABASE,
)

supabase_client: Optional[Client] = None

if FEATURE_ENABLE_DATABASE and SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        logging.info("Koneksi ke Supabase berhasil diinisialisasi.")
    except Exception as e:
        logging.error(f"Gagal menginisialisasi koneksi Supabase: {e}", exc_info=True)
        supabase_client = None
elif FEATURE_ENABLE_DATABASE:
    logging.warning("Fitur database diaktifkan tetapi URL atau Key Supabase tidak ada. Operasi database akan gagal.")


async def get_user_language_from_db(user_id: int) -> Optional[str]:
    """Mengambil preferensi bahasa pengguna dari database."""
    if not supabase_client or not FEATURE_ENABLE_DATABASE:
        return None
    try:
        response = (
            supabase_client.table("user_preferences")
            .select("language_code")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if response.data:
            return response.data[0].get("language_code")
        return None
    except Exception as e:
        logging.error(f"Error saat mengambil bahasa pengguna {user_id} dari DB: {e}", exc_info=True)
        return None


async def set_user_language_in_db(user_id: int, lang_code: str) -> bool:
    """Menyimpan atau memperbarui preferensi bahasa pengguna di database."""
    if not supabase_client or not FEATURE_ENABLE_DATABASE:
        return False
    try:
        data_to_upsert: Dict[str, Any] = {
            "user_id": user_id,
            "language_code": lang_code,
        }
        response = (
            supabase_client.table("user_preferences")
            .upsert(data_to_upsert, on_conflict="user_id")
            .execute()
        )
        if response.data:
            return True
        if hasattr(response, 'error') and response.error:
            logging.error(f"Supabase error saat upsert bahasa untuk user {user_id}: {response.error}")
            return False
        return not (hasattr(response, 'error') and response.error)
    except Exception as e:
        logging.error(f"Error saat menyimpan bahasa pengguna {user_id} ke DB: {e}", exc_info=True)
        return False


async def add_message_to_history(user_id: int, role: str, content: str) -> bool:
    """
    Menambahkan pesan ke riwayat percakapan pengguna.
    Role bisa 'user' atau 'model'.
    """
    if not supabase_client or not FEATURE_ENABLE_DATABASE:
        logging.debug("Supabase client tidak tersedia atau fitur database dinonaktifkan untuk add_message_to_history.")
        return False

    if role not in ['user', 'model']:
        logging.warning(f"Peran tidak valid ('{role}') saat mencoba menambahkan riwayat untuk user {user_id}.")
        return False

    try:
        message_data = {
            "user_id": user_id,
            "role": role,
            "content": content
        }
        response = supabase_client.table("conversation_history").insert(message_data).execute()
        if hasattr(response, 'error') and response.error:
            logging.error(f"Supabase error saat menambahkan riwayat untuk user {user_id}: {response.error}")
            return False
        return bool(response.data) 
    except Exception as e:
        logging.error(f"Error saat menambahkan riwayat percakapan untuk user {user_id}: {e}", exc_info=True)
        return False


async def get_conversation_history(user_id: int, limit: int = 10) -> List[Dict[str, str]]:
    """
    Mengambil riwayat percakapan terakhir untuk pengguna tertentu.
    Mengembalikan list of dictionaries, masing-masing berisi 'role' dan 'content'.
    """
    if not supabase_client or not FEATURE_ENABLE_DATABASE:
        logging.debug("Supabase client tidak tersedia atau fitur database dinonaktifkan untuk get_conversation_history.")
        return []

    try:
        response = (
            supabase_client.table("conversation_history")
            .select("role, content")  
            .eq("user_id", user_id)
            .order("timestamp", desc=True)
            .limit(limit)
            .execute()
        )

        if response.data:
            history = [{"role": msg["role"], "content": msg["content"]} for msg in reversed(response.data)]
            return history
        return []
    except Exception as e:
        logging.error(f"Error saat mengambil riwayat percakapan untuk user {user_id}: {e}", exc_info=True)
        return []

async def clear_user_conversation_history(user_id: int) -> bool:
    """Menghapus semua riwayat percakapan untuk pengguna tertentu."""
    if not supabase_client or not FEATURE_ENABLE_DATABASE:
        logging.debug("Supabase client tidak tersedia atau fitur database dinonaktifkan untuk clear_user_conversation_history.")
        return False

    try:
        response = (
            supabase_client.table("conversation_history")
            .delete()
            .eq("user_id", user_id)
            .execute()
        )
        if hasattr(response, 'error') and response.error:
            logging.error(f"Supabase error saat menghapus riwayat untuk user {user_id}: {response.error}")
            return False
        return True 
    except Exception as e:
        logging.error(f"Error saat menghapus riwayat percakapan untuk user {user_id}: {e}", exc_info=True)
        return False

async def get_user_selected_model(user_id: int) -> Optional[str]:
    """Mengambil model AI pilihan pengguna dari database."""
    if not supabase_client or not FEATURE_ENABLE_DATABASE:
        
        return None
    try:
        response = (
            supabase_client.table("user_preferences")
            .select("selected_gemini_model")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if response.data and response.data[0].get("selected_gemini_model"):
            return response.data[0].get("selected_gemini_model")
        return None 
    except Exception as e:
        logging.error(f"Error saat mengambil model pilihan pengguna {user_id} dari DB: {e}", exc_info=True)
        return None

async def set_user_selected_model(user_id: int, model_id: str) -> bool:
    """Menyimpan atau memperbarui model AI pilihan pengguna di database."""
    if not supabase_client or not FEATURE_ENABLE_DATABASE:
        
        return False
    try:
        
        response = (
            supabase_client.table("user_preferences")
            .update({"selected_gemini_model": model_id})
            .eq("user_id", user_id)
            .execute()
        )
        
        if hasattr(response, 'error') and response.error:
            logging.error(f"Supabase error saat update model pilihan untuk user {user_id}: {response.error}")
            return False
    
        logging.info(f"Model pilihan untuk user {user_id} diupdate ke {model_id}. Response data: {bool(response.data)}")
        return True
    except Exception as e:
        logging.error(f"Error saat menyimpan model pilihan pengguna {user_id} ke DB: {e}", exc_info=True)
        return False



async def check_and_update_chat_limit(user_id: int, daily_limit: int) -> Tuple[bool, int]:
    if not supabase_client or not FEATURE_ENABLE_DATABASE:
        logging.debug("Fitur DB nonaktif, chat limit tidak dicek.")
        return True, daily_limit 

    today = date.today()
    can_chat: bool
    remaining_chats: int
    current_db_count = 0 

    try:
        user_pref_response = (
            supabase_client.table("user_preferences")
            .select("daily_chat_count, last_chat_reset_date") 
            .eq("user_id", user_id)
            .maybe_single() 
            .execute()
        )

        user_data = None
        if hasattr(user_pref_response, 'data') and user_pref_response.data:
            user_data = user_pref_response.data
            if user_data.get("last_chat_reset_date"):
                try:
                    user_data["last_chat_reset_date"] = date.fromisoformat(user_data["last_chat_reset_date"])
                except (ValueError, TypeError):
                    logging.warning(f"Format tanggal salah untuk last_chat_reset_date user {user_id}, akan direset.")
                    user_data["last_chat_reset_date"] = today - timedelta(days=1) 

        db_update_payload: Optional[Dict[str, Any]] = None

        if not user_data: 
            logging.info(f"User {user_id} tidak ditemukan di user_preferences, membuat entri baru untuk chat limit.")
            db_update_payload = { 
                "user_id": user_id,
                "daily_chat_count": 1, 
                "last_chat_reset_date": today.isoformat(),
                
            }
            current_db_count = 0 
            can_chat = True
            remaining_chats = daily_limit - 1

            
            upsert_response = supabase_client.table("user_preferences").upsert(db_update_payload).execute()
            if hasattr(upsert_response, 'error') and upsert_response.error:
                logging.error(f"Gagal membuat/upsert entri user_preferences untuk user {user_id} saat cek limit: {upsert_response.error}")
                return True, daily_limit # Gagal DB, anggap bisa chat untuk UX
            db_update_payload = None # Sudah di-upsert, tidak perlu update lagi di bawah

        else: # Pengguna sudah ada
            current_db_count = user_data.get("daily_chat_count", 0)
            last_reset_date_from_db = user_data.get("last_chat_reset_date", today - timedelta(days=1)) # Anggap kemarin jika null

            if last_reset_date_from_db < today: 
                logging.info(f"Resetting daily chat count for user {user_id}. Old date: {last_reset_date_from_db}, today: {today}")
                current_db_count = 1 # Chat pertama hari ini
                db_update_payload = {
                    "daily_chat_count": current_db_count,
                    "last_chat_reset_date": today.isoformat()
                }
                can_chat = True
                remaining_chats = daily_limit - current_db_count
            else: # Masih hari yang sama
                if current_db_count < daily_limit:
                    current_db_count += 1
                    db_update_payload = {"daily_chat_count": current_db_count}
                    can_chat = True
                    remaining_chats = daily_limit - current_db_count
                else: # Sudah mencapai atau melewati limit hari ini (current_db_count >= daily_limit)
                    # current_db_count tetap, tidak diubah
                    db_update_payload = None # Tidak perlu update count jika sudah limit
                    can_chat = False
                    remaining_chats = 0

            if db_update_payload: 
                update_response = (
                    supabase_client.table("user_preferences")
                    .update(db_update_payload)
                    .eq("user_id", user_id)
                    .execute()
                )
                if hasattr(update_response, 'error') and update_response.error:
                    logging.error(f"Gagal update chat stats untuk user {user_id}: {update_response.error}")
                    # Pertimbangkan fallback jika update gagal
                    # Untuk saat ini, kita tetap pada `can_chat` yang sudah ditentukan
                    pass

        # current_db_count di sini adalah nilai setelah potensi increment atau reset
        logging.info(f"User {user_id}: can_chat={can_chat}, count={current_db_count}, limit={daily_limit}, remaining={remaining_chats}")
        return can_chat, remaining_chats

    except Exception as e:
        logging.error(f"Error di check_and_update_chat_limit untuk user {user_id}: {e}", exc_info=True)
        return True, daily_limit 
# --- AKHIR FUNGSI ---

async def get_user_chat_status_info(user_id: int) -> Optional[Dict[str, Any]]:
    """
    Mengambil daily_chat_count dan last_chat_reset_date untuk pengguna tertentu.
    Mengembalikan dict {'daily_chat_count': int, 'last_chat_reset_date': date} atau None.
    """
    if not supabase_client or not FEATURE_ENABLE_DATABASE:
        logging.debug("Supabase client tidak tersedia atau fitur database dinonaktifkan untuk get_user_chat_status_info.")
        return None # Atau kembalikan nilai default yang menandakan fitur nonaktif/error

    try:
        response = (
            supabase_client.table("user_preferences")
            .select("daily_chat_count, last_chat_reset_date")
            .eq("user_id", user_id)
            .maybe_single() # Gunakan maybe_single untuk handle jika user belum ada di tabel
            .execute()
        )

        if response.data:
            data = response.data
            # Konversi last_chat_reset_date dari string (YYYY-MM-DD) ke objek date
            if data.get("last_chat_reset_date"):
                try:
                    data["last_chat_reset_date"] = date.fromisoformat(data["last_chat_reset_date"])
                except (ValueError, TypeError):
                    logging.warning(f"Format tanggal salah untuk last_chat_reset_date (status) user {user_id}, mengembalikan None.")
                    # Kembalikan None atau tanggal default yang mengindikasikan perlu reset jika formatnya salah
                    return {"daily_chat_count": data.get("daily_chat_count", 0), "last_chat_reset_date": None}
            else: # Jika last_chat_reset_date null dari DB
                data["last_chat_reset_date"] = None

            return {
                "daily_chat_count": data.get("daily_chat_count", 0),
                "last_chat_reset_date": data["last_chat_reset_date"]
            }
        return None 
    except Exception as e:
        logging.error(f"Error saat mengambil status chat pengguna {user_id} dari DB: {e}", exc_info=True)
        return None

