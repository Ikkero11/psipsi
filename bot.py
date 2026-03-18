import os
import asyncio
import logging
import random
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Tuple

import ephem
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    KeyboardButton,
    InlineKeyboardMarkup,
    CallbackQuery,
    FSInputFile,
    ReplyKeyboardMarkup
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ========== КОНСТАНТЫ ==========
ADMIN_ID = 123456789              # ⚠️ ЗАМЕНИТЕ НА СВОЙ ID
DB_NAME = "stress_bot.db"
IMAGES_FOLDER = "images"
MOON_PHOTOS_FOLDER = "moon_photos"
FACTS_DAY_FILE = "facts_day.txt"
POLL_QUESTIONS_COUNT = 8

# ========== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ==========
TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise ValueError("❌ Переменная окружения BOT_TOKEN не установлена!")

# ========== ЗАГРУЗКА ФАКТОВ ==========
def load_facts(filename: str) -> List[str]:
    facts = []
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            facts = [line.strip() for line in f if line.strip()]
    else:
        with open(filename, "w", encoding="utf-8") as f:
            sample = [
                "Интересный факт: Осьминоги имеют три сердца.",
                "Факт: Бананы радиоактивны из-за содержания калия-40.",
                "Знаете ли вы? Группа крови влияет на предрасположенность к стрессу."
            ]
            f.write("\n".join(sample))
            facts = sample
        logging.warning(f"Файл {filename} не найден, создан с примерами.")
    return facts

facts_day = load_facts(FACTS_DAY_FILE)

