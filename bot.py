import os
import asyncio
import logging
import random
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List, Tuple

import ephem
import pytz
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
POLL_QUESTIONS_COUNT = 8
QUICK_TEST_QUESTIONS = 4
QUICK_TEST_COOLDOWN = 3600        # 1 час в секундах
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# ========== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ==========
TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise ValueError("❌ Переменная окружения BOT_TOKEN не установлена!")

# ========== ПРОВЕРКА НАЛИЧИЯ КАРТИНОК ==========
if not os.path.exists(IMAGES_FOLDER):
    os.makedirs(IMAGES_FOLDER)
    logging.warning(f"📁 Папка {IMAGES_FOLDER} создана. Положите в неё изображения.")
image_files = []
for f in os.listdir(IMAGES_FOLDER):
    if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
        image_files.append(os.path.join(IMAGES_FOLDER, f))
if not image_files:
    logging.warning("🖼 В папке images нет изображений. Утренняя рассылка будет пустой.")

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            gender TEXT,
            registered DATE,
            points INTEGER DEFAULT 0,
            current_streak INTEGER DEFAULT 0,
            last_mood_date DATE,
            poll_time TEXT,
            morning_time TEXT,
            last_breathing DATE,
            first_poll_done INTEGER DEFAULT 0,
            consecutive_red_days INTEGER DEFAULT 0,
            last_quick_test TIMESTAMP
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
            is_extra INTEGER DEFAULT 0,
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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_tasks (
            user_id INTEGER,
            task_date DATE,
            poll_done INTEGER DEFAULT 0,
            breathing_done INTEGER DEFAULT 0,
            quick_test_done INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, task_date)
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
        (user_id, username, first_name, datetime.now(MOSCOW_TZ).date().isoformat())
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

def get_user_points(user_id: int) -> int:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT points FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0

def add_points(user_id: int, points: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (points, user_id))
    conn.commit()
    conn.close()

def init_daily_tasks(user_id: int):
    today = datetime.now(MOSCOW_TZ).date().isoformat()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO daily_tasks (user_id, task_date) VALUES (?, ?)",
        (user_id, today)
    )
    conn.commit()
    conn.close()

def update_task(user_id: int, task: str):
    today = datetime.now(MOSCOW_TZ).date().isoformat()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(f"UPDATE daily_tasks SET {task} = 1 WHERE user_id = ? AND task_date = ?", (user_id, today))
    if cur.rowcount == 0:
        cur.execute("INSERT INTO daily_tasks (user_id, task_date) VALUES (?, ?)", (user_id, today))
        cur.execute(f"UPDATE daily_tasks SET {task} = 1 WHERE user_id = ? AND task_date = ?", (user_id, today))
    conn.commit()
    conn.close()

def get_tasks_status(user_id: int) -> dict:
    today = datetime.now(MOSCOW_TZ).date().isoformat()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        "SELECT poll_done, breathing_done, quick_test_done FROM daily_tasks WHERE user_id = ? AND task_date = ?",
        (user_id, today)
    )
    row = cur.fetchone()
    conn.close()
    if row:
        return {'poll': row[0], 'breathing': row[1], 'quick': row[2]}
    return {'poll': 0, 'breathing': 0, 'quick': 0}

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

