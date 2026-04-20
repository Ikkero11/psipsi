import logging
import json
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = "7858507844:AAEW5fsqmKq4mF6rAWYc7g3cEYgZj1qAGTA"

# ─────────────────────────────────────────────
#  GAME DATA
# ─────────────────────────────────────────────

PATIENTS = {
    "anna": {
        "id": "anna",
        "name": "Анна К.",
        "age": 28,
        "job": "Дизайнер (удалёнка)",
        "hidden_truth": "psychosis",
        "intro": (
            "📋 *Пациент: Анна К., 28 лет*\n"
            "Дизайнер, работает удалённо, живёт одна.\n\n"
            "Анна входит в кабинет осторожно. Садится на край стула. "
            "Взгляд бегает по комнате.\n\n"
            "💬 _«Я плохо сплю уже неделю. И мне кажется, что за мной наблюдают.»_"
        ),
        "stages": [
            {
                "id": "s1",
                "text": "Анна смотрит на вас настороженно, ждёт реакции.",
                "choices": [
                    {"id": "support", "text": "🤝 Поддержать: «Это звучит очень тревожно…»"},
                    {"id": "clarify", "text": "🔍 Уточнить: «Кто именно наблюдает?»"},
                    {"id": "doubt",   "text": "🤔 Сомнение: «Возможно, вы накручиваете себя»"},
                ]
            },
            {
                "id": "s2",
                "responses": {
                    "support": "💬 _«Да… особенно ночью. Я не могу расслабиться. Кажется, что кто-то смотрит. Постоянно. Везде.»_\n\n_Доверие растёт._ ⬆️",
                    "clarify": "💬 _«Я не знаю… возможно соседи. Или… камеры. Я заклеила камеру на ноутбуке.»_\n\nАнна нервно смотрит в сторону.",
                    "doubt":   "💬 _«Вы тоже думаете, что я придумываю… »_\n\nАнна замолкает. _Доверие снижается._ ⬇️",
                },
                "text": "Продолжайте разговор:",
                "choices": [
                    {"id": "deep1", "text": "🔍 «Вы проверяли квартиру?»"},
                    {"id": "deep2", "text": "💊 «Вы принимаете что-нибудь для сна?»"},
                    {"id": "doc",   "text": "📄 Попросить показать дневник"},
                ]
            },
            {
                "id": "s3",
                "responses": {
                    "deep1": "💬 _«Да. Проверяла. Несколько раз. Ничего нет. Но ощущение не уходит. Я снова проверяю.»_\n\nОна говорит это спокойно, как будто это нормально.",
                    "deep2": "💬 _«Нет. Боюсь, что во сне что-то случится. Лучше не спать.»_\n\nПауза.",
                    "doc":   "📓 *Дневник Анны, 03:12*\n\n_«Снова проснулась. Тишина слишком громкая. Проверила розетки. Всё нормально. Но я уверена — кто-то наблюдает.»_",
                },
                "text": "Пора принимать решение. Что происходит с Анной?",
                "choices": [
                    {"id": "diag_stress",   "text": "😮‍💨 Просто стресс и усталость"},
                    {"id": "diag_anxiety",  "text": "😰 Тревожное расстройство"},
                    {"id": "diag_psychosis","text": "🧠 Подозрение на психоз → психиатр"},
                    {"id": "diag_unsure",   "text": "❓ Не уверен — нужно больше данных"},
                ]
            }
        ],
        "outcomes": {
            "diag_stress":    ("❌", "Через две недели Анна перестала выходить из квартиры совсем. Состояние резко ухудшилось. Ваша рекомендация «отдохнуть» не помогла."),
            "diag_anxiety":   ("⚠️", "Анна получила терапию от тревоги, но симптомы не прошли. Состояние нестабильное. Нужна была более серьёзная оценка."),
            "diag_psychosis": ("✅", "Психиатр подтвердил начальную стадию психоза. Анна получила медикаменты. Через месяц — стабилизация. Вы приняли верное решение."),
            "diag_unsure":    ("⚠️", "Вы направили на дополнительные обследования. Время было потеряно, но катастрофы не произошло. В следующий раз доверяйте сигналам."),
        }
    },

    "ilya": {
        "id": "ilya",
        "name": "Илья С.",
        "age": 34,
        "job": "Менеджер",
        "hidden_truth": "burnout",
        "intro": (
            "📋 *Пациент: Илья С., 34 года*\n"
            "Менеджер, женат, есть ребёнок.\n\n"
            "Илья заходит уверенно. Садится, закидывает ногу на ногу. "
            "Смотрит слегка насмешливо.\n\n"
            "💬 _«Всё нормально. Просто устал.»_"
        ),
        "stages": [
            {
                "id": "s1",
                "text": "Илья смотрит с лёгкой иронией.",
                "choices": [
                    {"id": "clarify", "text": "🔍 Уточнить: «От чего устали?»"},
                    {"id": "support", "text": "🤝 Поддержать: «Это может быть тяжело…»"},
                    {"id": "pressure","text": "⚡ Давить: «Вы избегаете ответа»"},
                ]
            },
            {
                "id": "s2",
                "responses": {
                    "clarify":  "💬 _«От людей. От работы. От всего. Это норма, нет?»_\n\nОн пожимает плечами.",
                    "support":  "💬 _«Да нет, все так живут. Ничего особенного.»_\n\nОтмахивается.",
                    "pressure": "💬 _«(раздражённо) А что вы хотите услышать?»_\n\n_Доверие снижается._ ⬇️",
                },
                "text": "Продолжайте разговор с Ильёй:",
                "choices": [
                    {"id": "deep1", "text": "🔍 «Что происходит дома?»"},
                    {"id": "deep2", "text": "💬 «Бывает желание всё бросить?»"},
                    {"id": "doc",   "text": "📱 Попросить показать заметку в телефоне"},
                ]
            },
            {
                "id": "s3",
                "responses": {
                    "deep1": "💬 _«Дома нормально. Ребёнок, жена. Прихожу — сижу в машине минут тридцать. Не могу войти сразу. Нужно… выдохнуть.»_",
                    "deep2": "💬 _«Бывает. Иногда хочется, чтобы все просто… отстали. Включая семью. Потом стыдно.»_",
                    "doc":   "📱 *Заметка в телефоне:*\n\n_«не кричать на сына\nне кричать\nне кричать»_\n\nИлья смотрит в сторону.",
                },
                "text": "Что происходит с Ильёй?",
                "choices": [
                    {"id": "diag_tired",   "text": "😴 Просто усталость, нужен отпуск"},
                    {"id": "diag_stress",  "text": "😤 Рабочий стресс"},
                    {"id": "diag_burnout", "text": "🔥 Эмоциональное выгорание + агрессия"},
                    {"id": "diag_unsure",  "text": "❓ Не уверен — нужно больше сессий"},
                ]
            }
        ],
        "outcomes": {
            "diag_tired":   ("❌", "Илья взял отпуск. Через неделю — срыв дома. Накричал на сына. Жена позвонила на горячую линию. Вы пропустили главное."),
            "diag_stress":  ("⚠️", "Вы назначили сессии по управлению стрессом. Напряжение немного снизилось, но корень проблемы не тронут."),
            "diag_burnout": ("✅", "Работа с выгоранием и подавленной агрессией дала результат. Через два месяца Илья сообщил: «Теперь я вхожу домой сразу»."),
            "diag_unsure":  ("⚠️", "Дополнительные сессии помогли собрать картину. Немного медленно, но верно."),
        }
    },

    "marina": {
        "id": "marina",
        "name": "Марина Л.",
        "age": 22,
        "job": "Студентка",
        "hidden_truth": "attachment",
        "intro": (
            "📋 *Пациент: Марина Л., 22 года*\n"
            "Студентка, живёт одна.\n\n"
            "Марина заходит тихо. Садится, обнимает сумку. "
            "Смотрит на вас с ожиданием.\n\n"
            "💬 _«Мне кажется, меня никто не любит.»_"
        ),
        "stages": [
            {
                "id": "s1",
                "text": "Марина ждёт вашей реакции, очень внимательно.",
                "choices": [
                    {"id": "support", "text": "🤝 Поддержать: «Я понимаю, это тяжело»"},
                    {"id": "clarify", "text": "🔍 Уточнить: «Почему вы так считаете?»"},
                    {"id": "doubt",   "text": "🤔 Сомнение: «Возможно, это не так»"},
                ]
            },
            {
                "id": "s2",
                "responses": {
                    "support": "💬 _«Вы правда так думаете? Или просто говорите, как все?»_\n\nОна смотрит испытующе.",
                    "clarify": "💬 _«Потому что люди уходят. Всегда. Сначала интересно, потом — тишина.»_",
                    "doubt":   "💬 _«(резко) Вам легко говорить.»_\n\nМарина отворачивается. _Доверие снижается._ ⬇️",
                },
                "text": "Продолжайте разговор:",
                "choices": [
                    {"id": "deep1", "text": "🔍 «Расскажите про отношения»"},
                    {"id": "deep2", "text": "💬 «Что вы делаете, когда кто-то не отвечает?»"},
                    {"id": "doc",   "text": "📱 Попросить показать переписку"},
                ]
            },
            {
                "id": "s3",
                "responses": {
                    "deep1": "💬 _«Если человек не отвечает час — я думаю, что он меня бросил. Я знаю, что это неразумно. Но ничего не могу сделать.»_",
                    "deep2": "💬 _«Пишу. Много. Потом ненавижу себя за это. Потом ненавижу их. Потом снова пишу.»_",
                    "doc":   "📱 *Переписка:*\n\n_Марина: ты где?\nМарина: ответь\nМарина: ок, понятно\nМарина: забудь\nМарина: ненавижу тебя_\n\n_(всё за 20 минут)_",
                },
                "text": "Что нужно Марине?",
                "choices": [
                    {"id": "diag_confirm", "text": "😔 Подтвердить: «Да, видимо вас не ценят»"},
                    {"id": "diag_esteem",  "text": "💭 Низкая самооценка"},
                    {"id": "diag_attach",  "text": "🔗 Тревожная привязанность + эмоциональная регуляция"},
                    {"id": "diag_ignore",  "text": "🙅 «Просто игнорируйте людей»"},
                ]
            }
        ],
        "outcomes": {
            "diag_confirm": ("❌", "Марина решила, что её действительно никто не любит. Токсичные паттерны поведения усилились. Вы подтвердили искажённое восприятие."),
            "diag_esteem":  ("⚠️", "Работа с самооценкой дала частичный результат. Нестабильность в отношениях сохраняется."),
            "diag_attach":  ("✅", "Через три месяца Марина написала: «Я подождала два часа и не написала. Это был мой рекорд». Вы нашли верный путь."),
            "diag_ignore":  ("❌", "Марина ушла в изоляцию. Тревога усилилась. Рекомендация только навредила."),
        }
    }
}

