# FULL VERSION — Telegram Bot "Психолог"
# Полная реализация с секретной линией, событиями, обучением и полной логикой

import os
import pickle
import random
from pathlib import Path

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

TOKEN = os.getenv("BOT_TOKEN", "YOUR_TOKEN_HERE")
DATA_PATH = Path("patients_pickles")
SECRET_ID = 'NULL'

# ---------------- LOAD ----------------
def load_patients():
    patients = {}
    for file in DATA_PATH.glob("*.pickle"):
        with open(file, "rb") as f:
            p = pickle.load(f)
            patients[p['id']] = p
    return patients

PATIENTS = load_patients()
SECRET = PATIENTS[SECRET_ID]

# ---------------- PLAYER ----------------
def new_player():
    return {
        "energy": 5,
        "reputation": 3,
        "stress": 0,
        "day": 1,
        "progress": {},
        "flags": {},
        "mode": "day_start",
        "current": None,
        "visit": 0,
        "queue": [],
        "secret_phase": 0,
        "secret_disabled": False,
        "secret_today": False
    }

SESSIONS = {}

def get_player(uid):
    if uid not in SESSIONS:
        SESSIONS[uid] = new_player()
    return SESSIONS[uid]

# ---------------- CORE ----------------
def choose_patients(player):
    pool = [p for p in PATIENTS.values() if p['id'] != SECRET_ID and player['progress'].get(p['id'], 0) < 3]
    random.shuffle(pool)
    selected = pool[:2]
    return [(p, player['progress'].get(p['id'], 0) + 1) for p in selected]


def apply_consequences(player, cons):
    player['reputation'] += cons.get('reputation_change', 0)
    player['stress'] += cons.get('stress_change', 0)

    if cons.get('trust_flag'):
        player['flags'][cons['trust_flag']] = True

    if cons.get('patient_left'):
        player['progress'][player['current']['id']] = 3

    if cons.get('full_reset'):
        return "RESET"

    return None


def check_breakdown(player):
    if player['stress'] >= 100:
        player['reputation'] -= 1
        return True
    return False

# ---------------- SECRET EVENTS ----------------
async def secret_event(update, player, context):
    if player['secret_disabled']:
        return

    d = player['day']

    if d == 2:
        await update.message.reply_text("...Кстати. Вы ведь тоже иногда сомневаетесь?")

    elif d == 3:
        await update.message.reply_text("⚠️ Ошибка загрузки данных пациента. Повторите попытку.")

    elif d == 4:
        await update.message.reply_text("Пациент: ???")

    elif d == 5:
        await run_secret_dialog(update, player, context, phase=2)

    elif d == 6:
        await update.message.reply_text("Или вы тоже так делаете?")

    elif d == 7:
        await update.message.reply_text("❗ Данные отсутствуют")

    elif d == 8:
        await run_secret_dialog(update, player, context, phase=6)

# ---------------- SECRET DIALOG ----------------
async def run_secret_dialog(update, player, context, phase):
    player['mode'] = 'secret'

    visit = next(v for v in SECRET['visits'] if v['phase'] == phase)

    try:
        with open(DATA_PATH / "SECRET.jpg", 'rb') as img:
            await update.message.reply_photo(img)
    except:
        pass

    await update.message.reply_text(visit['dialog'])

    context.user_data['secret_options'] = visit['options']

    keyboard = [[o['text']] for o in visit['options']]
    await update.message.reply_text("...", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

# ---------------- PATIENT ----------------
async def next_patient(update, player, context):
    if player['energy'] <= 0 or not player['queue']:
        await update.message.reply_text("Приёмы завершены", reply_markup=ReplyKeyboardMarkup([["Завершить день"]], resize_keyboard=True))
        return

    patient, visit = player['queue'].pop(0)
    player['current'] = patient
    player['visit'] = visit
    player['energy'] -= 1
    player['mode'] = 'dialog'

    data = patient['visits'][visit - 1]

    try:
        with open(DATA_PATH / patient['image_file'], 'rb') as img:
            await update.message.reply_photo(img)
    except:
        pass

    await update.message.reply_text(f"{patient['name']} {patient['surname']}\nВизит {visit}/3\n\n{data['dialog']}")

    if visit < 3:
        context.user_data['options'] = data['options']
        keyboard = [[o['text']] for o in data['options']]
        await update.message.reply_text("Выберите:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    else:
        context.user_data['diagnosis'] = data['diagnosis_options']
        keyboard = [[d['diagnosis']] for d in data['diagnosis_options']]
        await update.message.reply_text("Диагноз:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

# ---------------- END DAY ----------------
async def end_day(update, player):
    player['stress'] = max(0, player['stress'] - 20)
    player['day'] += 1
    player['energy'] = 5

    await update.message.reply_text("День завершён")
    await start_day(update, player)

# ---------------- START DAY ----------------
async def start_day(update, player):
    player['secret_today'] = False
    player['queue'] = choose_patients(player)

    plist = ", ".join([f"{p['name']} {p['surname']} ({v})" for p, v in player['queue']]) or "нет"

    await update.message.reply_text(
        f"День {player['day']}\nЭнергия {player['energy']}/5\nРепутация {player['reputation']}\nСтресс {player['stress']}\n\nПациенты: {plist}",
        reply_markup=ReplyKeyboardMarkup([["Начать приём"], ["Моя статистика"]], resize_keyboard=True)
    )

# ---------------- MAIN HANDLER ----------------
async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    player = get_player(update.effective_user.id)
    text = update.message.text

    if text == "Начать приём":
        await next_patient(update, player, context)
        return

    if text == "Принять следующего":
        if not player['secret_today']:
            player['secret_today'] = True
            await secret_event(update, player, context)
        await next_patient(update, player, context)
        return

    if text == "Завершить день":
        await end_day(update, player)
        return

    if text == "Моя статистика":
        await update.message.reply_text(str(player))
        return

    # обычные ответы
    if player['mode'] == 'dialog':
        for opt in context.user_data.get('options', []):
            if opt['text'] == text:
                await update.message.reply_text(opt['reaction'])
                res = apply_consequences(player, opt['consequences'])

                player['progress'][player['current']['id']] = player['visit']

                if check_breakdown(player):
                    await update.message.reply_text("Срыв. День окончен")
                    return

                await update.message.reply_text("Далее", reply_markup=ReplyKeyboardMarkup([["Принять следующего"]], resize_keyboard=True))
                return

        for d in context.user_data.get('diagnosis', []):
            if d['diagnosis'] == text:
                await update.message.reply_text(d['reaction'])
                apply_consequences(player, d['consequences'])

                player['progress'][player['current']['id']] = 3

                await update.message.reply_text("Кейс завершён", reply_markup=ReplyKeyboardMarkup([["Принять следующего"]], resize_keyboard=True))
                return

    # секретные ответы
    if player['mode'] == 'secret':
        for opt in context.user_data.get('secret_options', []):
            if opt['text'] == text:
                await update.message.reply_text(opt['reaction'])
                cons = opt['consequences']

                if cons.get('secret_ending') == 'reset_game':
                    SESSIONS.clear()
                    await update.message.reply_text("Система перезагружена")
                    return

                player['mode'] = 'dialog'
                await update.message.reply_text("...", reply_markup=ReplyKeyboardMarkup([["Принять следующего"]], resize_keyboard=True))
                return

# ---------------- MAIN ----------------
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_day))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

    print("FULL GAME STARTED")
    app.run_polling()

if __name__ == '__main__':
    main()
