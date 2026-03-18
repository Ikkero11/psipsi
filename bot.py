import os
import asyncio
import logging
import random
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List

import ephem
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    FSInputFile
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ========== КОНСТАНТЫ (можно менять прямо здесь) ==========
ADMIN_ID = 7498442456              # ⚠️ замените на свой Telegram ID
DB_NAME = "stress_bot.db"          # файл базы данных
IMAGES_FOLDER = "images"           # папка с картинками
POLL_QUESTIONS_COUNT = 4            # количество вопросов в опросе (должно совпадать с числом вопросов ниже)

# ========== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ==========
TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise ValueError("❌ Переменная окружения BOT_TOKEN не установлена! Задайте её на хостинге.")

# ========== ПРОВЕРКА НАЛИЧИЯ КАРТИНОК ==========
if not os.path.exists(IMAGES_FOLDER):
    os.makedirs(IMAGES_FOLDER)
    logging.warning(f"📁 Папка {IMAGES_FOLDER} создана. Положите в неё изображения для рассылки.")
image_files = []
for f in os.listdir(IMAGES_FOLDER):
    if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
        image_files.append(os.path.join(IMAGES_FOLDER, f))
if not image_files:
    logging.warning("🖼 В папке images нет изображений. Рассылка картинок работать не будет.")

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            registered DATE,
            points INTEGER DEFAULT 0,
            current_streak INTEGER DEFAULT 0,
            last_mood_date DATE,
            morning_time TEXT,
            evening_time TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS moods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            mood_score INTEGER,
            date DATE,
            time TIME,
            moon_phase REAL,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)
    conn.commit()
    conn.close()
    logging.info("✅ База данных инициализирована.")

def register_user(user_id: int, username: str, first_name: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO users (user_id, username, first_name, registered) VALUES (?, ?, ?, ?)",
        (user_id, username, first_name, datetime.now().date().isoformat())
    )
    conn.commit()
    conn.close()

