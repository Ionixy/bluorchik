import asyncio
import logging
import os
import re
import warnings
from dotenv import load_dotenv

from aiohttp import web # Добавили импорт для микро-сервера Render
from aiogram import Bot, Dispatcher, types, F, html
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.media_group import MediaGroupBuilder

# Глушим предупреждение Pydantic
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

# Загружаем переменные окружения (для локальных тестов). 
# На Render файл .env не нужен, переменные подтянутся из настроек дашборда.
load_dotenv()

API_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_GROUP_ID_STR = os.getenv('ADMIN_GROUP_ID')
SUPPORT_GROUP_ID_STR = os.getenv('SUPPORT_GROUP_ID')

if not API_TOKEN or not ADMIN_GROUP_ID_STR or not SUPPORT_GROUP_ID_STR:
    raise ValueError("❌ ОШИБКА: В переменных окружения отсутствуют BOT_TOKEN, ADMIN_GROUP_ID или SUPPORT_GROUP_ID.")

ADMIN_GROUP_ID = int(ADMIN_GROUP_ID_STR)
SUPPORT_GROUP_ID = int(SUPPORT_GROUP_ID_STR)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Инициализация бота без прокси
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# Словарь для временного хранения альбомов фото/видео
album_data = {}

# --- КЛАВИАТУРЫ ---
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🛍 Сделать заказ")],
        [KeyboardButton(text="❓ Частые вопросы"), KeyboardButton(text="🛠 Поддержка")]
    ],
    resize_keyboard=True
)

# --- FSM (МАШИНА СОСТОЯНИЙ) ---
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
    await message.answer("Частые вопросы и ответы : \n\n<b>1. Сколько занимает доставка?</b>\n- В среднем заказ идет от 2-3 недель после покупки. В случае праздников время может достигать до 1 месяца\n\n<b>2. Возможен ли возврат ?</b>\nДа, возможен!\n- Если товар был доставлен на склад после чего вы отказываетесь от него, вы получаете <b>90%</b> от суммы товара\n- Если товар уже прибыл к заказчику и он от него отказывается, заказчик получает <b>50%</b> от суммы товара взамен на сам товар\n\nВ будущем список будет пополняться")