def save_mood_with_details(user_id: int, answers: List[int], is_extra: bool = False):
    moscow_now = datetime.now(MOSCOW_TZ)
    today = moscow_now.date().isoformat()
    now_time = moscow_now.time().strftime("%H:%M")
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
        INSERT INTO moods (user_id, raw_sum, zone, date, time, is_extra)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, raw_sum, zone, today, now_time, 1 if is_extra else 0))
    mood_id = cur.lastrowid

    full_answers = answers + [0] * (8 - len(answers)) if len(answers) < 8 else answers[:8]
    cur.execute("""
        INSERT INTO mood_details (mood_id, q1, q2, q3, q4, q5, q6, q7, q8)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (mood_id, *full_answers))

    if not is_extra:
        cur.execute("SELECT last_mood_date, current_streak, consecutive_red_days FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        last_date = row[0]
        streak = row[1] if row[1] else 0
        consecutive_red = row[2] if row[2] else 0

        yesterday = (datetime.now(MOSCOW_TZ).date() - timedelta(days=1)).isoformat()
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

        update_task(user_id, 'poll_done')
    else:
        cur.execute("UPDATE users SET points = points + 10 WHERE user_id = ?", (user_id,))
        update_task(user_id, 'quick_test_done')

    conn.commit()
    conn.close()

    if not is_extra and (answers[4] == 5 or answers[7] == 5):
        asyncio.create_task(notify_admin_trigger(user_id, answers))

    if not is_extra and new_consecutive_red >= 3:
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

def get_user_stats(user_id: int) -> Tuple[int, int, List[Tuple[int, str]], List[Tuple[int, str]]]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT points, current_streak FROM users WHERE user_id = ?", (user_id,))
    user_row = cur.fetchone()
    points = user_row[0] if user_row else 0
    streak = user_row[1] if user_row else 0

    cur.execute("SELECT raw_sum, zone FROM moods WHERE user_id = ? AND is_extra = 0 ORDER BY date DESC LIMIT 7", (user_id,))
    recent_main = [(row[0], row[1]) for row in cur.fetchall()]

    cur.execute("SELECT raw_sum, zone FROM moods WHERE user_id = ? AND is_extra = 1 ORDER BY date DESC LIMIT 7", (user_id,))
    recent_extra = [(row[0], row[1]) for row in cur.fetchall()]

    conn.close()
    return points, streak, recent_main, recent_extra

def set_user_poll_time(user_id: int, poll_time: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE users SET poll_time = ? WHERE user_id = ?", (poll_time, user_id))
    conn.commit()
    conn.close()

def set_user_morning_time(user_id: int, morning_time: Optional[str]):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE users SET morning_time = ? WHERE user_id = ?", (morning_time, user_id))
    conn.commit()
    conn.close()

def get_users_by_poll_time(current_time: str) -> List[int]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE poll_time = ?", (current_time,))
    users = [row[0] for row in cur.fetchall()]
    conn.close()
    return users

def get_users_by_morning_time(current_time: str) -> List[int]:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE morning_time = ?", (current_time,))
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

    week_ago = (datetime.now(MOSCOW_TZ).date() - timedelta(days=7)).isoformat()
    cur.execute("SELECT COUNT(DISTINCT user_id) FROM moods WHERE date >= ?", (week_ago,))
    active_users = cur.fetchone()[0]

    cur.execute("SELECT AVG(raw_sum) FROM moods WHERE date >= ? AND is_extra = 0", (week_ago,))
    avg_raw_main = cur.fetchone()[0] or 0

    cur.execute("SELECT AVG(raw_sum) FROM moods WHERE date >= ? AND is_extra = 1", (week_ago,))
    avg_raw_extra = cur.fetchone()[0] or 0

    cur.execute("SELECT zone, COUNT(*) FROM moods WHERE date >= ? AND is_extra = 0 GROUP BY zone", (week_ago,))
    zone_dist_main = cur.fetchall()

    cur.execute("SELECT zone, COUNT(*) FROM moods WHERE date >= ? AND is_extra = 1 GROUP BY zone", (week_ago,))
    zone_dist_extra = cur.fetchall()

    cur.execute("SELECT gender, COUNT(*) FROM users GROUP BY gender")
    gender_stats = cur.fetchall()

    conn.close()
    return {
        'total_users': total_users,
        'active_users': active_users,
        'avg_main': avg_raw_main,
        'avg_extra': avg_raw_extra,
        'zone_main': zone_dist_main,
        'zone_extra': zone_dist_extra,
        'gender_stats': gender_stats
    }

def has_today_mood(user_id: int) -> bool:
    today = datetime.now(MOSCOW_TZ).date().isoformat()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id FROM moods WHERE user_id = ? AND date = ? AND is_extra = 0", (user_id, today))
    exists = cur.fetchone() is not None
    conn.close()
    return exists

def can_take_breathing(user_id: int) -> bool:
    today = datetime.now(MOSCOW_TZ).date().isoformat()
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
    today = datetime.now(MOSCOW_TZ).date().isoformat()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_breathing = ? WHERE user_id = ?", (today, user_id))
    cur.execute("UPDATE users SET points = points + 5 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    update_task(user_id, 'breathing_done')

def can_take_quick_test(user_id: int) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT last_quick_test FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if row and row[0]:
        last_time = datetime.fromisoformat(row[0])
        if datetime.now(MOSCOW_TZ) - last_time < timedelta(seconds=QUICK_TEST_COOLDOWN):
            return False
    return True

def update_quick_test_time(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_quick_test = ? WHERE user_id = ?", (datetime.now(MOSCOW_TZ).isoformat(), user_id))
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

# ========== ДЫХАТЕЛЬНЫЕ УПРАЖНЕНИЯ ==========
breathing_exercises = [
    {
        "name": "Квадратное дыхание",
        "description": "Вдох (4 сек) → Задержка (4 сек) → Выдох (4 сек) → Задержка (4 сек). Повтори 5 раз."
    },
    {
        "name": "Дыхание 4-7-8",
        "description": "Вдох (4 сек) → Задержка (7 сек) → Выдох (8 сек). Повтори 4 раза."
    },
    {
        "name": "Диафрагмальное дыхание",
        "description": "Положи руку на живот. Медленно вдохни носом, чувствуя, как живот поднимается. Выдохни через рот, живот опускается. Повтори 10 раз."
    },
    {
        "name": "Расслабляющее дыхание",
        "description": "Представь, что ты вдыхаешь спокойствие, а выдыхаешь напряжение. Делай медленные глубокие вдохи и выдохи в течение 2 минут."
    },
    {
        "name": "Альтернативное дыхание через ноздри",
        "description": "Закрой правую ноздрю большим пальцем, вдохни левой. Закрой левую, выдохни правой. Повтори цикл 5 раз."
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
    waiting_for_morning = State()

class EveningPoll(StatesGroup):
    q1 = State()
    q2 = State()
    q3 = State()
    q4 = State()
    q5 = State()
    q6 = State()
    q7 = State()
    q8 = State()

class QuickTest(StatesGroup):
    q1 = State()
    q2 = State()
    q3 = State()
    q4 = State()

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler(timezone=MOSCOW_TZ)  # задаём часовой пояс для планировщика

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
    builder.add(KeyboardButton(text="⚡ Экспресс-тест"))
    builder.add(KeyboardButton(text="📋 Мои задания"))
    builder.add(KeyboardButton(text="⏰ Настроить время"))
    builder.add(KeyboardButton(text="🌙 Фаза луны"))
    builder.add(KeyboardButton(text="ℹ️ О боте"))
    builder.adjust(2, 2, 2, 1)
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
    if user.id == ADMIN_ID:
        await message.answer("👋 Привет, администратор! Я буду присылать тебе уведомления.")
    if get_user_gender(user.id) is None:
        await message.answer(
            f"Привет, {user.first_name}! 🌟\n"
            "Для более персонализированной работы укажи, пожалуйста, свой пол.\n"
            "(Это поможет в анализе статистики, данные анонимны)",
            reply_markup=gender_kb()
        )
        await state.set_state(Registration.waiting_for_gender)
    else:
        # Пол уже указан – предлагаем пробный опрос
        await message.answer(
            "Хочешь прямо сейчас пройти пробный вечерний опрос?\n"
            "Это займёт пару минут и покажет твой текущий уровень стресса.",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="✅ Да"), KeyboardButton(text="⏰ Позже")]],
                resize_keyboard=True
            )
        )
        await state.set_state(Registration.waiting_for_trial)

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
        "Спасибо! Теперь давай определимся с опросом.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="✅ Да"), KeyboardButton(text="⏰ Позже")]],
            resize_keyboard=True
        )
    )
    await state.set_state(Registration.waiting_for_trial)

@dp.message(Registration.waiting_for_trial)
async def process_trial(message: types.Message, state: FSMContext):
    if message.text == "✅ Да":
        # Запускаем вечерний опрос прямо сейчас
        await state.set_state(EveningPoll.q1)
        await state.update_data(answers=[], first_poll=True)
        q = PollQuestions.questions[0]
        await message.answer(
            f"🌆 Пробный опрос (1/{POLL_QUESTIONS_COUNT}):\n{q['text']}\n_{q['hint']}_",
            parse_mode="Markdown",
            reply_markup=build_poll_kb()
        )
    else:
        # Переходим к настройке времени
        await message.answer(
            "Хорошо, давай настроим время для ежедневного опроса.",
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
        "🧘 Дыхательная гимнастика — случайное упражнение +5 очков (раз в день)\n"
        "⚡ Экспресс-тест — быстрый тест (не чаще раза в час) +10 очков\n"
        "📋 Мои задания — прогресс по ежедневным заданиям\n"
        "⏰ Настроить время — изменить время опроса и утренней рассылки\n"
        "🌙 Фаза луны — картинка и описание текущей фазы\n"
        "ℹ️ О боте — информация о проекте"
    )

@dp.message(Command("admin_stats"))
async def admin_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    stats = get_admin_stats()
    avg_main_stress = round((stats['avg_main'] - 8) * 9 / 32 + 1, 1) if stats['avg_main'] else 0
    avg_extra_stress = round((stats['avg_extra'] - 8) * 9 / 32 + 1, 1) if stats['avg_extra'] else 0
    text = (
        f"📊 **Статистика бота**\n\n"
        f"👥 Всего пользователей: {stats['total_users']}\n"
        f"📆 Активных за 7 дней: {stats['active_users']}\n\n"
        f"📉 Основные опросы (7 дней):\n"
        f"   Средний сырой балл: {stats['avg_main']:.1f}\n"
        f"   Средний уровень стресса: {avg_main_stress}/10\n"
    )
    text += "   Распределение по зонам:\n"
    zone_emoji = {'green': '🟢', 'yellow': '🟡', 'red': '🔴'}
    for zone, count in stats['zone_main']:
        text += f"      {zone_emoji.get(zone, '⚪')} {zone}: {count}\n"

    text += f"\n📊 Экспресс-тесты (7 дней):\n"
    text += f"   Средний сырой балл: {stats['avg_extra']:.1f}\n"
    text += f"   Средний уровень стресса: {avg_extra_stress}/10\n"
    for zone, count in stats['zone_extra']:
        text += f"      {zone_emoji.get(zone, '⚪')} {zone}: {count}\n"

    text += "\n**Статистика по полу:**\n"
    gender_names = {'male': '👨 Мужской', 'female': '👩 Женский', 'other': '⚪ Другое', None: '❓ Не указан'}
    for gender, count in stats['gender_stats']:
        text += f"{gender_names.get(gender, gender)}: {count}\n"

    await message.answer(text, parse_mode="Markdown")

@dp.message(TimeSetup.waiting_for_poll)
async def set_poll_time(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text.lower() == "позже":
        await state.update_data(poll_unchanged=True)
        await message.answer("Хорошо, можешь настроить позже в меню «⏰ Настроить время».")
        await ask_morning_time(message, state)
        return
    try:
        datetime.strptime(text, "%H:%M")
    except ValueError:
        await message.answer("Неправильный формат. Попробуй ещё раз (ЧЧ:ММ) или отправь «позже».")
        return
    user_id = message.from_user.id
    set_user_poll_time(user_id, text)
    await state.update_data(poll_time=text)
    await ask_morning_time(message, state)

async def ask_morning_time(message: types.Message, state: FSMContext):
    await message.answer(
        "Теперь напиши время для **утренней рассылки** картинок (например, 09:00).\n"
        "Если не хочешь получать утром, отправь «нет»."
    )
    await state.set_state(TimeSetup.waiting_for_morning)

@dp.message(TimeSetup.waiting_for_morning)
async def set_morning_time(message: types.Message, state: FSMContext):
    text = message.text.strip()
    morning = None
    if text.lower() != "нет":
        try:
            datetime.strptime(text, "%H:%M")
            morning = text
        except ValueError:
            await message.answer("Неправильный формат. Попробуй ещё раз или отправь «нет».")
            return
    user_id = message.from_user.id
    set_user_morning_time(user_id, morning)
    await message.answer("Настройки сохранены! ✅", reply_markup=main_menu_kb())
    await state.clear()

@dp.message(F.text == "⏰ Настроить время")
async def time_settings(message: types.Message, state: FSMContext):
    await message.answer(
        "Напиши время для **вечернего опроса** в формате ЧЧ:ММ (например, 20:00).\n"
        "Если не хочешь менять, отправь «позже»."
    )
    await state.set_state(TimeSetup.waiting_for_poll)

@dp.message(F.text == "📊 Моя статистика")
async def show_stats(message: types.Message):
    user_id = message.from_user.id
    points, streak, recent_main, recent_extra = get_user_stats(user_id)
    text = f"📈 **Твоя статистика**\n\n⭐ Очки: {points}\n🔥 Серия дней: {streak}\n\n"
    if recent_main:
        text += "**Основные опросы** (последние 7):\n"
        for raw, zone in recent_main:
            emoji = {'green': '🟢', 'yellow': '🟡', 'red': '🔴'}.get(zone, '⚪')
            text += f"{emoji} {raw} баллов\n"
    else:
        text += "Основных опросов пока нет.\n"
    if recent_extra:
        text += "\n**Экспресс-тесты** (последние 7):\n"
        for raw, zone in recent_extra:
            emoji = {'green': '🟢', 'yellow': '🟡', 'red': '🔴'}.get(zone, '⚪')
            text += f"{emoji} {raw} баллов\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "🧘 Дыхательная гимнастика")
async def breathing_exercise(message: types.Message):
    user_id = message.from_user.id
    if can_take_breathing(user_id):
        exercise = random.choice(breathing_exercises)
        await message.answer(
            f"🧘 **{exercise['name']}**\n\n{exercise['description']}",
            parse_mode="Markdown"
        )
        mark_breathing_done(user_id)
        await message.answer("+5 очков за заботу о себе! 💚")
    else:
        await message.answer("Ты уже выполнял(а) дыхательную гимнастику сегодня. Возвращайся завтра!")

@dp.message(F.text == "⚡ Экспресс-тест")
async def quick_test_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if not can_take_quick_test(user_id):
        await message.answer("Ты уже проходил экспресс-тест недавно. Попробуй через час.")
        return
    await state.set_state(QuickTest.q1)
    await state.update_data(answers=[])
    q = PollQuestions.questions[0]
    await message.answer(
        f"⚡ Экспресс-тест (1/{QUICK_TEST_QUESTIONS}):\n{q['text']}\n_{q['hint']}_",
        parse_mode="Markdown",
        reply_markup=build_poll_kb()
    )

@dp.message(F.text == "📋 Мои задания")
async def show_tasks(message: types.Message):
    user_id = message.from_user.id
    init_daily_tasks(user_id)
    tasks = get_tasks_status(user_id)
    points = get_user_points(user_id)
    text = (
        f"📋 **Ежедневные задания**\n\n"
        f"⭐ Твои очки: {points}\n\n"
        f"{'✅' if tasks['poll'] else '⬜'} Пройти основной опрос (+15)\n"
        f"{'✅' if tasks['breathing'] else '⬜'} Выполнить дыхательную гимнастику (+5)\n"
        f"{'✅' if tasks['quick'] else '⬜'} Пройти экспресс-тест (+10)\n\n"
        f"*Задания обновляются каждый день в 00:00*"
    )
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "🌙 Фаза луны")
async def show_moon(message: types.Message):
    today = datetime.now(MOSCOW_TZ).date().isoformat()
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
        "Дыхательная гимнастика и экспресс-тесты приносят дополнительные очки.\n\n"
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
    await dp.fsm.storage.set_data(chat=user_id, data={'answers': [], 'first_poll': False})
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
    now = datetime.now(MOSCOW_TZ).strftime("%H:%M")
    users = get_users_by_poll_time(now)
    for uid in users:
        await send_poll_to_user(uid)

# ========== УТРЕННЯЯ РАССЫЛКА (ТОЛЬКО КАРТИНКИ) ==========
async def send_morning_pic(user_id: int):
    if not image_files:
        return
    try:
        img_path = random.choice(image_files)
        photo = FSInputFile(img_path)
        caption = "Доброе утро! Хорошего дня ☀️"
        await bot.send_photo(chat_id=user_id, photo=photo, caption=caption)
    except Exception as e:
        logging.error(f"Не удалось отправить картинку {user_id}: {e}")

async def scheduled_morning():
    now = datetime.now(MOSCOW_TZ).strftime("%H:%M")
    users = get_users_by_morning_time(now)
    for uid in users:
        await send_morning_pic(uid)

# ========== ОБРАБОТКА ОТВЕТОВ НА ОПРОС ==========
@dp.callback_query(lambda c: c.data and c.data.startswith('poll_'))
async def handle_poll_answer(callback: CallbackQuery, state: FSMContext):
    score = int(callback.data.split('_')[1])
    current_state = await state.get_state()
    if not current_state:
        await callback.answer("Сейчас нет активного опроса.", show_alert=True)
        return

    if current_state.startswith('EveningPoll'):
        questions = PollQuestions.questions
        total = POLL_QUESTIONS_COUNT
        state_num = int(current_state.split(':')[1].replace('q', ''))
    elif current_state.startswith('QuickTest'):
        questions = PollQuestions.questions[:QUICK_TEST_QUESTIONS]
        total = QUICK_TEST_QUESTIONS
        state_num = int(current_state.split(':')[1].replace('q', ''))
    else:
        await callback.answer("Неизвестное состояние.", show_alert=True)
        return

    data = await state.get_data()
    answers = data.get('answers', [])
    answers.append(score)
    await state.update_data(answers=answers)

    if state_num == total:
        user_id = callback.from_user.id
        is_extra = current_state.startswith('QuickTest')
        zone, raw_sum = save_mood_with_details(user_id, answers, is_extra)

        if zone == 'green':
            feedback = "Красавчик/Красотка! Ты в балансе. Поддерживай этот вайб ✨"
        elif zone == 'yellow':
            feedback = ("Похоже, день выдался напряженным. Давай выдохнем? "
                        "Сделай 5 глубоких вдохов или просто отложи телефон на 15 минут 🧘‍♂️")
        else:
            feedback = ("Ого, уровень стресса зашкаливает. Пожалуйста, береги себя. "
                        "Попробуй технику заземления (назови 5 предметов, которые видишь прямо сейчас) или напиши другу 🫂")

        await callback.message.edit_text(
            f"✅ {'Экспресс-тест' if is_extra else 'Опрос'} завершён!\n"
            f"Сумма баллов: {raw_sum}\n"
            f"Зона: {'🟢 Зелёная' if zone=='green' else '🟡 Жёлтая' if zone=='yellow' else '🔴 Красная'}\n\n"
            f"{feedback}"
        )

        if is_extra:
            update_quick_test_time(user_id)
            await callback.message.answer("+10 очков за экспресс-тест! ⚡")
        elif data.get('first_poll', False):
            # Это был пробный опрос, переходим к настройке времени
            await callback.message.answer(
                "Спасибо за пробный опрос! Теперь давай настроим время для ежедневных опросов.",
                reply_markup=main_menu_kb()
            )
            await callback.message.answer(
                "Напиши время для **вечернего опроса** в формате ЧЧ:ММ (например, 20:00).\n"
                "Если хочешь настроить позже, отправь «позже»."
            )
            await state.set_state(TimeSetup.waiting_for_poll)
            return

        await callback.message.answer(
            "Выбери действие:",
            reply_markup=main_menu_kb()
        )
        await state.clear()
    else:
        next_num = state_num + 1
        next_state = getattr(state.__class__, f'q{next_num}')
        await state.set_state(next_state)
        q = questions[next_num-1]
        await callback.message.edit_text(
            f"Вопрос {next_num}/{total}:\n{q['text']}\n_{q['hint']}_",
            parse_mode="Markdown",
            reply_markup=build_poll_kb()
        )
    await callback.answer()

# ========== ПЛАНИРОВЩИК ==========
def setup_scheduler():
    scheduler.add_job(scheduled_polls, trigger="interval", minutes=1)
    scheduler.add_job(scheduled_morning, trigger="interval", minutes=1)
    scheduler.start()

# ========== ЗАПУСК ==========
async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    setup_scheduler()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
