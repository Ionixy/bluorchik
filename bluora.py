import asyncio
import logging
import os
import re
import warnings
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F, html
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.media_group import MediaGroupBuilder

# Импорты для вебхуков и сервера
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# Игнорируем лишние предупреждения Pydantic
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

# Загрузка переменных (локально из файла, на Render из панели управления)
load_dotenv()

# --- КОНФИГУРАЦИЯ ---
API_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_GROUP_ID = int(os.getenv('ADMIN_GROUP_ID', 0))
SUPPORT_GROUP_ID = int(os.getenv('SUPPORT_GROUP_ID', 0))

# Настройки Render
BASE_WEBHOOK_URL = os.getenv('WEBHOOK_URL') # Например: https://my-bot.onrender.com
WEBHOOK_PATH = f"/webhook/{API_TOKEN}"
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = int(os.getenv('PORT', 8080))

if not API_TOKEN or not BASE_WEBHOOK_URL:
    exit("❌ ОШИБКА: Установи BOT_TOKEN и WEBHOOK_URL в настройках Render!")

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Инициализация бота и диспетчера
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# Временное хранилище для альбомов
album_data = {}

# --- КЛАВИАТУРЫ ---
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🛍 Сделать заказ")],
        [KeyboardButton(text="❓ Частые вопросы"), KeyboardButton(text="🛠 Поддержка")]
    ],
    resize_keyboard=True
)

# --- МАШИНА СОСТОЯНИЙ (FSM) ---
class OrderForm(StatesGroup):
    waiting_for_item = State()
    waiting_for_details = State()

class SupportForm(StatesGroup):
    waiting_for_message = State()

# --- ХЭНДЛЕРЫ КЛИЕНТА ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    if message.chat.type == 'private':
        await message.answer(
            f"💎 <b>Добро пожаловать в Bluora Store!</b>\n\n"
            f"Воспользуйся меню ниже, чтобы сделать заказ или задать вопрос.",
            reply_markup=main_kb
        )
    else:
        await message.answer(f"ID этой группы: <code>{message.chat.id}</code>")

@dp.message(F.text == "❓ Частые вопросы")
async def faq_handler(message: types.Message):
    text = (
        "<b>1. Сколько занимает доставка?</b>\n- В среднем 2-3 недели.\n\n"
        "<b>2. Возможен ли возврат?</b>\n- Да, 90% до склада, 50% после получения."
    )
    await message.answer(text)