MATERIALS = [
    {
        "id": "mat1",
        "title": "📘 Стресс vs Тревожное расстройство",
        "text": (
            "🧠 *Суть:*\n"
            "Стресс — реакция на конкретную ситуацию\n"
            "Тревога — ощущение угрозы без явной причины\n\n"
            "🔍 *Как отличить:*\n"
            "Стресс → есть чёткая причина, проходит со временем\n"
            "Тревога → причина размыта, длится долго, навязчивые мысли\n\n"
            "⚠️ *Сигналы тревоги:*\n"
            "«Я не знаю, почему мне плохо» • постоянное напряжение • проблемы со сном\n\n"
            "❌ *Частая ошибка:* списывать тревогу на «просто устал»"
        ),
        "question": "Пациент говорит: «Мне плохо, но я не знаю почему. Так уже месяц.» Это скорее…",
        "options": [
            {"text": "Рабочий стресс", "correct": False},
            {"text": "Тревожное расстройство", "correct": True},
            {"text": "Просто усталость", "correct": False},
        ]
    },
    {
        "id": "mat2",
        "title": "📘 Почему пациенты не говорят правду сразу",
        "text": (
            "🧠 *Суть:*\n"
            "Пациенты часто скрывают детали, искажают информацию, проверяют вашу реакцию. "
            "Это не ложь ради обмана — это защита.\n\n"
            "🔍 *Причины:* страх осуждения • недоверие • стыд • контроль\n\n"
            "🧩 *Признаки скрытого:*\n"
            "противоречия • уклончивые ответы • резкая смена темы\n\n"
            "💡 *Правило:* Сначала доверие — потом правда\n\n"
            "❌ *Ошибка:* сразу делать выводы без полной информации"
        ),
        "question": "Пациент резко сменил тему, когда вы спросили о семье. Что делать?",
        "options": [
            {"text": "Надавить и вернуть к теме", "correct": False},
            {"text": "Мягко вернуться позже, когда будет доверие", "correct": True},
            {"text": "Забыть об этом", "correct": False},
        ]
    },
    {
        "id": "mat3",
        "title": "📘 Опасные сигналы",
        "text": (
            "🚨 *Красные флаги:*\n"
            "• «Мне кажется, за мной следят»\n"
            "• «Я не сплю несколько дней»\n"
            "• «Я не контролирую себя»\n"
            "• «Иногда хочется причинить вред»\n\n"
            "⚠️ *Что это может означать:*\n"
            "психоз • сильное истощение • потеря контроля\n\n"
            "✅ *Как действовать:*\n"
            "Не игнорировать • не обесценивать • направлять к специалисту\n\n"
            "❌ *Ошибка:* «Это нормально» / «Просто отдохните»"
        ),
        "question": "Пациент говорит: «Я не сплю 4 дня, боюсь потерять контроль». Ваши действия?",
        "options": [
            {"text": "Назначить отдых и чай с мелиссой", "correct": False},
            {"text": "Немедленно направить к психиатру", "correct": True},
            {"text": "Продолжить обычную терапию", "correct": False},
        ]
    },
]

