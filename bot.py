import telebot
import random
import datetime
import sqlite3
import os  
from dotenv import load_dotenv 
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import atexit


load_dotenv()

# ==================== НАСТРОЙКИ ====================
TOKEN = os.getenv("BOT_TOKEN") 

ADMIN_ID = 5602213785
BOT_USERNAME = "cashblrd_bot"
WITHDRAW_CHANNEL = "@cashzay"

if not TOKEN:
    print("Ошибка: Токен не найден в файле .env!")
    exit()

bot = telebot.TeleBot(TOKEN)


DB_PATH = "bot_data.db"

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.execute("PRAGMA journal_mode=WAL;")
conn.execute("PRAGMA busy_timeout=5000;")

atexit.register(conn.close)

# ==================== ИНИЦИАЛИЗАЦИЯ БАЗЫ ====================
def init_db():
    c = conn.cursor()
    try: c.execute("ALTER TABLE users ADD COLUMN games_today INTEGER DEFAULT 0")
    except: pass
    try: c.execute("ALTER TABLE users ADD COLUMN last_game_date TEXT DEFAULT NULL")
    except: pass
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            stars INTEGER DEFAULT 10,
            referrals INTEGER DEFAULT 0,
            referrer_id INTEGER DEFAULT NULL,
            games_today INTEGER DEFAULT 0,
            last_game_date TEXT DEFAULT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS sponsors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_username TEXT UNIQUE NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id INTEGER,
            sponsor_id INTEGER,
            subscribed_at TEXT,
            PRIMARY KEY (user_id, sponsor_id)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            amount INTEGER,
            item TEXT,
            status TEXT DEFAULT 'Ожидает обработки',
            created_at TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            stars INTEGER NOT NULL,
            activations_left INTEGER NOT NULL,
            created_at TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS promo_activations (
            user_id INTEGER,
            code TEXT,
            activated_at TEXT,
            PRIMARY KEY (user_id, code)
        )
    ''')

    try:
        c.execute("ALTER TABLE users ADD COLUMN tasks_today INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
        
conn.commit()

init_db()

# ==================== ФУНКЦИИ ====================
def get_stars(user_id):
    c = conn.cursor()
    c.execute("SELECT stars FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    return row[0] if row else 10

def add_stars(user_id, amount):
    c = conn.cursor()
    c.execute("SELECT stars FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    current = row[0] if row else 10
    new_balance = current + amount
    if new_balance < 0:
        new_balance = 0
    c.execute("""
        INSERT INTO users (user_id, stars) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET stars = ?
    """, (user_id, new_balance, new_balance))
    conn.commit()

def register_referral(user_id, referrer_id):
    if user_id == referrer_id:
        return
    c = conn.cursor()
    c.execute("SELECT referrer_id FROM users WHERE user_id = ?", (user_id,))
    if c.fetchone():
        return
    c.execute("INSERT INTO users (user_id, referrer_id) VALUES (?, ?)", (user_id, referrer_id))
    c.execute("UPDATE users SET referrals = referrals + 1 WHERE user_id = ?", (referrer_id,))
    add_stars(referrer_id, 7)   
    add_stars(user_id, 3)
    conn.commit()

    try:
        bot.send_message(referrer_id, 
            f"🎉 Новый реферал!\n"
            f"+7 ⭐ тебе\n"
            f"ID нового: {user_id}\n"
            f"Приглашай ещё — чем больше рефералов, тем круче награды!)")
    except Exception as e:
        print(f"Ошибка уведомления рефереру {referrer_id}: {e}")

    try:
        bot.send_message(ADMIN_ID, f"Новый реферал! {user_id} от {referrer_id}")
    except:
        pass

def get_random_available_sponsor(user_id):
    c = conn.cursor()
    c.execute("""
        SELECT s.id, s.channel_username 
        FROM sponsors s 
        LEFT JOIN subscriptions sub ON sub.sponsor_id = s.id AND sub.user_id = ?
        WHERE sub.sponsor_id IS NULL
        ORDER BY RANDOM() LIMIT 1
    """, (user_id,))
    row = c.fetchone()
    return {"id": row[0], "username": row[1]} if row else None

def mark_subscribed(user_id, sponsor_id):
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO subscriptions (user_id, sponsor_id, subscribed_at) VALUES (?, ?, ?)",
              (user_id, sponsor_id, datetime.datetime.now().isoformat()))
    conn.commit()

def add_sponsor(channel_username):
    c = conn.cursor()
    try:
        c.execute("INSERT INTO sponsors (channel_username) VALUES (?)", (channel_username,))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def delete_sponsor(channel_username):
    c = conn.cursor()
    c.execute("DELETE FROM sponsors WHERE channel_username = ?", (channel_username,))
    conn.commit()

def get_all_sponsors():
    c = conn.cursor()
    c.execute("SELECT channel_username FROM sponsors")
    return [row[0] for row in c.fetchall()]

def get_stats():
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users"); users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM subscriptions"); subs = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM sponsors"); sponsors = c.fetchone()[0]
    return f"Пользователей: {users}\nПодписок: {subs}\nСпонсоров: {sponsors}"

def get_user_games_today(user_id):
    c = conn.cursor()
    c.execute("SELECT games_today, last_game_date FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    today = datetime.date.today().isoformat()
    if row:
        games, last_date = row
        if last_date == today:
            return games
        else:
            c.execute("UPDATE users SET games_today = 0, last_game_date = ? WHERE user_id = ?", (today, user_id))
            conn.commit()
            return 0
    return 0

def increment_games_today(user_id):
    today = datetime.date.today().isoformat()
    c = conn.cursor()
    c.execute("UPDATE users SET games_today = games_today + 1, last_game_date = ? WHERE user_id = ?",
              (today, user_id))
    conn.commit()

def get_referrals_count(user_id):
    c = conn.cursor()
    c.execute("SELECT referrals FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    return row[0] if row else 0

def get_user_profile(user_id):
    c = conn.cursor()
    c.execute("SELECT stars, referrals, games_today FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if not row:
        return None
    stars, referrals, games_today = row
    
    c.execute("SELECT COUNT(*) FROM subscriptions WHERE user_id = ?", (user_id,))
    subs_count = c.fetchone()[0]
    
    return {
        "stars": stars,
        "referrals": referrals,
        "games_today": games_today,
        "subs_count": subs_count
    }

def get_sponsor_stats():
    c = conn.cursor()
    c.execute("SELECT s.channel_username, COUNT(sub.user_id) as subs FROM sponsors s "
              "LEFT JOIN subscriptions sub ON sub.sponsor_id = s.id GROUP BY s.id")
    return c.fetchall()

def create_withdrawal(user_id, username, amount, item):
    c = conn.cursor()
    c.execute("SELECT MAX(id) FROM withdrawals")
    last_id = c.fetchone()[0] or 0
    new_id = last_id + 1
    c.execute("""
        INSERT INTO withdrawals (id, user_id, username, amount, item, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (new_id, user_id, username, amount, item, datetime.datetime.now().isoformat()))
    conn.commit()
    return new_id

def update_withdrawal_status(withdrawal_id, new_status):
    c = conn.cursor()
    c.execute("UPDATE withdrawals SET status = ? WHERE id = ?", (new_status, withdrawal_id))
    conn.commit()

def get_withdrawal_message_text(withdrawal_id, user_id, username, amount, item, status):
    return (
        f"Вывод #{withdrawal_id}\n"
        f"👤 Юзер: @{username} | ID: {user_id}\n"
        f"💫 Количество: {amout}.0 [{item}]\n"
        f"Статус: {status}"
    )

def create_promo(code, stars, activations):
    c = conn.cursor()
    try:
        c.execute("INSERT INTO promo_codes (code, stars, activations_left, created_at) VALUES (?, ?, ?, ?)",
                  (code, stars, activations, datetime.datetime.now().isoformat()))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def activate_promo(user_id, code):
    c = conn.cursor()
    c.execute("SELECT stars, activations_left FROM promo_codes WHERE code = ?", (code,))
    row = c.fetchone()
    if not row:
        return "Промокод не найден или истёк"
    
    stars, left = row
    if left <= 0:
        return "Промокод исчерпан"
    
    c.execute("SELECT 1 FROM promo_activations WHERE user_id = ? AND code = ?", (user_id, code))
    if c.fetchone():
        return "Ты уже активировал этот промокод"
    
    c.execute("UPDATE promo_codes SET activations_left = activations_left - 1 WHERE code = ?", (code,))
    
    c.execute("INSERT INTO promo_activations (user_id, code) VALUES (?, ?)", (user_id, code))

    add_stars(user_id, stars)
    
    conn.commit()
    return f"Промокод активирован! +{stars} ⭐"

def process_admin_task_sponsor(message):
    channel = message.text.strip()
    if not channel.startswith('@'):
        channel = '@' + channel
    
    c = conn.cursor()
    try:
        c.execute("INSERT INTO sponsors (channel_username, for_tasks) VALUES (?, 1)", (channel,))
        conn.commit()
        bot.reply_to(message, f"Канал {channel} добавлен для заданий!")
    except sqlite3.IntegrityError:
        c.execute("UPDATE sponsors SET for_tasks = 1 WHERE channel_username = ?", (channel,))
        conn.commit()
        bot.reply_to(message, f"Канал {channel} обновлён для заданий!")
# ==================== ХЭНДЛЕРЫ ====================
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    referrer_id = None
    if len(message.text.split()) > 1 and message.text.split()[1].startswith("ref_"):
        try:
            referrer_id = int(message.text.split()[1].split("_")[1])
        except:
            pass

    if referrer_id:
        register_referral(user_id, referrer_id)

    stars = get_stars(user_id)
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"

    text = (
        f"Привет, {message.from_user.first_name}! ⭐\n"
        f"У тебя {stars} звёзд\n\n"
        f"Играй: /play\n"
        f"Приглашай друзей: {ref_link}\n"
        "За друга +7⭐ тебе, +3⭐ ему!\n\n"
    )

    markup = InlineKeyboardMarkup()
    markup.add(types.KeyboardButton("💼Задания"))
    markup.add(InlineKeyboardButton("🎁 Промокод", callback_data="enter_promo"))

    bot.send_message(message.chat.id, text, reply_markup=markup)

@bot.message_handler(commands=['me'])
def profile(message):
    user_id = message.from_user.id
    stars = get_stars(user_id)
    referrals = get_referrals_count(user_id)
    games_today = get_user_games_today(user_id)
    text = (
        "🛡️ **Твой профиль** 🛡️\n\n"
        f"⭐ Звёзды: {stars}\n"
        f"👥 Рефералы: {referrals}\n"
        f"🎮 Игр сегодня: {games_today}/20\n\n"
        "Приглашай друзей, чтобы получить больше игр и звёзд!"
    )
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(commands=['play'])
def play(message):
    user_id = message.from_user.id
    if get_user_games_today(user_id) >= 20:
        bot.reply_to(message, "Ты уже сыграл 20 игр сегодня! Приходи завтра или пригласи друга за +7 звёзд.")
        return

    stars = get_stars(user_id)
    if stars < 3:
        bot.reply_to(message, f"Недостаточно звёзд ({stars} ⭐). Нужно минимум 3.\nПригласи друга за +7 ⭐ или подожди бонус!")
        return

    increment_games_today(user_id)

    correct_color = random.choice(["blue", "red"])
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("Синий 🔵", callback_data=f"guess_blue_{correct_color}"),
        InlineKeyboardButton("Красный 🔴", callback_data=f"guess_red_{correct_color}")
    )
    bot.send_message(message.chat.id, "Выбери цвет круга:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("guess_"))
def process_guess(call):
    user_id = call.from_user.id
    _, guess, correct = call.data.split("_")

    if guess == correct:
        add_stars(user_id, 3)
        result = "Правильно! +3 ⭐"
    else:
        add_stars(user_id, -3)
        result = "Неправильно... -3 ⭐"

    stars = get_stars(user_id)
    text = f"{result}\nТвой баланс: {stars} ⭐"

    if random.random() < 0.25:
        sponsor = get_random_available_sponsor(user_id)
        if sponsor:
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("Подписаться на канал", url=f"https://t.me/{sponsor['username']}"))
            markup.row(InlineKeyboardButton("Проверить подписку", callback_data=f"check_sub_{sponsor['id']}"))
            markup.row(InlineKeyboardButton("Пригласить друга", url=f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"))
            text += "\n\nЧтобы продолжить — подпишись на спонсора или пригласи друга!"
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)
            bot.answer_callback_query(call.id)
            return

    bot.edit_message_text(text + "\n\n/play — следующая", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("check_sub_"))
def check_sub(call):
    user_id = call.from_user.id
    sponsor_id = int(call.data.split("_")[2])

    c = conn.cursor()
    c.execute("SELECT channel_username FROM sponsors WHERE id = ?", (sponsor_id,))
    row = c.fetchone()
    if not row:
        bot.answer_callback_query(call.id, "Канал не найден", show_alert=True)
        return
    channel = row[0]

    try:
        member = bot.get_chat_member(f"@{channel}", user_id)
        if member.status in ['member', 'administrator', 'creator']:
            mark_subscribed(user_id, sponsor_id)
            add_stars(user_id, 5)# ← +10 за подписку (можно изменить)
            bot.answer_callback_query(call.id, "Подписка подтверждена! +5 ⭐", show_alert=True)
            bot.delete_message(call.message.chat.id, call.message.message_id)
            bot.send_message(ADMIN_ID, f"Новая подписка! {user_id} на @{channel}")
            play(call.message)
            
        else:
            bot.answer_callback_query(call.id, "Ещё не подписан. Подпишись и попробуй снова!", show_alert=True)
    except Exception as e:
        bot.answer_callback_query(call.id, f"Ошибка: {str(e)}", show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data == "enter_promo")
def enter_promo(call):
    admin_states[call.from_user.id] = "waiting_promo_code"
    bot.send_message(call.message.chat.id, "Введите промокод:")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("check_sub_"))
def check_sub_callback(call):
    user_id = call.from_user.id
    channel = call.data.split("_")[2]

    try:
        status = bot.get_chat_member(channel, user_id).status
        if status in ['member', 'administrator', 'creator']:
            c = conn.cursor()
            c.execute("UPDATE users SET stars = stars + 0.25 WHERE user_id = ?", (user_id,))
            conn.commit()

            bot.answer_callback_query(call.id, "Подписка подтверждена! +0.25⭐️", show_alert=True)
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="Подписка подтверждена! +0.25⭐️\nМожешь взять новое задание позже."
            )
        else:
            bot.answer_callback_query(call.id, "Ты ещё не подписался 😕 Подпишись и попробуй снова!", show_alert=True)
    except Exception as e:
        bot.answer_callback_query(call.id, "Ошибка проверки подписки. Попробуй позже.", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data == "admin_task_sponsor")
def admin_task_sponsor(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён")
        return
    
    bot.send_message(
        call.message.chat.id,
        "Введи @username канала для заданий (например @channel_name):"
    )
    bot.register_next_step_handler(call.message, process_admin_task_sponsor)


# ==================== МАРКЕТ ====================
@bot.message_handler(commands=['market'])
def market(message):
    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("Подарок 🧸/💝 — 50 ⭐", callback_data="buy_gift"))
    markup.row(InlineKeyboardButton("Мой баланс", callback_data="market_balance"))
    bot.send_message(message.chat.id, 
        "🛒 **Маркет** 🛒\n\n"
        "Обменяй звёзды на реальные подарки!\n\n"
        "Доступно сейчас:\n"
        "• Подарок (🧸/💝) — 50 звёзд (нужно 5+ рефералов)\n\n"
        "Выбери товар ниже 👇", 
        reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data in ["buy_gift", "market_balance"])
def market_callback(call):
    user_id = call.from_user.id
    username = call.from_user.username or "без_ника"

    if call.data == "market_balance":
        stars = get_stars(user_id)
        bot.answer_callback_query(call.id, f"Твой баланс: {stars} ⭐", show_alert=True)
        return

    if call.data == "buy_gift":
        stars = get_stars(user_id)
        referrals = get_referrals_count(user_id)
        
        if referrals < 5:
            bot.answer_callback_query(call.id, f"Нужно минимум 5 рефералов для вывода подарка! У тебя {referrals}", show_alert=True)
            return
        
        if stars < 50:
            bot.answer_callback_query(call.id, f"Недостаточно звёзд! Нужно 50, у тебя {stars}", show_alert=True)
            return

        add_stars(user_id, -50)
        withdrawal_id = create_withdrawal(user_id, username, 50, "🧸/💝")

        text = get_withdrawal_message_text(withdrawal_id, user_id, username, 50, "🧸/💝", "Ожидает обработки")
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("✅ Принять", callback_data=f"accept_{withdrawal_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{withdrawal_id}")
        )
        bot.send_message(WITHDRAW_CHANNEL, text, reply_markup=markup, parse_mode="Markdown")
        bot.answer_callback_query(call.id, "Заявка создана! Ожидай обработки в канале: @cashzay", show_alert=True)

# ==================== ЛИДЕРБОРД ====================
@bot.message_handler(commands=['top'])
def top(message):
    c = conn.cursor()
    c.execute("SELECT user_id, referrals FROM users ORDER BY referrals DESC LIMIT 10")
    leaders = c.fetchall()
    
    if not leaders:
        bot.reply_to(message, "Пока нет лидеров 😔")
        return
    
    text = "🏆 **Топ-10 по рефералам** 🏆\n\n"
    for i, (user_id, refs) in enumerate(leaders, 1):
        try:
            user = bot.get_chat(user_id)
            name = user.first_name or f"ID {user_id}"
            text += f"{i}. {name} — {refs} 👥\n"
        except:
            text += f"{i}. ID {user_id} — {refs} 👥\n"
    
    bot.reply_to(message, text, parse_mode="Markdown")

# ==================== ПРОСМОТР ПРОФИЛЯ ЮЗЕРА ====================
@bot.callback_query_handler(func=lambda call: call.data == "admin_view_profile")
def admin_view_profile(call):
    admin_states[call.from_user.id] = "waiting_view_profile"
    bot.send_message(call.message.chat.id, "Введите @username или user_id юзера:")
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda message: message.from_user.id == ADMIN_ID and message.from_user.id in admin_states and admin_states[message.from_user.id] == "waiting_view_profile")
def view_profile_handler(message):
    query = message.text.strip().lstrip("@")
    try:
        if query.isdigit():
            user_id = int(query)
        else:
            
            user = bot.get_chat_member("@"+query, message.from_user.id)  
            user_id = user.user.id  
            bot.reply_to(message, "Пока поддерживается только поиск по user_id. Введи числовой ID.")
            return
    except:
        bot.reply_to(message, "Не удалось найти юзера. Введи числовой user_id.")
        return

    profile = get_user_profile(user_id)
    if not profile:
        bot.reply_to(message, "Юзер не найден.")
        del admin_states[message.from_user.id]
        return

    text = (
        f"Профиль пользователя {user_id}:\n\n"
        f"⭐ Звёзды: {profile['stars']}\n"
        f"👥 Рефералы: {profile['referrals']}\n"
        f"🎮 Игр сегодня: {profile['games_today']}/20\n"
        f"📊 Подписок на спонсоров: {profile['subs_count']}\n"
    )
    bot.reply_to(message, text)
    del admin_states[message.from_user.id]

# ==================== СТАТИСТИКА СПОНСОРОВ ====================
@bot.callback_query_handler(func=lambda call: call.data == "admin_sponsor_stats")
def sponsor_stats(call):
    stats = get_sponsor_stats()
    if not stats:
        text = "Нет спонсоров или подписок"
    else:
        text = "Статистика спонсоров:\n\n"
        for channel, subs in stats:
            text += f"@{channel}: {subs} подписок\n"
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("Назад", callback_data="admin_back"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)
    bot.answer_callback_query(call.id)

# ==================== АДМИН-КОНСОЛЬ ====================
admin_states = {}

@bot.message_handler(commands=['consol'])
def admin_consol(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "Доступ запрещён!")
        return

    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("Добавить спонсора", callback_data="admin_add"))
    markup.row(InlineKeyboardButton("Удалить спонсора", callback_data="admin_del"))
    markup.row(InlineKeyboardButton("Добавить звёзды юзеру", callback_data="admin_add_stars"))
    markup.row(InlineKeyboardButton("Отнять звёзды у юзера", callback_data="admin_del_stars"))
    markup.row(InlineKeyboardButton("Список спонсоров", callback_data="admin_list"))
    markup.row(InlineKeyboardButton("Статистика спонсоров", callback_data="admin_sponsor_stats"))
    markup.row(InlineKeyboardButton("Просмотр профиля юзера", callback_data="admin_view_profile"))
    markup.row(InlineKeyboardButton("Общая статистика", callback_data="admin_stats"))
    markup.row(InlineKeyboardButton("Создать промокод", callback_data="admin_create_promo"))
    markup.row(InlineKeyboardButton("Спонсор Задания", callback_data="admin_task_sponsor"))
    
    bot.reply_to(message, "Админ-консоль:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_"))
def admin_callback(call):
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Доступ запрещён!", show_alert=True)
        return

    action = call.data

    if action == "admin_add":
        admin_states[call.from_user.id] = "waiting_add_sponsor"
        bot.send_message(call.message.chat.id, "Введите username канала (без @):")
    elif action == "admin_del":
        admin_states[call.from_user.id] = "waiting_del_sponsor"
        bot.send_message(call.message.chat.id, "Введите username канала (без @):")
    elif action == "admin_add_stars":
        admin_states[call.from_user.id] = "waiting_add_stars_id"
        bot.send_message(call.message.chat.id, "Введите user_id юзера:")
    elif action == "admin_del_stars":
        admin_states[call.from_user.id] = "waiting_del_stars_id"
        bot.send_message(call.message.chat.id, "Введите user_id юзера:")
    elif action == "admin_list":
        sponsors = get_all_sponsors()
        text = "Спонсоры:\n" + "\n".join([f"@{s}" for s in sponsors]) if sponsors else "Нет спонсоров"
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("Назад", callback_data="admin_back"))
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)
    elif action == "admin_sponsor_stats":
        sponsor_stats(call)
    elif action == "admin_view_profile":
        admin_view_profile(call)
    elif action == "admin_stats":
        stats = get_stats()
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("Назад", callback_data="admin_back"))
        bot.edit_message_text(stats, call.message.chat.id, call.message.message_id, reply_markup=markup)
    elif action == "admin_back":
        admin_consol(call.message)
    elif action == "admin_create_promo":
        admin_states[call.from_user.id] = "waiting_create_promo"
        bot.send_message(call.message.chat.id, "Введите промокод в формате: код кол-во_звёзд кол-во_активаций\nПример: GIFT50 100 20")
        bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda message: message.from_user.id == ADMIN_ID and message.from_user.id in admin_states)
def admin_input_handler(message):
    state = admin_states.get(message.from_user.id)
    if not state:
        return

    if state == "waiting_add_sponsor":
        channel = message.text.strip()
        if add_sponsor(channel):
            bot.reply_to(message, f"Спонсор @{channel} добавлен!")
        else:
            bot.reply_to(message, "Уже есть или ошибка.")
        del admin_states[message.from_user.id]

    elif state == "waiting_del_sponsor":
        channel = message.text.strip()
        delete_sponsor(channel)
        bot.reply_to(message, f"Спонсор @{channel} удалён!")
        del admin_states[message.from_user.id]

    elif state == "waiting_add_stars_id":
        try:
            user_id = int(message.text.strip())
            admin_states[message.from_user.id] = {"state": "waiting_add_stars_amount", "user_id": user_id}
            bot.reply_to(message, f"Введите сумму звёзд для добавления юзеру {user_id}:")
        except ValueError:
            bot.reply_to(message, "Неверный ID. Попробуйте заново.")

    elif isinstance(admin_states[message.from_user.id], dict) and admin_states[message.from_user.id].get("state") == "waiting_add_stars_amount":
        try:
            amount = int(message.text.strip())
            user_id = admin_states[message.from_user.id]["user_id"]
            add_stars(user_id, amount)
            bot.reply_to(message, f"+{amount} звёзд добавлено юзеру {user_id}!")
            del admin_states[message.from_user.id]
        except ValueError:
            bot.reply_to(message, "Неверная сумма. Попробуйте заново.")

    elif state == "waiting_del_stars_id":
        try:
            user_id = int(message.text.strip())
            admin_states[message.from_user.id] = {"state": "waiting_del_stars_amount", "user_id": user_id}
            bot.reply_to(message, f"Введите сумму звёзд для отнимания у юзера {user_id}:")
        except ValueError:
            bot.reply_to(message, "Неверный ID. Попробуйте заново.")

    elif isinstance(admin_states[message.from_user.id], dict) and admin_states[message.from_user.id].get("state") == "waiting_del_stars_amount":
        try:
            amount = int(message.text.strip())
            user_id = admin_states[message.from_user.id]["user_id"]
            add_stars(user_id, -amount)
            bot.reply_to(message, f"-{amount} звёзд отнято у юзера {user_id}!")
            del admin_states[message.from_user.id]
        except ValueError:
            bot.reply_to(message, "Неверная сумма. Попробуйте заново.")

    elif state == "waiting_create_promo":
        try:
            parts = message.text.strip().split()
            if len(parts) != 3:
                bot.reply_to(message, "Неверный формат. Пример: GIFT50 100 20")
                return

            code, stars, activations = parts[0], int(parts[1]), int(parts[2])

            if create_promo(code, stars, activations):
                text = (
                    f"Промокод создан! 🎉\n\n"
                    f"Код: **{code}**\n"
                    f"Награда: +{stars} ⭐\n"
                    f"Активаций: {activations}\n\n"
                    f"Отправь в канал:\n"
                    f"🎁 Промокод: `{code}`\n"
                    f"Получи {stars} звёзд! Введи в боте /start → 🎁 Промокод"
                )
                bot.reply_to(message, text, parse_mode="Markdown")
            else:
                bot.reply_to(message, "Промокод с таким названием уже существует!")
        except ValueError:
            bot.reply_to(message, "Неверный формат чисел. Пример: GIFT50 100 20")
        del admin_states[message.from_user.id]
@bot.message_handler(func=lambda message: message.from_user.id in admin_states and admin_states[message.from_user.id] == "waiting_promo_code")
def promo_input_user(message):
    code = message.text.strip()
    result = activate_promo(message.from_user.id, code)
    bot.reply_to(message, result)
    del admin_states[message.from_user.id]

@bot.message_handler(func=lambda message: message.text == "💼Задания")
def tasks_handler(message):
    user_id = message.from_user.id

    c = conn.cursor()
    today = datetime.date.today().isoformat()
    c.execute("SELECT tasks_today FROM users WHERE user_id = ?", (user_id,))
    tasks_today = c.fetchone()[0] if c.fetchone() else 0

    if tasks_today >= 10:
        bot.reply_to(message, "Лимит заданий на сегодня. Завтра новые! 😔")
        return

    c.execute("SELECT channel_username FROM sponsors WHERE active = 1 LIMIT 1")
    sponsor = c.fetchone()

    if sponsor:
        channel = sponsor[0] 

        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton("Канал", url=f"https://t.me/{channel[1:]}"))
        markup.add(types.InlineKeyboardButton("Подтвердить", callback_data=f"check_sub_{channel}"))

        bot.reply_to(message,
            "Получи звезды за задание! 👇\n\n"
            f"🟢 Подпишись на канал и нажми \"Подтвердить\"\n\n"
            "Вознаграждение: +0.25⭐️",
            reply_markup=markup
        )

        c.execute("UPDATE users SET tasks_today = tasks_today + 1 WHERE user_id = ?", (user_id,))
        conn.commit()
    else:
        bot.reply_to(message, "Заданий пока нет 😔\nПриходи позже!")

# ==================== ЗАПУСК ====================
print("Бот запущен...")
bot.infinity_polling()