# --- БЛОК ПОДДЕРЖКИ ---
@dp.message(F.text == "🛠 Поддержка")
async def support_handler(message: types.Message, state: FSMContext):
    await message.answer(
        "Напиши свой вопрос или подробно опиши проблему одним сообщением (можно прикрепить фото или видео), и наш менеджер ответит тебе в ближайшее время:",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(SupportForm.waiting_for_message)

@dp.message(StateFilter(SupportForm.waiting_for_message))
async def process_support_message(message: types.Message, state: FSMContext):
    user = message.from_user
    user_link = f"<a href='tg://user?id={user.id}'>{html.quote(user.full_name)}</a>"
    username = f"@{user.username}" if user.username else "Скрыт"

    # Текст или подпись к медиа
    support_text_content = message.text or message.caption or "Без текста (только медиа)"

    support_info = (
        f"🆘 <b>НОВЫЙ ВОПРОС В ПОДДЕРЖКУ</b>\n\n"
        f"👤 <b>От:</b> {user_link}\n"
        f"🔗 <b>Юзер:</b> {username}\n"
        f"🆔 <b>ID:</b> {user.id}\n\n"
        f"📝 <b>Сообщение:</b>\n{html.quote(support_text_content)}"
    )

    try:
        if message.video:
            await bot.send_video(chat_id=SUPPORT_GROUP_ID, video=message.video.file_id, caption=support_info)
        elif message.photo:
            await bot.send_photo(chat_id=SUPPORT_GROUP_ID, photo=message.photo[-1].file_id, caption=support_info)
        else:
            await bot.send_message(chat_id=SUPPORT_GROUP_ID, text=support_info)

        await message.answer("✅ Твое сообщение успешно отправлено в поддержку! Ожидай ответа.", reply_markup=main_kb)
    except Exception as e:
        logging.error(f"Ошибка отправки в поддержку: {e}")
        await message.answer("❌ Произошла ошибка. Попробуй позже.", reply_markup=main_kb)

    await state.clear()


# --- БЛОК ЗАКАЗОВ ---
@dp.message(F.text == "🛍 Сделать заказ")
async def start_order(message: types.Message, state: FSMContext):
    await message.answer(
        "Отлично! Отправь мне фото или видео товара (можно сразу несколько) или напиши его название/ссылку.",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(OrderForm.waiting_for_item)

@dp.message(StateFilter(OrderForm.waiting_for_item))
async def process_item(message: types.Message, state: FSMContext):
    item_desc = message.caption or message.text or "Без описания"

    media_type = None
    file_id = None

    if message.video:
        media_type = 'video'
        file_id = message.video.file_id
    elif message.photo:
        media_type = 'photo'
        file_id = message.photo[-1].file_id

    if message.media_group_id:
        if message.media_group_id not in album_data:
            if media_type:
                album_data[message.media_group_id] = [{'type': media_type, 'id': file_id}]

            await state.update_data(item_text=item_desc)
            await asyncio.sleep(1)

            await state.update_data(media_items=album_data.get(message.media_group_id, []))
            if message.media_group_id in album_data:
                del album_data[message.media_group_id]

            await message.answer("Супер! Теперь напиши нужный <b>размер, цвет</b>, также <b>город</b> для доставки:")
            await state.set_state(OrderForm.waiting_for_details)
        else:
            if media_type:
                album_data[message.media_group_id].append({'type': media_type, 'id': file_id})

    elif media_type:
        await state.update_data(media_items=[{'type': media_type, 'id': file_id}], item_text=item_desc)
        await message.answer("Супер! Теперь напиши нужный <b>размер, цвет</b>, также <b>город</b> для доставки:")
        await state.set_state(OrderForm.waiting_for_details)

    else:
        await state.update_data(media_items=[], item_text=item_desc)
        await message.answer("Супер! Теперь напиши нужный <b>размер, цвет</b>, также <b>город</b> для доставки:")
        await state.set_state(OrderForm.waiting_for_details)

@dp.message(StateFilter(OrderForm.waiting_for_details))
async def process_details(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    media_items = user_data.get('media_items', [])
    item_text = user_data.get('item_text', 'Без описания')
    details = message.text
    user = message.from_user

    user_link = f"<a href='tg://user?id={user.id}'>{html.quote(user.full_name)}</a>"
    username = f"@{user.username}" if user.username else "Скрыт"

    order_info = (
        f"🛍 <b>НОВЫЙ ЗАКАЗ</b>\n\n"
        f"👤 <b>От:</b> {user_link}\n"
        f"🔗 <b>Юзер:</b> {username}\n"
        f"🆔 <b>ID:</b> {user.id}\n\n"
        f"📝 <b>Товар:</b>\n{html.quote(item_text)}\n\n"
        f"📏 <b>Детали/Размер:</b>\n{html.quote(details)}"
    )

    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Взять в работу", callback_data="take_order")]
    ])

    try:
        if len(media_items) > 1:
            media_group = MediaGroupBuilder()
            for item in media_items:
                if item['type'] == 'photo':
                    media_group.add_photo(media=item['id'])
                elif item['type'] == 'video':
                    media_group.add_video(media=item['id'])

            await bot.send_media_group(chat_id=ADMIN_GROUP_ID, media=media_group.build())
            await bot.send_message(chat_id=ADMIN_GROUP_ID, text=order_info, reply_markup=admin_kb)

        elif len(media_items) == 1:
            item = media_items[0]
            if item['type'] == 'photo':
                await bot.send_photo(chat_id=ADMIN_GROUP_ID, photo=item['id'], caption=order_info, reply_markup=admin_kb)
            elif item['type'] == 'video':
                await bot.send_video(chat_id=ADMIN_GROUP_ID, video=item['id'], caption=order_info, reply_markup=admin_kb)

        else:
            await bot.send_message(chat_id=ADMIN_GROUP_ID, text=order_info, reply_markup=admin_kb)

        await message.answer("✅ Запрос успешно отправлен! Менеджер скоро свяжется с тобой.", reply_markup=main_kb)
    except Exception as e:
        logging.error(f"Ошибка отправки заказа: {e}")
        await message.answer("❌ Произошла ошибка. Попробуй позже или напиши нам напрямую.", reply_markup=main_kb)

    await state.clear()

# --- ХЭНДЛЕРЫ МЕНЕДЖЕРОВ И ПОДДЕРЖКИ ---
@dp.callback_query(F.data == "take_order")
async def take_order_handler(callback: CallbackQuery):
    admin_name = callback.from_user.full_name
    new_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"В работе у: {admin_name}", callback_data="already_taken")]
    ])
    await callback.message.edit_reply_markup(reply_markup=new_kb)
    await callback.answer("Ты взял заявку в работу!")

@dp.callback_query(F.data == "already_taken")
async def already_taken_handler(callback: CallbackQuery):
    await callback.answer("Эту заявку уже забрал другой сотрудник!", show_alert=True)

# Хэндлер для ответа клиенту из ЛЮБОЙ админской группы
@dp.message(F.chat.id.in_({ADMIN_GROUP_ID, SUPPORT_GROUP_ID}), F.reply_to_message)
async def admin_reply_to_user(message: types.Message):
    original_text = message.reply_to_message.text or message.reply_to_message.caption
    if not original_text:
        return

    match = re.search(r"🆔 ID:\s+(\d+)", original_text)
    if match:
        user_id = int(match.group(1))

        # Определяем, откуда ответ
        sender_role = "Менеджера" if message.chat.id == ADMIN_GROUP_ID else "Службы поддержки"

        try:
            await bot.send_message(
                chat_id=user_id,
                text=f"👩‍💻 <b>Сообщение от {sender_role}:</b>\n\n{html.quote(message.text)}"
            )
            await message.reply("✅ Твой ответ отправлен клиенту!")
        except Exception as e:
            logging.error(f"Не удалось отправить ответ клиенту {user_id}: {e}")
            await message.reply("❌ Не получилось отправить. Возможно, клиент заблокировал бота.")


# --- ФИКТИВНЫЙ ВЕБ-СЕРВЕР ДЛЯ RENDER ---
async def handle_ping(request):
    return web.Response(text="Bot is running and port is bound!")

async def start_dummy_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Render сам передает нужный порт через переменную окружения PORT
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"Фиктивный веб-сервер запущен на порту {port}")

# --- ЗАПУСК ---
async def main():
    logging.info("--- Бот Bluora запущен на Render (Free Tier) ---")
    
    # 1. Запускаем веб-заглушку, чтобы закрыть требование открытого порта от Render
    await start_dummy_server()

    # 2. Удаляем старый вебхук на случай, если он висит в Telegram
    await bot.delete_webhook(drop_pending_updates=True)
    
    # 3. Запускаем long-polling бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен")