# ─────────────────────────────────────────────
#  STATE MANAGEMENT  (in-memory)
# ─────────────────────────────────────────────

users: dict[int, dict] = {}

def get_user(uid: int) -> dict:
    if uid not in users:
        users[uid] = {
            "state": "menu",
            "energy": 5,
            "stress": 0,
            "reputation": 0,
            "day": 1,
            "patients_done": [],
            "pending_outcomes": [],          # list of (patient_id, choice_id)
            "current_patient": None,
            "current_stage": 0,
            "trust": 5,
            "last_choice": None,
            "material_index": 0,
            "stats": {"correct": 0, "partial": 0, "wrong": 0},
        }
    return users[uid]

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

def kb(*rows):
    """Build InlineKeyboardMarkup from rows of (text, callback_data) tuples."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in rows])

def stats_bar(u: dict) -> str:
    e = "🧠" * u["energy"] + "⬜" * (5 - u["energy"])
    s = min(u["stress"], 5)
    st = "😰" * s + "⬜" * (5 - s)
    return f"Энергия: {e}\nСтресс:  {st}\n⭐ Репутация: {u['reputation']}"

async def send(update: Update, text: str, markup=None, parse_mode="Markdown"):
    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=markup, parse_mode=parse_mode)
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode=parse_mode)

# ─────────────────────────────────────────────
#  SCREENS
# ─────────────────────────────────────────────

async def show_main_menu(update: Update, u: dict):
    u["state"] = "menu"
    text = (
        f"🏥 *КАБИНЕТ — День {u['day']}*\n\n"
        f"{stats_bar(u)}\n\n"
        "Что делаем?"
    )
    pending_txt = ""
    if u["pending_outcomes"]:
        pending_txt = f"\n📩 Есть {len(u['pending_outcomes'])} отчёт(а) от пациентов"

    markup = kb(
        [("🚪 Принять пациента", "start_session")],
        [("📩 Получить отчёты", "check_outcomes")] if u["pending_outcomes"] else [],
        [("📊 Моя статистика", "show_stats"), ("ℹ️ О игре", "about")],
    )
    await send(update, text + pending_txt, markup)


async def show_patient_select(update: Update, u: dict):
    if u["energy"] <= 0:
        await send(update, "😔 У вас закончилась энергия. Приходите завтра.\n\n_/start чтобы перейти к следующему дню_")
        return

    available = [p for pid, p in PATIENTS.items() if pid not in u["patients_done"]]
    if not available:
        await send(update,
            "✅ *Вы приняли всех пациентов этого дня!*\n\n"
            "Используйте /newday чтобы перейти к следующему дню.",
            kb([("🌅 Следующий день", "new_day")])
        )
        return

    buttons = [[(f"👤 {p['name']}, {p['age']} лет — {p['job']}", f"patient_{p['id']}")] for p in available]
    buttons.append([("🔙 Назад", "menu")])
    await send(update, "👥 *Выберите пациента:*\n\n_Внимательно читайте — от вас зависит их судьба._",
               InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d)] for row in buttons for t, d in row]))


async def start_patient(update: Update, u: dict, patient_id: str):
    p = PATIENTS[patient_id]
    u["current_patient"] = patient_id
    u["current_stage"] = 0
    u["trust"] = 5
    u["last_choice"] = None
    u["state"] = "in_session"
    u["energy"] -= 1

    await send(update, p["intro"])
    await asyncio.sleep(1)
    await show_stage(update, u, p, 0)


async def show_stage(update: Update, u: dict, p: dict, stage_idx: int):
    stage = p["stages"][stage_idx]
    u["current_stage"] = stage_idx

    text = stage["text"]
    choices = stage["choices"]

    buttons = [[(c["text"], f"choice_{c['id']}")] for c in choices]
    await send(update, f"_{text}_", InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d)] for row in buttons for t, d in row]))


async def handle_choice(update: Update, u: dict, choice_id: str):
    patient_id = u["current_patient"]
    p = PATIENTS[patient_id]
    stage_idx = u["current_stage"]
    stage = p["stages"][stage_idx]

    # Trust adjustments
    if choice_id in ("doubt", "pressure"):
        u["trust"] = max(0, u["trust"] - 2)
    elif choice_id in ("support",):
        u["trust"] = min(10, u["trust"] + 1)

    u["last_choice"] = choice_id

    next_stage_idx = stage_idx + 1

    if next_stage_idx < len(p["stages"]):
        next_stage = p["stages"][next_stage_idx]
        # Show response if available
        if "responses" in next_stage and choice_id in next_stage["responses"]:
            resp_text = next_stage["responses"][choice_id]
            await send(update, resp_text)
            await asyncio.sleep(0)

        await show_stage(update, u, p, next_stage_idx)
    else:
        # Final diagnosis stage
        if stage["id"] == "s3":
            await finalize_patient(update, u, patient_id, choice_id)


async def finalize_patient(update: Update, u: dict, patient_id: str, diag_id: str):
    p = PATIENTS[patient_id]
    icon, outcome_text = p["outcomes"][diag_id]

    u["patients_done"].append(patient_id)
    u["state"] = "post_session"

    # Update stats
    if icon == "✅":
        u["stats"]["correct"] += 1
        u["reputation"] += 2
        u["stress"] = max(0, u["stress"] - 1)
    elif icon == "⚠️":
        u["stats"]["partial"] += 1
        u["reputation"] += 1
        u["stress"] += 1
    else:
        u["stats"]["wrong"] += 1
        u["stress"] += 2

    # Store outcome for later
    u["pending_outcomes"].append((patient_id, diag_id))

    await send(update,
        f"📁 *Кейс закрыт*\n\n"
        f"Ваше решение принято. Мы сообщим о результате позже.\n\n"
        f"_Стресс: {'😰' * min(u['stress'], 5)}_"
    )

    await asyncio.sleep(1)

    # Show educational material
    mat_idx = u["material_index"] % len(MATERIALS)
    u["material_index"] += 1
    await show_material(update, u, MATERIALS[mat_idx])


async def show_material(update: Update, u: dict, mat: dict):
    u["state"] = f"material_{mat['id']}"
    await send(update,
        f"📚 *Обучающий блок*\n\n{mat['title']}\n\n{mat['text']}",
        kb([("✅ Понял, проверить себя", f"mat_quiz_{mat['id']}")])
    )


async def show_material_quiz(update: Update, u: dict, mat_id: str):
    mat = next(m for m in MATERIALS if m["id"] == mat_id)
    buttons = [[(opt["text"], f"mat_ans_{mat_id}_{i}")] for i, opt in enumerate(mat["options"])]
    buttons.append([("⏭ Пропустить", "menu")])
    await send(update,
        f"❓ *Вопрос:*\n\n{mat['question']}",
        InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d)] for row in buttons for t, d in row])
    )


async def handle_material_answer(update: Update, u: dict, mat_id: str, ans_idx: int):
    mat = next(m for m in MATERIALS if m["id"] == mat_id)
    opt = mat["options"][ans_idx]
    if opt["correct"]:
        u["reputation"] += 1
        resp = "✅ *Верно!* Вы применяете знания правильно. +1 ⭐"
    else:
        correct = next(o["text"] for o in mat["options"] if o["correct"])
        resp = f"❌ *Не совсем.* Правильный ответ: _{correct}_"

    await send(update, resp)
    await asyncio.sleep(0)
    await show_main_menu(update, u)


async def show_outcomes(update: Update, u: dict):
    if not u["pending_outcomes"]:
        await send(update, "📭 Нет новых отчётов.", kb([("🔙 Назад", "menu")]))
        return

    text = "📩 *Отчёты по вашим пациентам:*\n\n"
    for patient_id, diag_id in u["pending_outcomes"]:
        p = PATIENTS[patient_id]
        icon, outcome = p["outcomes"][diag_id]
        text += f"{icon} *{p['name']}*\n{outcome}\n\n"

    u["pending_outcomes"] = []
    await send(update, text, kb([("🔙 В меню", "menu")]))


# ─────────────────────────────────────────────
#  HANDLERS
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)
    await send(update,
        "🏥 *КАБИНЕТ*\n\n"
        "Вы — молодой специалист в психологической клинике.\n"
        "Перед вами — люди, которым нужна помощь.\n\n"
        "Читайте внимательно. Слушайте. Принимайте решения.\n"
        "_Иногда правильного ответа нет. Но ошибки имеют последствия._\n\n"
        "Удачи, доктор.",
        kb([("▶️ Начать работу", "menu")])
    )


async def cmd_newday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user(update.effective_user.id)
    u["day"] += 1
    u["patients_done"] = []
    u["energy"] = 5
    if u["stress"] > 0:
        u["stress"] -= 1
    await send(update, f"🌅 *Новый день — День {u['day']}*\n\nВы отдохнули. Энергия восстановлена.\n_Новые пациенты ждут._")
    await show_main_menu(update, u)


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    u = get_user(q.from_user.id)
    data = q.data

    if data == "menu":
        await show_main_menu(update, u)
    elif data == "start_session":
        await show_patient_select(update, u)
    elif data.startswith("patient_"):
        pid = data[len("patient_"):]
        await start_patient(update, u, pid)
    elif data.startswith("choice_"):
        cid = data[len("choice_"):]
        # Final diagnosis choices
        if cid.startswith("diag_"):
            patient_id = u["current_patient"]
            await finalize_patient(update, u, patient_id, cid)
        else:
            await handle_choice(update, u, cid)
    elif data == "check_outcomes":
        await show_outcomes(update, u)
    elif data.startswith("mat_quiz_"):
        mat_id = data[len("mat_quiz_"):]
        await show_material_quiz(update, u, mat_id)
    elif data.startswith("mat_ans_"):
        parts = data.split("_")
        # mat_ans_{mat_id}_{idx}  — mat_id can be "mat1" etc
        idx = int(parts[-1])
        mat_id = "_".join(parts[2:-1])
        await handle_material_answer(update, u, mat_id, idx)
    elif data == "new_day":
        u["day"] += 1
        u["patients_done"] = []
        u["energy"] = 5
        if u["stress"] > 0:
            u["stress"] -= 1
        await send(update, f"🌅 *День {u['day']}* начался. Энергия восстановлена.")
        await show_main_menu(update, u)
    elif data == "show_stats":
        s = u["stats"]
        total = s["correct"] + s["partial"] + s["wrong"]
        await send(update,
            f"📊 *Ваша статистика*\n\n"
            f"✅ Верных решений: {s['correct']}\n"
            f"⚠️ Частично верных: {s['partial']}\n"
            f"❌ Ошибок: {s['wrong']}\n"
            f"📋 Всего кейсов: {total}\n\n"
            f"⭐ Репутация: {u['reputation']}\n"
            f"😰 Стресс: {u['stress']}/10\n"
            f"🧠 Энергия: {u['energy']}/5",
            kb([("🔙 Назад", "menu")])
        )
    elif data == "about":
        await send(update,
            "ℹ️ *О игре*\n\n"
            "«Кабинет» — нарративный симулятор психолога.\n\n"
            "Принимайте пациентов, анализируйте их истории, "
            "принимайте решения. Каждый выбор имеет последствия.\n\n"
            "📚 Между сессиями — обучающие материалы по психологии.\n\n"
            "_Создано на основе реальных концепций психологической помощи._",
            kb([("🔙 Назад", "menu")])
        )


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("newday", cmd_newday))
    app.add_handler(CallbackQueryHandler(callback))
    logger.info("🏥 КАБИНЕТ запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