# ========== ПРОВЕРКА НАЛИЧИЯ КАРТИНОК ==========
if not os.path.exists(IMAGES_FOLDER):
    os.makedirs(IMAGES_FOLDER)
    logging.warning(f"📁 Папка {IMAGES_FOLDER} создана. Положите в неё изображения.")
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
            gender TEXT,                -- пол: 'male', 'female', 'other'
            registered DATE,
            points INTEGER DEFAULT 0,
            current_streak INTEGER DEFAULT 0,
            last_mood_date DATE,
            poll_time TEXT,
            last_breathing DATE,
            first_poll_done INTEGER DEFAULT 0,
            consecutive_red_days INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS moods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            raw_sum INTEGER,
            zone TEXT,
            date DATE,
            time TIME,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mood_details (
            mood_id INTEGER,
            q1 INTEGER, q2 INTEGER, q3 INTEGER, q4 INTEGER,
            q5 INTEGER, q6 INTEGER, q7 INTEGER, q8 INTEGER,
            FOREIGN KEY(mood_id) REFERENCES moods(id)
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

def set_user_gender(user_id: int, gender: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE users SET gender = ? WHERE user_id = ?", (gender, user_id))
    conn.commit()
    conn.close()

def get_user_gender(user_id: int) -> Optional[str]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT gender FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

def has_previous_moods(user_id: int) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM moods WHERE user_id = ?", (user_id,))
    count = cur.fetchone()[0]
    conn.close()
    return count > 0

def set_first_poll_done(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE users SET first_poll_done = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def save_mood_with_details(user_id: int, answers: List[int]):
    today = datetime.now().date().isoformat()
    now_time = datetime.now().time().strftime("%H:%M")
    raw_sum = sum(answers)

    if raw_sum <= 16:
        zone = 'green'
    elif raw_sum <= 28:
        zone = 'yellow'
    else:
        zone = 'red'

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO moods (user_id, raw_sum, zone, date, time)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, raw_sum, zone, today, now_time))
    mood_id = cur.lastrowid

    cur.execute("""
        INSERT INTO mood_details (mood_id, q1, q2, q3, q4, q5, q6, q7, q8)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (mood_id, *answers))

    cur.execute("SELECT last_mood_date, current_streak, consecutive_red_days FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    last_date = row[0]
    streak = row[1] if row[1] else 0
    consecutive_red = row[2] if row[2] else 0

    yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
    if last_date == yesterday:
        new_streak = streak + 1
    elif last_date == today:
        new_streak = streak
    else:
        new_streak = 1

    if zone == 'red':
        new_consecutive_red = consecutive_red + 1
    else:
        new_consecutive_red = 0

    cur.execute("""
        UPDATE users SET
            points = points + 15,
            current_streak = ?,
            last_mood_date = ?,
            consecutive_red_days = ?
        WHERE user_id = ?
    """, (new_streak, today, new_consecutive_red, user_id))

    conn.commit()
    conn.close()

    # Проверка триггеров (ответ 5 в вопросах 5 или 8)
    if answers[4] == 5 or answers[7] == 5:
        asyncio.create_task(notify_admin_trigger(user_id, answers))

    # Проверка длительной красной зоны
    if new_consecutive_red >= 3:
        asyncio.create_task(notify_user_red_streak(user_id, new_consecutive_red))
        asyncio.create_task(notify_admin_red_streak(user_id, new_consecutive_red))

    return zone, raw_sum

async def notify_admin_trigger(user_id: int, answers: List[int]):
    try:
        gender = get_user_gender(user_id) or "не указан"
        text = (f"⚠️ Триггерный ответ у пользователя {user_id} (пол: {gender})\n"
                f"Вопрос 5 (эмоции): {answers[4]}\n"
                f"Вопрос 8 (контроль): {answers[7]}")
        await bot.send_message(chat_id=ADMIN_ID, text=text)
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление админу: {e}")

async def notify_admin_red_streak(user_id: int, days: int):
    try:
        gender = get_user_gender(user_id) or "не указан"
        text = f"🔴 Пользователь {user_id} (пол: {gender}) в красной зоне {days} дня подряд."
        await bot.send_message(chat_id=ADMIN_ID, text=text)
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление админу: {e}")

async def notify_user_red_streak(user_id: int, days: int):
    try:
        text = ("Я замечаю, что тебе трудно уже несколько дней. "
                "Возможно, стоит обсудить это со специалистом или близким человеком? 🫂")
        await bot.send_message(chat_id=user_id, text=text)
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление пользователю: {e}")

def get_user_stats(user_id: int) -> Tuple[int, int, List[Tuple[int, str]]]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT points, current_streak FROM users WHERE user_id = ?", (user_id,))
    user_row = cur.fetchone()
    points = user_row[0] if user_row else 0
    streak = user_row[1] if user_row else 0

    cur.execute("SELECT raw_sum, zone FROM moods WHERE user_id = ? ORDER BY date DESC LIMIT 7", (user_id,))
    recent = [(row[0], row[1]) for row in cur.fetchall()]
    conn.close()
    return points, streak, recent

def set_user_poll_time(user_id: int, poll_time: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE users SET poll_time = ? WHERE user_id = ?", (poll_time, user_id))
    conn.commit()
    conn.close()

def get_users_by_poll_time(current_time: str) -> List[int]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE poll_time = ?", (current_time,))
    users = [row[0] for row in cur.fetchall()]
    conn.close()
    return users

def get_all_users() -> List[int]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users")
    users = [row[0] for row in cur.fetchall()]
    conn.close()
    return users

def get_admin_stats():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]

    week_ago = (datetime.now().date() - timedelta(days=7)).isoformat()
    cur.execute("SELECT COUNT(DISTINCT user_id) FROM moods WHERE date >= ?", (week_ago,))
    active_users = cur.fetchone()[0]

    cur.execute("SELECT AVG(raw_sum) FROM moods WHERE date >= ?", (week_ago,))
    avg_raw = cur.fetchone()[0] or 0

    cur.execute("SELECT zone, COUNT(*) FROM moods WHERE date >= ? GROUP BY zone", (week_ago,))
    zone_dist = cur.fetchall()

    # Статистика по полу
    cur.execute("SELECT gender, COUNT(*) FROM users GROUP BY gender")
    gender_stats = cur.fetchall()

    conn.close()
    return total_users, active_users, avg_raw, zone_dist, gender_stats

def has_today_mood(user_id: int) -> bool:
    today = datetime.now().date().isoformat()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id FROM moods WHERE user_id = ? AND date = ?", (user_id, today))
    exists = cur.fetchone() is not None
    conn.close()
    return exists

def add_points(user_id: int, points: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (points, user_id))
    conn.commit()
    conn.close()

def can_take_breathing(user_id: int) -> bool:
    today = datetime.now().date().isoformat()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT last_breathing FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if row and row[0] == today:
        conn.close()
        return False
    conn.close()
    return True

def mark_breathing_done(user_id: int):
    today = datetime.now().date().isoformat()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_breathing = ? WHERE user_id = ?", (today, user_id))
    conn.commit()
    conn.close()

def get_moon_phase(date_str: str) -> float:
    d = ephem.Date(date_str)
    moon = ephem.Moon()
    moon.compute(d)
    return moon.moon_phase

# ========== ВОПРОСЫ ОПРОСА ==========
class PollQuestions:
    questions = [
        {
            "text": "Как чувствует себя твоё тело?",
            "hint": "1 — полностью расслаблен, 5 — всё зажато и болит"
        },
        {
            "text": "Насколько тебя сегодня 'грузят' уведомления и новости?",
            "hint": "1 — вообще не замечаю, 5 — тотальный перегруз"
        },
        {
            "text": "Много ли в голове крутится мыслей 'а что если...'?",
            "hint": "1 — в голове чисто, 5 — мысли не остановить"
        },
        {
            "text": "Как с концентрацией на делах?",
            "hint": "1 — я в потоке, 5 — туман в голове, не могу собраться"
        },
        {
            "text": "Твой общий вайб сегодня?",
            "hint": "1 — спокойствие и радость, 5 — на грани срыва"
        },
        {
            "text": "Как самочувствие после сна?",
            "hint": "1 — бодр и свеж, 5 — состояние зомби"
        },
        {
            "text": "Готов(а) к общению с людьми?",
            "hint": "1 — заряжен на 100%, 5 — хочу исчезнуть"
        },
        {
            "text": "Чувствуешь, что управляешь своей жизнью сегодня?",
            "hint": "1 — полностью рулю, 5 — вокруг полный хаос"
        }
    ]

# ========== ПРЕДОСТЕРЕЖЕНИЯ О ЛУНЕ ==========
moon_disclaimers = [
    "* Хотя некоторые люди замечают изменения в самочувствии в полнолуние, научные исследования не подтверждают прямого влияния луны на стресс.",
    "* Связь фаз луны с эмоциональным состоянием — скорее фольклор, чем доказанный факт.",
    "* Не принимайте фазу луны как руководство к действию — ваш стресс зависит от многих реальных факторов.",
    "* Исследования показывают, что вера в влияние луны может создавать эффект самовнушения, но объективной связи нет."
]

# ========== СОСТОЯНИЯ FSM ==========
class Registration(StatesGroup):
    waiting_for_gender = State()

class TimeSetup(StatesGroup):
    waiting_for_poll = State()

class EveningPoll(StatesGroup):
    q1 = State()
    q2 = State()
    q3 = State()
    q4 = State()
    q5 = State()
    q6 = State()
    q7 = State()
    q8 = State()

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

# ========== КЛАВИАТУРЫ ==========
def gender_kb() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text="👨 Мужской"))
    builder.add(KeyboardButton(text="👩 Женский"))
    builder.add(KeyboardButton(text="⚪ Другое/Не скажу"))
    builder.adjust(2, 1)
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)

def main_menu_kb() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text="📊 Моя статистика"))
    builder.add(KeyboardButton(text="🧘 Дыхательная гимнастика"))
    builder.add(KeyboardButton(text="⏰ Настроить время опроса"))
    builder.add(KeyboardButton(text="🌙 Фаза луны"))
    builder.add(KeyboardButton(text="ℹ️ О боте"))
    builder.adjust(2, 2, 1)
    return builder.as_markup(resize_keyboard=True)

def build_poll_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i in range(1, 6):
        builder.button(text=str(i), callback_data=f"poll_{i}")
    builder.adjust(5)
    return builder.as_markup()

# ========== ОБРАБОТЧИКИ ==========
@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    user = message.from_user
    register_user(user.id, user.username, user.first_name)
    # Проверяем, не указан ли уже пол
    if get_user_gender(user.id) is None:
        await message.answer(
            f"Привет, {user.first_name}! 🌟\n"
            "Для более персонализированной работы укажи, пожалуйста, свой пол.\n"
            "(Это поможет в анализе статистики, данные анонимны)",
            reply_markup=gender_kb()
        )
        await state.set_state(Registration.waiting_for_gender)
    else:
        # Пол уже указан, переходим к настройке времени
        await message.answer(
            "Давай настроим время для вечернего опроса (например, 20:00).",
            reply_markup=main_menu_kb()
        )
        await message.answer(
            "Напиши время для **вечернего опроса** в формате ЧЧ:ММ (например, 20:00).\n"
            "Если хочешь настроить позже, отправь «позже»."
        )
        await state.set_state(TimeSetup.waiting_for_poll)

@dp.message(Registration.waiting_for_gender)
async def process_gender(message: types.Message, state: FSMContext):
    text = message.text.strip()
    gender_map = {
        "👨 Мужской": "male",
        "👩 Женский": "female",
        "⚪ Другое/Не скажу": "other"
    }
    if text not in gender_map:
        await message.answer("Пожалуйста, выбери вариант из кнопок ниже.", reply_markup=gender_kb())
        return
    gender = gender_map[text]
    set_user_gender(message.from_user.id, gender)
    await message.answer(
        "Спасибо! Теперь настроим время для вечернего опроса.",
        reply_markup=main_menu_kb()
    )
    await message.answer(
        "Напиши время для **вечернего опроса** в формате ЧЧ:ММ (например, 20:00).\n"
        "Если хочешь настроить позже, отправь «позже»."
    )
    await state.set_state(TimeSetup.waiting_for_poll)

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "📌 **Команды бота:**\n"
        "/start — регистрация и настройка времени\n"
        "/help — это сообщение\n\n"
        "**Кнопки меню:**\n"
        "📊 Моя статистика — очки, серия, последние результаты\n"
        "🧘 Дыхательная гимнастика — упражнение +5 очков (раз в день)\n"
        "⏰ Настроить время опроса — изменить время вечернего опроса\n"
        "🌙 Фаза луны — картинка и описание текущей фазы\n"
        "ℹ️ О боте — информация о проекте"
    )

@dp.message(Command("admin_stats"))
async def admin_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    total, active, avg_raw, zone_dist, gender_stats = get_admin_stats()
    avg_stress = round((avg_raw - 8) * 9 / 32 + 1, 1) if avg_raw else 0
    text = (
        f"📊 **Статистика бота**\n\n"
        f"👥 Всего пользователей: {total}\n"
        f"📆 Активных за 7 дней: {active}\n"
        f"📉 Средний уровень стресса (7 дней): {avg_stress}/10 (сырой: {avg_raw:.1f})\n\n"
        f"Распределение по зонам за 7 дней:\n"
    )
    zone_emoji = {'green': '🟢', 'yellow': '🟡', 'red': '🔴'}
    for zone, count in zone_dist:
        text += f"{zone_emoji.get(zone, '⚪')} {zone}: {count}\n"

    text += "\n**Статистика по полу:**\n"
    gender_names = {'male': '👨 Мужской', 'female': '👩 Женский', 'other': '⚪ Другое', None: '❓ Не указан'}
    for gender, count in gender_stats:
        text += f"{gender_names.get(gender, gender)}: {count}\n"

    await message.answer(text, parse_mode="Markdown")

@dp.message(TimeSetup.waiting_for_poll)
async def set_poll_time(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text.lower() == "позже":
        await message.answer("Хорошо, можешь настроить позже в меню «⏰ Настроить время опроса».", reply_markup=main_menu_kb())
        await state.clear()
        return
    try:
        datetime.strptime(text, "%H:%M")
    except ValueError:
        await message.answer("Неправильный формат. Попробуй ещё раз (ЧЧ:ММ) или отправь «позже».")
        return
    user_id = message.from_user.id
    set_user_poll_time(user_id, text)
    await message.answer(f"Время вечернего опроса сохранено: {text}. Я пришлю вопросы в это время.", reply_markup=main_menu_kb())
    await state.clear()

@dp.message(F.text == "⏰ Настроить время опроса")
async def time_settings(message: types.Message, state: FSMContext):
    await message.answer(
        "Напиши время для **вечернего опроса** в формате ЧЧ:ММ (например, 20:00).\n"
        "Если не хочешь менять, отправь «нет»."
    )
    await state.set_state(TimeSetup.waiting_for_poll)

@dp.message(F.text == "📊 Моя статистика")
async def show_stats(message: types.Message):
    user_id = message.from_user.id
    points, streak, recent = get_user_stats(user_id)
    text = f"📈 **Твоя статистика**\n\n⭐ Очки: {points}\n🔥 Серия дней: {streak}\n\n"
    if recent:
        text += "Последние результаты (от новых к старым):\n"
        for raw, zone in recent:
            emoji = {'green': '🟢', 'yellow': '🟡', 'red': '🔴'}.get(zone, '⚪')
            text += f"{emoji} {raw} баллов\n"
    else:
        text += "Пока нет оценок стресса. Дождись вечернего опроса!"
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "🧘 Дыхательная гимнастика")
async def breathing_exercise(message: types.Message):
    user_id = message.from_user.id
    if can_take_breathing(user_id):
        exercise = (
            "🌀 **Квадратное дыхание**:\n"
            "Вдох (4 сек) → Задержка (4 сек) → Выдох (4 сек) → Задержка (4 сек).\n"
            "Повтори 5 раз."
        )
        await message.answer(exercise, parse_mode="Markdown")
        add_points(user_id, 5)
        mark_breathing_done(user_id)
        await message.answer("+5 очков за заботу о себе! 💚")
    else:
        await message.answer("Ты уже выполнял(а) дыхательную гимнастику сегодня. Возвращайся завтра!")

@dp.message(F.text == "🌙 Фаза луны")
async def show_moon(message: types.Message):
    today = datetime.now().date().isoformat()
    phase = get_moon_phase(today)

    if phase < 0.05:
        desc = "🌑 Новолуние"
        img_file = "new_moon.jpg"
    elif phase < 0.2:
        desc = "🌒 Молодая луна"
        img_file = "waxing_crescent.jpg"
    elif phase < 0.3:
        desc = "🌓 Первая четверть"
        img_file = "first_quarter.jpg"
    elif phase < 0.45:
        desc = "🌔 Растущая луна"
        img_file = "waxing_gibbous.jpg"
    elif phase < 0.55:
        desc = "🌕 Полнолуние"
        img_file = "full_moon.jpg"
    elif phase < 0.7:
        desc = "🌖 Убывающая луна"
        img_file = "waning_gibbous.jpg"
    elif phase < 0.8:
        desc = "🌗 Последняя четверть"
        img_file = "last_quarter.jpg"
    else:
        desc = "🌘 Старая луна"
        img_file = "waning_crescent.jpg"

    img_path = os.path.join(MOON_PHOTOS_FOLDER, img_file)
    if os.path.exists(img_path):
        photo = FSInputFile(img_path)
        await message.answer_photo(
            photo=photo,
            caption=f"Сегодня {desc}\n(фаза: {phase:.2f})"
        )
    else:
        await message.answer(f"Сегодня {desc}\n(фаза: {phase:.2f})")

    if random.random() < 0.3:
        disclaimer = random.choice(moon_disclaimers)
        await message.answer(disclaimer)

@dp.message(F.text == "ℹ️ О боте")
async def about(message: types.Message):
    await message.answer(
        "Этот бот помогает отслеживать уровень стресса и заботиться о себе.\n"
        "Каждый вечер в настроенное время я присылаю опрос из 8 вопросов.\n"
        "За прохождение опроса ты получаешь очки и серии дней.\n"
        "Дыхательная гимнастика приносит дополнительные очки.\n\n"
        "🌙 *О луне:* информация о фазах луны добавляется для интереса, "
        "но не имеет доказанного влияния на стресс.\n\n"
        "📌 **О проекте:**\n"
        "Данный бот разработан в рамках республиканской научно-практической "
        "конференции школьников «Первые шаги в науку» (2026 год).\n"
        "Автор: [Ваше ФИО], 11 класс, [школа]"
    )

# ========== ВЕЧЕРНИЙ ОПРОС ПО РАСПИСАНИЮ ==========
async def send_poll_to_user(user_id: int):
    if has_today_mood(user_id):
        return
    current_state = await dp.fsm.storage.get_state(chat=user_id)
    if current_state and current_state.startswith('EveningPoll'):
        return
    await dp.fsm.storage.set_state(chat=user_id, state=EveningPoll.q1)
    await dp.fsm.storage.set_data(chat=user_id, data={'answers': [], 'first_poll': not has_previous_moods(user_id)})
    q = PollQuestions.questions[0]
    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"🌆 Вечерний опрос (1/{POLL_QUESTIONS_COUNT}):\n{q['text']}\n_{q['hint']}_",
            parse_mode="Markdown",
            reply_markup=build_poll_kb()
        )
    except Exception as e:
        logging.error(f"Не удалось отправить опрос пользователю {user_id}: {e}")

async def scheduled_polls():
    now = datetime.now().strftime("%H:%M")
    users = get_users_by_poll_time(now)
    for uid in users:
        await send_poll_to_user(uid)

# ========== ОБРАБОТКА ОТВЕТОВ НА ОПРОС ==========
@dp.callback_query(lambda c: c.data and c.data.startswith('poll_'))
async def handle_poll_answer(callback: CallbackQuery, state: FSMContext):
    score = int(callback.data.split('_')[1])
    current_state = await state.get_state()
    if not current_state or not current_state.startswith('EveningPoll'):
        await callback.answer("Сейчас нет активного опроса.", show_alert=True)
        return
    state_num = int(current_state.split(':')[1].replace('q', ''))

    data = await state.get_data()
    answers = data.get('answers', [])
    answers.append(score)
    await state.update_data(answers=answers)

    if state_num == POLL_QUESTIONS_COUNT:
        user_id = callback.from_user.id
        zone, raw_sum = save_mood_with_details(user_id, answers)

        if zone == 'green':
            feedback = "Красавчик/Красотка! Ты в балансе. Поддерживай этот вайб ✨"
        elif zone == 'yellow':
            feedback = ("Похоже, день выдался напряженным. Давай выдохнем? "
                        "Сделай 5 глубоких вдохов или просто отложи телефон на 15 минут 🧘‍♂️")
        else:
            feedback = ("Ого, уровень стресса зашкаливает. Пожалуйста, береги себя. "
                        "Попробуй технику заземления (назови 5 предметов, которые видишь прямо сейчас) или напиши другу 🫂")

        await callback.message.edit_text(
            f"✅ Опрос завершён!\n"
            f"Сумма баллов: {raw_sum}\n"
            f"Зона: {'🟢 Зелёная' if zone=='green' else '🟡 Жёлтая' if zone=='yellow' else '🔴 Красная'}\n\n"
            f"{feedback}"
        )

        if data.get('first_poll', False):
            await callback.message.answer(
                "Спасибо, что прошёл первый опрос! Теперь я буду присылать его ежедневно в выбранное время."
            )
            set_first_poll_done(user_id)

        await callback.message.answer(
            "Выбери действие:",
            reply_markup=main_menu_kb()
        )
        await state.clear()
    else:
        next_num = state_num + 1
        next_state = getattr(EveningPoll, f'q{next_num}')
        await state.set_state(next_state)
        q = PollQuestions.questions[next_num-1]
        await callback.message.edit_text(
            f"Вопрос {next_num}/{POLL_QUESTIONS_COUNT}:\n{q['text']}\n_{q['hint']}_",
            parse_mode="Markdown",
            reply_markup=build_poll_kb()
        )
    await callback.answer()

# ========== ДНЕВНАЯ РАССЫЛКА ФАКТОВ ==========
async def send_day_fact(user_id: int):
    if facts_day:
        fact = random.choice(facts_day)
        try:
            await bot.send_message(chat_id=user_id, text=f"📌 Интересный факт:\n{fact}")
        except Exception as e:
            logging.error(f"Не удалось отправить факт {user_id}: {e}")

async def scheduled_day_facts():
    now = datetime.now()
    if 12 <= now.hour < 14:
        for uid in get_all_users():
            await send_day_fact(uid)

# ========== ПЛАНИРОВЩИК ==========
def setup_scheduler():
    scheduler.add_job(scheduled_polls, trigger="interval", minutes=1)
    scheduler.add_job(scheduled_day_facts, trigger="interval", minutes=30)
    scheduler.start()

# ========== ЗАПУСК ==========
async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    setup_scheduler()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