def save_mood(user_id: int, score: int):
    today = datetime.now().date().isoformat()
    now_time = datetime.now().time().strftime("%H:%M")
    moon_phase = get_moon_phase(today)

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO moods (user_id, mood_score, date, time, moon_phase) VALUES (?, ?, ?, ?, ?)",
        (user_id, score, today, now_time, moon_phase)
    )
    # Обновляем статистику пользователя (очки, серии)
    cur.execute("SELECT last_mood_date, current_streak FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    last_date = row[0]
    streak = row[1] if row[1] else 0
    yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
    if last_date == yesterday:
        new_streak = streak + 1
    elif last_date == today:
        new_streak = streak
    else:
        new_streak = 1

    points_add = 10
    if new_streak > streak:
        points_add += 5

    cur.execute(
        "UPDATE users SET points = points + ?, current_streak = ?, last_mood_date = ? WHERE user_id = ?",
        (points_add, new_streak, today, user_id)
    )
    conn.commit()
    conn.close()

def get_user_stats(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT points, current_streak FROM users WHERE user_id = ?", (user_id,))
    user_row = cur.fetchone()
    points = user_row[0] if user_row else 0
    streak = user_row[1] if user_row else 0

    cur.execute("SELECT mood_score FROM moods WHERE user_id = ? ORDER BY date DESC LIMIT 7", (user_id,))
    recent = [row[0] for row in cur.fetchall()]
    conn.close()
    return points, streak, recent

def get_moon_phase(date_str: str) -> float:
    d = ephem.Date(date_str)
    moon = ephem.Moon()
    moon.compute(d)
    return moon.moon_phase

def set_user_notification_time(user_id: int, morning: Optional[str] = None, evening: Optional[str] = None):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    if morning is not None:
        cur.execute("UPDATE users SET morning_time = ? WHERE user_id = ?", (morning, user_id))
    if evening is not None:
        cur.execute("UPDATE users SET evening_time = ? WHERE user_id = ?", (evening, user_id))
    conn.commit()
    conn.close()

def get_users_for_notification(period: str, current_time: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    if period == "morning":
        cur.execute("SELECT user_id FROM users WHERE morning_time = ?", (current_time,))
    else:
        cur.execute("SELECT user_id FROM users WHERE evening_time = ?", (current_time,))
    users = [row[0] for row in cur.fetchall()]
    conn.close()
    return users

# ========== ОПРЕДЕЛЕНИЕ ВОПРОСОВ ДЛЯ ОПРОСА ==========
class PollQuestions:
    questions = [
        {
            "text": "Как ты оцениваешь свою усталость сегодня?",
            "options": [
                ("😎 Бодрячком", 1),
                ("😐 Нормально, но подустал(а)", 3),
                ("😴 Очень устал(а), еле двигаюсь", 5),
                ("💤 Полный ноль, хочу только спать", 5)
            ]
        },
        {
            "text": "Насколько ты доволен(льна) сегодняшним днём?",
            "options": [
                ("😍 Отлично!", 1),
                ("🙂 Неплохо", 2),
                ("😐 Так себе", 3),
                ("😞 Плохо", 4),
                ("😫 Ужасно", 5)
            ]
        },
        {
            "text": "Были ли сегодня ситуации, которые вызвали стресс или тревогу?",
            "options": [
                ("✅ Нет, всё спокойно", 1),
                ("⚠️ Были небольшие", 3),
                ("🔥 Да, сильно напрягали", 5)
            ]
        },
        {
            "text": "Как ты оцениваешь качество своего сна прошлой ночью?",
            "options": [
                ("💤 Выспался(ась) отлично", 1),
                ("😐 Средне", 3),
                ("😫 Плохо, почти не спал(а)", 5)
            ]
        }
    ]

# ========== СОСТОЯНИЯ FSM ==========
class NotificationSetup(StatesGroup):
    waiting_for_morning = State()
    waiting_for_evening = State()

class EveningPoll(StatesGroup):
    q1 = State()
    q2 = State()
    q3 = State()
    q4 = State()

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

# ========== КЛАВИАТУРЫ ==========
def main_menu_kb():
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text="📝 Пройти вечерний опрос"))
    builder.add(KeyboardButton(text="📊 Моя статистика"))
    builder.add(KeyboardButton(text="🌙 Фаза луны"))
    builder.add(KeyboardButton(text="⚙️ Настройки рассылки"))
    builder.adjust(2, 2)
    return builder.as_markup(resize_keyboard=True)

def build_poll_kb(options: List[tuple]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for text, value in options:
        builder.button(text=text, callback_data=f"poll_{value}")
    return builder.as_markup()

# ========== ОБРАБОТЧИКИ ==========
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user = message.from_user
    register_user(user.id, user.username, user.first_name)
    await message.answer(
        f"Привет, {user.first_name}! 🌟\n"
        "Этот бот поможет тебе отслеживать уровень стресса с помощью вечернего опроса.\n"
        "После ответов на несколько вопросов я рассчитаю твой уровень стресса и сохраню его.\n"
        "Используй меню ниже:",
        reply_markup=main_menu_kb()
    )

@dp.message(F.text == "📝 Пройти вечерний опрос")
async def start_poll(message: types.Message, state: FSMContext):
    today = datetime.now().date().isoformat()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id FROM moods WHERE user_id = ? AND date = ?", (message.from_user.id, today))
    if cur.fetchone():
        await message.answer("Ты уже проходил(а) опрос сегодня. Возвращайся завтра!")
        conn.close()
        return
    conn.close()
    await state.set_state(EveningPoll.q1)
    q = PollQuestions.questions[0]
    await message.answer(
        f"Вопрос 1/{POLL_QUESTIONS_COUNT}:\n{q['text']}",
        reply_markup=build_poll_kb(q['options'])
    )

@dp.callback_query(lambda c: c.data and c.data.startswith('poll_'))
async def handle_poll_answer(callback: CallbackQuery, state: FSMContext):
    score = int(callback.data.split('_')[1])
    current_state = await state.get_state()
    if current_state == EveningPoll.q1.state:
        await state.update_data(q1=score)
        await state.set_state(EveningPoll.q2)
        q = PollQuestions.questions[1]
        await callback.message.edit_text(
            f"Вопрос 2/{POLL_QUESTIONS_COUNT}:\n{q['text']}",
            reply_markup=build_poll_kb(q['options'])
        )
    elif current_state == EveningPoll.q2.state:
        await state.update_data(q2=score)
        await state.set_state(EveningPoll.q3)
        q = PollQuestions.questions[2]
        await callback.message.edit_text(
            f"Вопрос 3/{POLL_QUESTIONS_COUNT}:\n{q['text']}",
            reply_markup=build_poll_kb(q['options'])
        )
    elif current_state == EveningPoll.q3.state:
        await state.update_data(q3=score)
        await state.set_state(EveningPoll.q4)
        q = PollQuestions.questions[3]
        await callback.message.edit_text(
            f"Вопрос 4/{POLL_QUESTIONS_COUNT}:\n{q['text']}",
            reply_markup=build_poll_kb(q['options'])
        )
    elif current_state == EveningPoll.q4.state:
        data = await state.get_data()
        q1 = data.get('q1', 0)
        q2 = data.get('q2', 0)
        q3 = data.get('q3', 0)
        q4 = score
        total = q1 + q2 + q3 + q4
        # Преобразование суммы в шкалу 1-10
        stress_level = round((total - 4) * 9 / 16 + 1)
        user_id = callback.from_user.id
        save_mood(user_id, stress_level)
        await callback.message.edit_text(
            f"✅ Опрос завершён!\n"
            f"Твой уровень стресса сегодня: **{stress_level} из 10**.\n"
            f"Спасибо, что поделился(ась)! 😊"
        )
        phase = get_moon_phase(datetime.now().date().isoformat())
        await callback.message.answer(f"🌙 Сегодня фаза луны: {phase:.2f}")
        await state.clear()
    await callback.answer()

@dp.message(F.text == "📊 Моя статистика")
async def show_stats(message: types.Message):
    user_id = message.from_user.id
    points, streak, recent = get_user_stats(user_id)
    text = f"📈 **Твоя статистика**\n\n⭐ Очки: {points}\n🔥 Серия дней: {streak}\n\n"
    if recent:
        text += "Последние оценки стресса (от новых к старым):\n"
        text += " ".join(str(x) for x in recent)
    else:
        text += "Пока нет оценок стресса. Пройди опрос!"
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "🌙 Фаза луны")
async def show_moon(message: types.Message):
    today = datetime.now().date().isoformat()
    phase = get_moon_phase(today)
    if phase < 0.05:
        desc = "🌑 Новолуние"
    elif phase < 0.2:
        desc = "🌒 Молодая луна"
    elif phase < 0.3:
        desc = "🌓 Первая четверть"
    elif phase < 0.45:
        desc = "🌔 Растущая луна"
    elif phase < 0.55:
        desc = "🌕 Полнолуние"
    elif phase < 0.7:
        desc = "🌖 Убывающая луна"
    elif phase < 0.8:
        desc = "🌗 Последняя четверть"
    else:
        desc = "🌘 Старая луна"
    await message.answer(f"Сегодня {desc}\n(фаза: {phase:.2f})")

@dp.message(F.text == "⚙️ Настройки рассылки")
async def notification_settings(message: types.Message, state: FSMContext):
    await message.answer(
        "Я могу присылать тебе мотивационные картинки или мемы утром и вечером.\n"
        "Напиши время для **утренней** рассылки в формате ЧЧ:ММ (например, 08:00).\n"
        "Если не хочешь получать утром, отправь «нет»."
    )
    await state.set_state(NotificationSetup.waiting_for_morning)

@dp.message(NotificationSetup.waiting_for_morning)
async def set_morning_time(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text.lower() == "нет":
        morning = None
    else:
        try:
            datetime.strptime(text, "%H:%M")
            morning = text
        except ValueError:
            await message.answer("Неправильный формат. Попробуй ещё раз (ЧЧ:ММ) или отправь «нет».")
            return
    await state.update_data(morning=morning)
    await message.answer("Теперь напиши время для **вечерней** рассылки (ЧЧ:ММ) или «нет».")
    await state.set_state(NotificationSetup.waiting_for_evening)

@dp.message(NotificationSetup.waiting_for_evening)
async def set_evening_time(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text.lower() == "нет":
        evening = None
    else:
        try:
            datetime.strptime(text, "%H:%M")
            evening = text
        except ValueError:
            await message.answer("Неправильный формат. Попробуй ещё раз (ЧЧ:ММ) или отправь «нет».")
            return
    data = await state.get_data()
    morning = data.get("morning")
    user_id = message.from_user.id
    set_user_notification_time(user_id, morning, evening)
    await message.answer("Настройки сохранены! ✅", reply_markup=main_menu_kb())
    await state.clear()

# ========== РАССЫЛКА ПО РАСПИСАНИЮ ==========
async def send_motivation(user_id: int, period: str):
    if not image_files:
        return
    try:
        img_path = random.choice(image_files)
        photo = FSInputFile(img_path)
        caption = "Доброе утро!" if period == "morning" else "Добрый вечер! Как прошёл день?"
        await bot.send_photo(chat_id=user_id, photo=photo, caption=caption)
    except Exception as e:
        logging.error(f"Не удалось отправить картинку пользователю {user_id}: {e}")

async def check_notifications():
    now = datetime.now().strftime("%H:%M")
    for uid in get_users_for_notification("morning", now):
        await send_motivation(uid, "morning")
    for uid in get_users_for_notification("evening", now):
        await send_motivation(uid, "evening")

# ========== ПЛАНИРОВЩИК ==========
def setup_scheduler():
    scheduler.add_job(check_notifications, trigger="interval", minutes=1)
    scheduler.start()

# ========== ЗАПУСК ==========
async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    setup_scheduler()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
