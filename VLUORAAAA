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
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.media_group import MediaGroupBuilder

# --- ИМПОРТЫ ДЛЯ ВЕБХУКОВ ---
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

# Загружаем переменные (на Render они берутся из панели управления Dashboard)
load_dotenv()

API_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_GROUP_ID = int(os.getenv('ADMIN_GROUP_ID', 0))
SUPPORT_GROUP_ID = int(os.getenv('SUPPORT_GROUP_ID', 0))

# НАСТРОЙКИ RENDER
# Ссылка вида https://my-bot.onrender.com (берется из настроек Render)
BASE_WEBHOOK_URL = os.getenv('WEBHOOK_URL')
WEBHOOK_PATH = f"/webhook/{API_TOKEN}"
# Render сам назначает порт, мы просто его читаем
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = int(os.getenv('PORT', 8080))

if not API_TOKEN or not BASE_WEBHOOK_URL:
    raise ValueError("❌ ОШИБКА: Убедись, что BOT_TOKEN и WEBHOOK_URL установлены в Environment Variables на Render!")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

album_data = {}

# --- КЛАВИАТУРЫ ---
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🛍 Сделать заказ")],
        [KeyboardButton(text="❓ Частые вопросы"), KeyboardButton(text="🛠 Поддержка")]
    ],
    resize_keyboard=True
)

# --- FSM ---
class OrderForm(StatesGroup):
    waiting_for_item = State()
    waiting_for_details = State()

class SupportForm(StatesGroup):
    waiting_for_message = State()

# --- ХЭНДЛЕРЫ (БЕЗ ИЗМЕНЕНИЙ В ЛОГИКЕ) ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    if message.chat.type == 'private':
        await message.answer(
            f"💎 <b>Добро пожаловать в Bluora Store!</b>\n\nВоспользуйся меню ниже.",
            reply_markup=main_kb
        )
    else:
        await message.answer(f"ID этой группы: <code>{message.chat.id}</code>")

@dp.message(F.text == "❓ Частые вопросы")
async def faq_handler(message: types.Message):
    await message.answer("<b>1. Сколько занимает доставка?</b>\n- 2-3 недели.\n\n<b>2. Возврат?</b>\n- Да, 90% до получения, 50% после.")

@dp.message(F.text == "🛠 Поддержка")
async def support_handler(message: types.Message, state: FSMContext):
    await message.answer("Напиши свой вопрос одним сообщением:", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(SupportForm.waiting_for_message)

@dp.message(StateFilter(SupportForm.waiting_for_message))
async def process_support_message(message: types.Message, state: FSMContext):
    user = message.from_user
    text = message.text or message.caption or "Медиа"
    info = f"🆘 <b>ПОДДЕРЖКА</b>\n👤 {html.quote(user.full_name)}\n🆔 ID: <code>{user.id}</code>\n📝 {html.quote(text)}"
    
    try:
        if message.video: await bot.send_video(SUPPORT_GROUP_ID, message.video.file_id, caption=info)
        elif message.photo: await bot.send_photo(SUPPORT_GROUP_ID, message.photo[-1].file_id, caption=info)
        else: await bot.send_message(SUPPORT_GROUP_ID, info)
        await message.answer("✅ Отправлено!", reply_markup=main_kb)
    except Exception as e:
        await message.answer("❌ Ошибка отправки.")
    await state.clear()

@dp.message(F.text == "🛍 Сделать заказ")
async def start_order(message: types.Message, state: FSMContext):
    await message.answer("Пришли фото/видео или ссылку на товар:", reply_markup=types.ReplyKeyboardRemove())
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
            await asyncio.sleep(0.8) # Небольшая пауза для сбора альбома
            data = await state.get_data()
            await state.update_data(media_items=album_data.get(message.media_group_id, []))
            del album_data[message.media_group_id]
            await message.answer("Теперь напиши <b>размер, цвет и город</b>:")
            await state.set_state(OrderForm.waiting_for_details)
        else:
            if media_type: album_data[message.media_group_id].append({'type': media_type, 'id': file_id})
    else:
        await state.update_data(media_items=[{'type': media_type, 'id': file_id}] if media_type else [], item_text=item_desc)
        await message.answer("Теперь напиши <b>размер, цвет и город</b>:")
        await state.set_state(OrderForm.waiting_for_details)

@dp.message(StateFilter(OrderForm.waiting_for_details))
async def process_details(message: types.Message, state: FSMContext):
    data = await state.get_data()
    info = f"🛍 <b>ЗАКАЗ</b>\n👤 {html.quote(message.from_user.full_name)}\n🆔 ID: <code>{message.from_user.id}</code>\n📝 {data['item_text']}\n📏 {message.text}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Взять", callback_data="take_order")]])

    try:
        items = data.get('media_items', [])
        if len(items) > 1:
            mg = MediaGroupBuilder()
            for i in items:
                if i['type'] == 'photo': mg.add_photo(i['id'])
                else: mg.add_video(i['id'])
            await bot.send_media_group(ADMIN_GROUP_ID, mg.build())
            await bot.send_message(ADMIN_GROUP_ID, info, reply_markup=kb)
        elif len(items) == 1:
            if items[0]['type'] == 'photo': await bot.send_photo(ADMIN_GROUP_ID, items[0]['id'], caption=info, reply_markup=kb)
            else: await bot.send_video(ADMIN_GROUP_ID, items[0]['id'], caption=info, reply_markup=kb)
        else:
            await bot.send_message(ADMIN_GROUP_ID, info, reply_markup=kb)
        await message.answer("✅ Заказ отправлен!", reply_markup=main_kb)
    except Exception as e:
        logging.error(f"Error: {e}")
    await state.clear()

@dp.callback_query(F.data == "take_order")
async def take_order(c: CallbackQuery):
    await c.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"В работе: {c.from_user.first_name}", callback_data="none")]]))
    await c.answer("Удачи!")

@dp.message(F.chat.id.in_({ADMIN_GROUP_ID, SUPPORT_GROUP_ID}), F.reply_to_message)
async def admin_reply(message: types.Message):
    orig = message.reply_to_message.text or message.reply_to_message.caption
    match = re.search(r"ID:\s+(\d+)", orig)
    if match:
        user_id = int(match.group(1))
        role = "Менеджера" if message.chat.id == ADMIN_GROUP_ID else "Поддержки"
        try:
            await bot.send_message(user_id, f"👩‍💻 <b>Ответ от {role}:</b>\n\n{message.text}")
            await message.reply("Отправлено!")
        except:
            await message.reply("Ошибка (блок бота?)")

# --- ЗАПУСК ---
async def on_startup(bot: Bot):
    # Устанавливаем вебхук и удаляем старые обновления
    await bot.set_webhook(
        f"{BASE_WEBHOOK_URL.rstrip('/')}{WEBHOOK_PATH}",
        drop_pending_updates=True 
    )

def main():
    dp.startup.register(on_startup)
    app = web.Application()
    
    # Настройка хэндлера вебхука
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    
    setup_application(app, dp, bot=bot)
    
    web.run_app(app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)

if __name__ == "__main__":
    main()
