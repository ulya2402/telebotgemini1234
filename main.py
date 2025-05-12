import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode 
from aiogram.client.default import DefaultBotProperties

from core.config import TELEGRAM_BOT_TOKEN
from bot.handlers import router as main_router

async def main():
    if not TELEGRAM_BOT_TOKEN:
        logging.error("Tidak ada TELEGRAM_BOT_TOKEN. Bot tidak bisa dijalankan.")
        return

    default_props = DefaultBotProperties(parse_mode=ParseMode.MARKDOWN) 
    bot = Bot(token=TELEGRAM_BOT_TOKEN, default=default_props)

    dp = Dispatcher()

    dp.include_router(main_router)

    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        force=True,
        format='%(asctime)s - %(levelname)s - %(name)s - [%(filename)s:%(lineno)d] - %(message)s'
    )

    await bot.delete_webhook(drop_pending_updates=True)

    logging.info(f"Memulai polling bot dengan parse mode default: {bot.default.parse_mode}")
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Error saat polling: {e}", exc_info=True)
    finally:
        await bot.session.close()
        logging.info("Sesi bot ditutup.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot dihentikan.")