# --- ЛОГИКА ПОДДЕРЖКИ ---
@dp.message(F.text == "🛠 Поддержка")
async def support_handler(message: types.Message, state: FSMContext):
    await message.answer("Напиши свой вопрос (можно с фото/видео) одним сообщением:", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(SupportForm.waiting_for_message)

@dp.message(StateFilter(SupportForm.waiting_for_message))
async def process_support_message(message: types.Message, state: FSMContext):
    user = message.from_user
    text = message.text or message.caption or "Без текста"
    info = (
        f"🆘 <b>ВОПРОС В ПОДДЕРЖКУ</b>\n"
        f"👤 От: {html.quote(user.full_name)}\n"
        f"🆔 ID: <code>{user.id}</code>\n\n"
        f"📝 Сообщение: {html.quote(text)}"
    )
    
    try:
        if message.video:
            await bot.send_video(SUPPORT_GROUP_ID, message.video.file_id, caption=info)
        elif message.photo:
            await bot.send_photo(SUPPORT_GROUP_ID, message.photo[-1].file_id, caption=info)
        else:
            await bot.send_message(SUPPORT_GROUP_ID, info)
        await message.answer("✅ Отправлено в поддержку!", reply_markup=main_kb)
    except Exception as e:
        logging.error(f"Support error: {e}")
        await message.answer("❌ Ошибка при отправке.")
    await state.clear()

# --- ЛОГИКА ЗАКАЗОВ ---
@dp.message(F.text == "🛍 Сделать заказ")
async def start_order(message: types.Message, state: FSMContext):
    await message.answer("Пришли фото/видео товара или ссылку:", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(OrderForm.waiting_for_item)

@dp.message(StateFilter(OrderForm.waiting_for_item))
async def process_item(message: types.Message, state: FSMContext):
    item_desc = message.caption or message.text or "Без описания"
    media_type = 'video' if message.video else ('photo' if message.photo else None)
    file_id = message.video.file_id if message.video else (message.photo[-1].file_id if message.photo else None)

    if message.media_group_id:
        if message.media_group_id not in album_data:
            album_data[message.media_group_id] = [{'type': media_type, 'id': file_id}] if media_type else []
            await state.update_data(item_text=item_desc)
            await asyncio.sleep(1) # Ждем сбора всех медиа в группе
            
            data = await state.get_data()
            await state.update_data(media_items=album_data.get(message.media_group_id, []))
            if message.media_group_id in album_data: del album_data[message.media_group_id]
            
            await message.answer("Супер! Теперь напиши нужный <b>размер, цвет и город</b>:")
            await state.set_state(OrderForm.waiting_for_details)
        else:
            if media_type:
                album_data[message.media_group_id].append({'type': media_type, 'id': file_id})
    else:
        await state.update_data(media_items=[{'type': media_type, 'id': file_id}] if media_type else [], item_text=item_desc)
        await message.answer("Супер! Теперь напиши нужный <b>размер, цвет и город</b>:")
        await state.set_state(OrderForm.waiting_for_details)

@dp.message(StateFilter(OrderForm.waiting_for_details))
async def process_details(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    media_items = user_data.get('media_items', [])
    info = (
        f"🛍 <b>НОВЫЙ ЗАКАЗ</b>\n"
        f"👤 От: {html.quote(message.from_user.full_name)}\n"
        f"🆔 ID: <code>{message.from_user.id}</code>\n\n"
        f"📝 Товар: {html.quote(user_data['item_text'])}\n"
        f"📏 Детали: {html.quote(message.text)}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Взять в работу", callback_data="take_order")]])

    try:
        if len(media_items) > 1:
            mg = MediaGroupBuilder()
            for i in media_items:
                if i['type'] == 'photo': mg.add_photo(media=i['id'])
                else: mg.add_video(media=i['id'])
            await bot.send_media_group(ADMIN_GROUP_ID, media=mg.build())
            await bot.send_message(ADMIN_GROUP_ID, info, reply_markup=kb)
        elif len(media_items) == 1:
            i = media_items[0]
            if i['type'] == 'photo': await bot.send_photo(ADMIN_GROUP_ID, i['id'], caption=info, reply_markup=kb)
            else: await bot.send_video(ADMIN_GROUP_ID, i['id'], caption=info, reply_markup=kb)
        else:
            await bot.send_message(ADMIN_GROUP_ID, info, reply_markup=kb)
        
        await message.answer("✅ Заказ успешно отправлен!", reply_markup=main_kb)
    except Exception as e:
        logging.error(f"Order send error: {e}")
        await message.answer("❌ Ошибка при оформлении.")
    
    await state.clear()

# --- ОБРАБОТКА В ГРУППАХ ---

@dp.callback_query(F.data == "take_order")
async def take_order(callback: CallbackQuery):
    name = callback.from_user.first_name
    await callback.message.edit_reply_markup(
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"В работе у: {name}", callback_data="none")]
        ])
    )
    await callback.answer("Заявка принята!")

@dp.message(F.chat.id.in_({ADMIN_GROUP_ID, SUPPORT_GROUP_ID}), F.reply_to_message)
async def admin_reply(message: types.Message):
    orig_text = message.reply_to_message.text or message.reply_to_message.caption
    if not orig_text: return

    match = re.search(r"🆔 ID:\s+(\d+)", orig_text)
    if match:
        user_id = int(match.group(1))
        role = "Менеджера" if message.chat.id == ADMIN_GROUP_ID else "Поддержки"
        try:
            await bot.send_message(user_id, f"👩‍💻 <b>Ответ от {role}:</b>\n\n{message.text}")
            await message.reply("✅ Ответ доставлен.")
        except:
            await message.reply("❌ Не удалось отправить (блок?).")

# --- СЛУЖЕБНЫЕ ФУНКЦИИ ---

async def on_startup(bot: Bot):
    webhook_url = f"{BASE_WEBHOOK_URL.rstrip('/')}{WEBHOOK_PATH}"
    await bot.set_webhook(webhook_url, drop_pending_updates=True)
    logging.info(f"🚀 Вебхук запущен: {webhook_url}")

async def on_shutdown(bot: Bot):
    logging.info("💤 Закрытие сессий...")
    await bot.session.close()

def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()
    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    web.run_app(app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)

if __name__ == "__main__":
    main()
