import telebot
from telebot import types
import random
import json
import os
from datetime import datetime, timedelta, timezone
import threading
import time
import pickle
from functools import wraps
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from telebot.types import BotCommand
from telebot import TeleBot, types





TOKEN = "**"
ADMIN_IDS = [**]
DATA_FILE = "casino_data.json"
LOG_FILE = "casino_log.txt"
BONUS_AMOUNT = 100
BONUS_THRESHOLD = 50
PENDING_TOPUPS = []
USER_BANS = {} 
BAN_LOG_FILE = "ban_log.json"




bot = telebot.TeleBot(TOKEN)

# ========== DATA MANAGEMENT ==========

@bot.message_handler(commands=['menu'])
def show_menu(message):
    send_reply_main_menu(message.chat.id)

bot.set_my_commands([
    BotCommand("start", "Запуск/сброс бота"),
    BotCommand("menu", "Показать главное меню"),
    BotCommand("help", "Помощь"),
])    


def ban_guard_callback(func):
    @wraps(func)
    def wrapper(call, *args, **kwargs):
        user = get_user(call.from_user.id)
        username = user.get("username", "").lower()
        ban = USER_BANS.get(username)
        if ban and datetime.utcnow() < ban['until']:
            bot.answer_callback_query(
                call.id,
                f"⛔ Вы заблокированы до {ban['until'].strftime('%Y-%m-%d %H:%M:%S')} UTC.\nПричина: {ban['reason']}",
                show_alert=True
            )
            return
        return func(call, *args, **kwargs)
    return wrapper


def save_bans(bans):
    with open('user_bans.pickle', 'wb') as f:
        pickle.dump(bans, f)

def save_ban_log(ban_log):
    with open(BAN_LOG_FILE, "w", encoding="utf-8") as f:
        # Для сериализации datetimes — переводим их в строку
        serializable = []
        for entry in ban_log:
            entry_copy = entry.copy()
            for key in ["start", "until", "unban_time"]:
                if entry_copy.get(key) is not None:
                    entry_copy[key] = entry_copy[key].strftime("%Y-%m-%d %H:%M:%S")
            serializable.append(entry_copy)
        json.dump(serializable, f, ensure_ascii=False, indent=2)

def load_bans():
    try:
        with open('user_bans.pickle', 'rb') as f:
            return pickle.load(f)
    except Exception:
        return {}

def load_ban_log():
    try:
        with open(BAN_LOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Десериализация datetime
            for entry in data:
                for key in ["start", "until", "unban_time"]:
                    if entry.get(key):
                        from datetime import datetime
                        entry[key] = datetime.strptime(entry[key], "%Y-%m-%d %H:%M:%S")
            return data
    except Exception:
        return []

BAN_LOG = load_ban_log()

def load_data():
    if not os.path.isfile(DATA_FILE):
        with open(DATA_FILE, "w") as f:
            json.dump({"users": {}, "dp_codes": {}}, f)
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

def get_user(user_id):
    data = load_data()
    user_id = str(user_id)
    if user_id not in data["users"]:
        data["users"][user_id] = {
            "balance": 500,
            "username": "",
            "is_admin": user_id in map(str, ADMIN_IDS),
            "last_bonus": "",
            "registered": datetime.utcnow().strftime("%Y-%m-%d"),
            "exchanges": 0,
            "wins": 0,
            "bonuses": 0
        }
        save_data(data)
    return data["users"][user_id]

def set_user(user_id, user_data):
    data = load_data()
    data["users"][str(user_id)] = user_data
    save_data(data)

def update_username(user_id, username):
    user = get_user(user_id)
    if username:
        user["username"] = username.lstrip("@")
        set_user(user_id, user)

def get_top_users(limit=10):
    data = load_data()
    users = data["users"]
    sorted_users = sorted(users.items(), key=lambda x: x[1].get("balance", 0), reverse=True)
    return sorted_users[:limit]

def is_admin(user_id):
    user = get_user(user_id)
    return user.get("is_admin", False)

def find_userid_by_username(username):
    username = username.lstrip("@").lower()
    data = load_data()
    for uid, info in data["users"].items():
        if info.get("username", "").lower() == username:
            return int(uid)
    return None

def all_dp_codes_left():
    data = load_data()
    return dict(data.get("dp_codes", {}))

def dp_codes_left(nominal):
    data = load_data()
    nominal = str(nominal)
    return len(data.get("dp_codes", {}).get(nominal, []))

def add_dp_codes(nominal, codes, admin=None):
    data = load_data()
    nominal = str(nominal)
    if "dp_codes" not in data:
        data["dp_codes"] = {}
    if nominal not in data["dp_codes"]:
        data["dp_codes"][nominal] = []
    data["dp_codes"][nominal].extend(codes)
    save_data(data)
    log_dp(admin, f"Добавил коды номиналом {nominal}: {', '.join(codes)}")

def get_dp_code(nominal, user=None, admin=None):
    data = load_data()
    nominal = str(nominal)
    if "dp_codes" in data and nominal in data["dp_codes"] and data["dp_codes"][nominal]:
        code = data["dp_codes"][nominal][0]
        data["dp_codes"][nominal] = data["dp_codes"][nominal][1:]
        save_data(data)
        log_dp(user if user else admin, f"Выдал код {code} номиналом {nominal}")
        return code
    return None

# ========== LOGGING ==========
def write_log(line, log_type, user_id=None):
    dt = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if user_id:
            f.write(f"[{dt}] [{log_type}] [{user_id}] {line}\n")
        else:
            f.write(f"[{dt}] [{log_type}] {line}\n")

def log_gwcoin(user, admin, old, new, diff, reason):
    who = f"@{user['username']}" if user else "?"
    adminpart = f" [by @{admin['username']}]" if admin else ""
    write_log(f"{who}{adminpart}: {old} -> {new} ({'+' if diff>=0 else ''}{diff}) {reason}", "GWCOIN", user.get('username',''))

def log_dp(user, msg):
    who = f"@{user['username']}" if user else "?"
    write_log(f"{who}: {msg}", "DP", user.get('username',''))

def log_user(user, msg):
    who = f"@{user['username']}" if user else "?"
    write_log(f"{who}: {msg}", "USER", user.get('username',''))

def log_bonus(user, bonus):
    who = f"@{user['username']}"
    write_log(f"{who} получил бонус +{bonus} GW-coin", "GWCOIN", user.get('username',''))

def log_user_activity(user_id, activity):
    write_log(activity, "USER", user_id)

def log_user_win(user_id, game, amount):
    write_log(f"Выиграл в {game}: +{amount} GW-coin", "GWCOIN", user_id)

def log_user_exchange(user_id, gw, dp, code):
    write_log(f"Обменял {gw} GW-coin на {dp} DP (код: {code})", "DP", user_id)

def get_user_logs(user_id, count=20):
    if not os.path.isfile(LOG_FILE):
        return []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        lines = [line for line in f if f"[{user_id}]" in line]
    return [line.split(']',3)[-1].strip() for line in lines[-count:]]

def show_log_lines(log_type, count=15):
    if not os.path.isfile(LOG_FILE):
        return []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        lines = [line for line in f if f"[{log_type}]" in line]
    return lines[-count:]

def ensure_profile_fields(user):
    changed = False
    if 'exchanges' not in user:
        user['exchanges'] = 0
        changed = True
    if 'wins' not in user:
        user['wins'] = 0
        changed = True
    if 'bonuses' not in user:
        user['bonuses'] = 0
        changed = True
    if 'registered' not in user:
        user['registered'] = datetime.utcnow().strftime("%Y-%m-%d")
        changed = True
    return changed

def increment_profile_field(user_id, field):
    user = get_user(user_id)
    ensure_profile_fields(user)
    user[field] += 1
    set_user(user_id, user)

# ========== MAIN REPLY MENU ==========
def send_reply_main_menu(chat_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("🎮 Игры")
    markup.add("💱 Обмен")
    markup.add("🎁 Бонус")
    markup.add("🏆 Баланс")
    markup.add("📜 Профиль")
    markup.add("🏅 ТОП")
    markup.add("💸 Пополнить баланс GW-coins")
    if is_admin(chat_id):
        markup.add("⚙️ Админ-панель")
    bot.send_message(chat_id, "🏠 Главное меню:", reply_markup=markup)

@bot.message_handler(commands=['start'])
def start(message):
    username = message.from_user.username or message.from_user.first_name
    update_username(message.from_user.id, username)
    user = get_user(message.from_user.id)
    if ensure_profile_fields(user):
        set_user(message.from_user.id, user)
    log_user(user, f"Вошел в бота (start)")
    bot.send_message(message.chat.id, "Добро пожаловать в Казино! Ваш стартовый баланс: 1000 GW-coin.")
    send_reply_main_menu(message.chat.id)

# ========== GAMES MENU ==========
@bot.message_handler(func=lambda m: m.text == "🎮 Игры")
def games_menu(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🎰 Слоты", callback_data="game_slots"))
    markup.add(types.InlineKeyboardButton("🎲 Кости", callback_data="game_dice"))
    markup.add(types.InlineKeyboardButton("♠️ Блэкджек", callback_data="game_blackjack"))
    markup.add(types.InlineKeyboardButton("🎡 Рулетка", callback_data="game_roulette"))
    markup.add(types.InlineKeyboardButton("Назад", callback_data="back_main"))
    bot.send_message(message.chat.id, "Выберите игру:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "back_main")
def back_main(call):
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    send_reply_main_menu(call.message.chat.id)

# ========== СЛОТЫ ==========

# Константы
POPULAR_BETS = [(10, "⭐️"), (50, "⭐️"), (100, "⭐️"), (500, "⭐️")]
SLOT_EMOJIS = ["🍒", "🍋", "🍉", "⭐️", "7️⃣"]
ANIMATION_DELAYS = [0.3, 0.3, 0.5, 0.7, 1.0]  # Задержки для разных этапов анимации
MIN_BET = 10
MAX_BET = 500

def send_slots_bet_inline_keyboard(chat_id, user_id):
        # Получаем данные пользователя
    user = get_user(user_id)
    user_name = user.get("name", "Игрок")  # Используем имя из профиля или "Игрок" по умолчанию
    user_balance = user["balance"]
    markup = types.InlineKeyboardMarkup(row_width=2)
    for bet, emoji in POPULAR_BETS:
        markup.add(types.InlineKeyboardButton(f"{emoji} {bet}", callback_data=f"slot_bet_{bet}"))
    markup.add(types.InlineKeyboardButton("🎲 Другая сумма", callback_data="slot_bet_custom"))
    bot.send_message(
        chat_id,
        f"🎰 {user_name}, ваш баланс: {user_balance} GW-coin\n\n"
        f"Выберите ставку (минимум {MIN_BET}, максимум {MAX_BET} GW-coin):",
        reply_markup=markup
    )

def animate_slot_roll(chat_id, message_id, result):
    """Анимация вращения слотов с постепенной остановкой"""
    last_text = None
    
    for i in range(5):
        # Генерация текущего состояния барабанов
        if i < 2:  # Все барабаны крутятся
            roll = [random.choice(SLOT_EMOJIS) for _ in range(3)]
        elif i == 2:  # Остановлен первый барабан
            roll = [result[0], random.choice(SLOT_EMOJIS), random.choice(SLOT_EMOJIS)]
        elif i == 3:  # Остановлены два барабана
            roll = [result[0], result[1], random.choice(SLOT_EMOJIS)]
        else:  # Все барабаны остановлены
            roll = result
        
        # Формируем текст сообщения
        current_text = format_slot_message(roll, i)
        
        # Редактируем только если текст изменился
        if current_text != last_text:
            try:
                bot.edit_message_text(
                    text=current_text,
                    chat_id=chat_id,
                    message_id=message_id
                )
                last_text = current_text
            except Exception as e:
                print(f"Не удалось обновить анимацию: {e}")
                # Продолжаем анимацию несмотря на ошибку
            
        time.sleep(ANIMATION_DELAYS[i])

def format_slot_message(roll, step):
    """Форматирует сообщение со слотами в зависимости от этапа анимации"""
    if step < 2:
        return f"🎰 {' | '.join(roll)}"
    
    # Для этапов с частичной остановкой
    parts = []
    for i in range(3):
        if step == 2 and i == 0:  # Первый остановлен
            parts.append(roll[0])
        elif step == 3 and i < 2:  # Первые два остановлены
            parts.append(roll[i])
        elif step >= 4:  # Все остановлены
            parts.append(roll[i])
        else:
            parts.append("🔄")  # Вращающийся символ
    
    return f"🎰 {' | '.join(parts)}"

def process_slots_bet_execute(message, bet):
    user_id = message.from_user.id
    user = get_user(user_id)
    user_balance = user["balance"]

    if bet > user_balance:
        bot.send_message(message.chat.id, "❗️ У вас недостаточно GW-coin!")
        return
    if bet > MAX_BET:
        bot.send_message(message.chat.id, f"🚫 Максимальная ставка — {MAX_BET} GW-coin.")
        return
    if bet < MIN_BET:
        bot.send_message(message.chat.id, f"🚫 Минимальная ставка — {MIN_BET} GW-coin.")
        return

    # Генерация результата
    result = [random.choice(SLOT_EMOJIS) for _ in range(3)]

    # Отправка начального сообщения
    sent_msg = bot.send_message(message.chat.id, "🎰 🔄 | 🔄 | 🔄")

    # Анимация
    animate_slot_roll(message.chat.id, sent_msg.message_id, result)

    # Определение выигрыша
    win = calculate_win(result, bet)
    old_balance = user_balance
    user_balance = user_balance - bet + win
    user["balance"] = user_balance
    set_user(user_id, user)

    # Отправка результата
    show_result(message.chat.id, result, bet, win, user_balance)

def calculate_win(result, bet):
    if result[0] == result[1] == result[2]:
        return bet * (10 if result[0] == "7️⃣" else 3)
    elif result[0] == result[1] or result[1] == result[2] or result[0] == result[2]:
        return bet * 2
    return 0

def show_result(chat_id, result, bet, win, balance):
    if result[0] == result[1] == result[2]:
        if result[0] == "7️⃣":
            msg = f"🎉 ДЖЕКПОТ! 7️⃣7️⃣7️⃣ +{win} GW-coin!"
        else:
            msg = f"🎉 Три {result[0]}! Вы выиграли {win} GW-coin!"
    elif result[0] == result[1] or result[1] == result[2] or result[0] == result[2]:
        msg = f"🎊 Две одинаковых! Выигрыш {win} GW-coin!"
    else:
        msg = "😢 Вы проиграли."
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔁 Ещё раз", callback_data="game_slots"))
    markup.add(types.InlineKeyboardButton("🏠 Уйти", callback_data="leave_slots"))
    
    bot.send_message(
        chat_id,
        f"{msg}\n\n💰 Ваш баланс: {balance} GW-coin",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == "game_slots")
def slots_handler_inline(call):
    bot.answer_callback_query(call.id)
    send_slots_bet_inline_keyboard(call.message.chat.id, call.from_user.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("slot_bet_"))
def process_slots_bet_inline(call):
    bot.answer_callback_query(call.id)
    if call.data == "slot_bet_custom":
        msg = bot.send_message(call.message.chat.id, f"💡 Введите свою ставку (минимум {MIN_BET}, максимум {MAX_BET} GW-coin):")
        bot.register_next_step_handler(msg, process_slots_bet_custom_input)
    else:
        bet = int(call.data.replace("slot_bet_", ""))
        process_slots_bet_execute(call.message, bet)

def process_slots_bet_custom_input(message):
    try:
        bet = int(message.text)
        process_slots_bet_execute(message, bet)
    except (ValueError, TypeError):
        bot.send_message(message.chat.id, f"🚫 Некорректная ставка. Введите число не менее {MIN_BET} и не более {MAX_BET}.")

@bot.callback_query_handler(func=lambda call: call.data == "leave_slots")
def leave_slots_handler(call):
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "Спасибо за игру! Возвращайтесь!")

# ========КОСТИ=======
MIN_BET = 10
MAX_BET = 500

# --- Кости с выбором ставки через кнопки и финальными кнопками ---
def send_dice_bet_inline_keyboard(chat_id, user_id):
    user = get_user(user_id)  # Теперь используем переданный user_id
    user_name = user.get("name", "Игрок")
    user_balance = user["balance"]
    markup = types.InlineKeyboardMarkup(row_width=2)
    for bet, emoji in POPULAR_BETS:
        markup.add(types.InlineKeyboardButton(f"{emoji} {bet}", callback_data=f"dice_bet_{bet}"))
    markup.add(types.InlineKeyboardButton("🎲 Другая сумма", callback_data="dice_bet_custom"))
    markup.add(types.InlineKeyboardButton("🏠 Уйти", callback_data="back_main"))
    bot.send_message(
        chat_id,
        f"🎰 {user_name}, ваш баланс: {user_balance} GW-coin\n\n"
        f"Выберите ставку (минимум {MIN_BET}, максимум {MAX_BET} GW-coin):",
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data == "game_dice")
@ban_guard_callback
def dice_handler_inline(call):
    bot.answer_callback_query(call.id)
    send_dice_bet_inline_keyboard(call.message.chat.id, call.from_user.id)  # Добавляем user_id

@bot.callback_query_handler(func=lambda call: call.data.startswith("dice_bet_"))
def process_dice_bet_inline(call):
    bot.answer_callback_query(call.id)
    if call.data == "dice_bet_custom":
        msg = bot.send_message(call.message.chat.id, f"💡 Введите свою ставку (минимум {MIN_BET}, максимум {MAX_BET} GW-coin):")
        bot.register_next_step_handler(msg, process_dice_bet_custom_input)
    else:
        bet = int(call.data.replace("dice_bet_", ""))
        process_dice_bet_execute(call.message, bet)

def process_dice_bet_custom_input(message):
    try:
        bet = int(message.text)
        process_dice_bet_execute(message, bet)
    except (ValueError, TypeError):
        bot.send_message(message.chat.id, f"🚫 Некорректная ставка. Введите число не менее {MIN_BET} и не более {MAX_BET}.")

def process_dice_bet_execute(message, bet):
    user_id = message.from_user.id
    user = get_user(user_id)
    old_balance = user["balance"] 
    if bet > user["balance"]:
        bot.send_message(message.chat.id, "У вас недостаточно GW-coin!")
        send_dice_bet_inline_keyboard(message.chat.id, user_id)  # Добавлен user_id
        return
    if bet > MAX_BET:
        bot.send_message(message.chat.id, f"🚫 Максимальная ставка — {MAX_BET} GW-coin.")
        send_dice_bet_inline_keyboard(message.chat.id, user_id)  # Добавлен user_id
        return
    if bet < MIN_BET:
        bot.send_message(message.chat.id, f"🚫 Минимальная ставка — {MIN_BET} GW-coin.")
        send_dice_bet_inline_keyboard(message.chat.id, user_id)  # Добавлен user_id
        return
    
    user["balance"] -= bet
    set_user(user_id, user)

    bot.send_message(message.chat.id, "🎲 Вы бросаете кости...")
    player_dice_msg = bot.send_dice(message.chat.id, emoji="🎲")
    import time
    time.sleep(3)
    player_value = player_dice_msg.dice.value

    bot.send_message(message.chat.id, "🤖 Теперь бот бросает кости...")
    bot_dice_msg = bot.send_dice(message.chat.id, emoji="🎲")
    time.sleep(3)
    bot_value = bot_dice_msg.dice.value

    if player_value > bot_value:
        win = bet * 2
        result = f"🏆 Вы победили! +{win} GW-coin."
    elif player_value < bot_value:
        win = 0
        result = f"😢 Вы проиграли! -{bet} GW-coin."
    else:
        win = bet
        result = "🤝 Ничья! Ставка возвращается."

    old_balance = user["balance"]
    user["balance"] += win_amount
    set_user(user_id, user)
    
    if win > 0:
        increment_profile_field(user_id, "wins")
        log_user_win(user_id, "Кости", win)
    set_user(user_id, user)
    log_gwcoin(user, None, user_balance, user["balance"], bet if win > bet else -bet, "Кости")


    markup = types.InlineKeyboardMarkup()
    for bet_value, emoji in POPULAR_BETS:
        markup.add(types.InlineKeyboardButton(f"{emoji} {bet_value}", callback_data=f"dice_bet_{bet_value}"))
    markup.add(types.InlineKeyboardButton("🎲 Другая сумма", callback_data="dice_bet_custom"))
    markup.add(types.InlineKeyboardButton("🏠 Уйти", callback_data="back_main"))

    bot.send_message(
        message.chat.id,
        f"<b>Результат:</b>\n"
        f"Вы: {player_value}\n"
        f"Бот: {bot_value}\n"
        f"{result}\n"
        f"Ваш баланс: {user['balance']} GW-coin",
        parse_mode="HTML",
        reply_markup=markup
    )

# ========== БЛЭКДЖЕК ==========
import random
from telebot import types

# Карты: (название, номинал)
BJ_SUITS = ["♠️", "♥️", "♦️", "♣️"]
BJ_CARDS = [
    ("A", [1, 11]),
    ("2", [2]),
    ("3", [3]),
    ("4", [4]),
    ("5", [5]),
    ("6", [6]),
    ("7", [7]),
    ("8", [8]),
    ("9", [9]),
    ("10", [10]),
    ("J", [10]),
    ("Q", [10]),
    ("K", [10]),
]

MIN_BET = 10
MAX_BET = 500

def bj_new_deck():
    deck = []
    for suit in BJ_SUITS:
        for name, value in BJ_CARDS:
            deck.append((name, suit, value))
    random.shuffle(deck)
    return deck

def bj_hand_str(hand):
    # hand: [(name, suit, [v])]
    return ", ".join(
        f"{name}{suit} ({'/'.join(map(str, value))})"
        for (name, suit, value) in hand
    )

def bj_hand_value(hand):
    # Возвращает лучший подсчёт по правилам блэкджека
    values = [0]
    for name, suit, vlist in hand:
        new_values = []
        for v in vlist:
            for acc in values:
                new_values.append(acc + v)
        values = new_values
    # Все суммы <= 21
    valid = [v for v in set(values) if v <= 21]
    if valid:
        return max(valid)
    return min(set(values))

def bj_init_game(user_id, bet):
    deck = bj_new_deck()
    user_hand = [deck.pop(), deck.pop()]
    bot_hand = [deck.pop(), deck.pop()]
    # Сохраняем всё в память (можно в базу, тут простой пример)
    BLACKJACK_SESSION[user_id] = {
        "deck": deck,
        "user": user_hand,
        "bot": bot_hand,
        "bet": bet,
        "state": "user_turn"
    }

def bj_get_session(user_id):
    return BLACKJACK_SESSION.get(user_id)

def bj_remove_session(user_id):
    if user_id in BLACKJACK_SESSION:
        del BLACKJACK_SESSION[user_id]

# Глобальное хранилище сессий (лучше использовать redis/db)
BLACKJACK_SESSION = {}

@bot.callback_query_handler(func=lambda call: call.data == "game_blackjack")
def start_blackjack(call):
    bot.answer_callback_query(call.id)
    # Показываем выбор ставки через звёздочки (как в слотах)
    markup = types.InlineKeyboardMarkup(row_width=2)
    for bet, emoji in POPULAR_BETS:
        markup.add(types.InlineKeyboardButton(f"{emoji} {bet}", callback_data=f"bj_bet_{bet}"))
    markup.add(types.InlineKeyboardButton("🎲 Другая сумма", callback_data="bj_bet_custom"))
    markup.add(types.InlineKeyboardButton("🏠 Уйти", callback_data="back_main"))
    bot.send_message(
        call.message.chat.id,
        f"Выберите ставку для Блэкджека (минимум {MIN_BET}, максимум {MAX_BET} GW-coin):",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("bj_bet_"))
def process_bj_bet_inline(call):
    bot.answer_callback_query(call.id)
    user = get_user(call.from_user.id)
    if call.data == "bj_bet_custom":
        msg = bot.send_message(call.message.chat.id, f"💡 Введите свою ставку (минимум {MIN_BET}, максимум {MAX_BET} GW-coin):")
        bot.register_next_step_handler(msg, bj_bet_custom_input)
        return
    bet = int(call.data.replace("bj_bet_", ""))
    if bet < MIN_BET or bet > MAX_BET:
        bot.send_message(call.message.chat.id, f"🚫 Ставка должна быть не менее {MIN_BET} и не более {MAX_BET} GW-coin.")
        return
    if bet > user["balance"]:
        bot.send_message(call.message.chat.id, "Ставка некорректна или не хватает средств.")
        return
    bj_init_game(call.from_user.id, bet)
    bj_show_user_hand(call.message.chat.id, call.from_user.id)

def bj_bet_custom_input(message):
    user = get_user(message.from_user.id)
    try:
        bet = int(message.text)
        if bet < MIN_BET or bet > MAX_BET or bet > user["balance"]:
            raise ValueError
        bj_init_game(message.from_user.id, bet)
        bj_show_user_hand(message.chat.id, message.from_user.id)
    except Exception:
        bot.send_message(
            message.chat.id,
            f"🚫 Некорректная ставка. Введите число не менее {MIN_BET} и не более {MAX_BET}, и не больше вашего баланса."
        )

def bj_show_user_hand(chat_id, user_id):
    session = bj_get_session(user_id)
    if not session:
        bot.send_message(chat_id, "Сессия не найдена.")
        return
    user_hand = session["user"]
    value = bj_hand_value(user_hand)
    hand_str = bj_hand_str(user_hand)
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("➕ Ещё", callback_data="bj_hit"))
    markup.add(types.InlineKeyboardButton("🛑 Хватит", callback_data="bj_stand"))
    bot.send_message(
        chat_id,
        f"🃏 Ваши карты: {hand_str}\nСумма: {value}\n\n➕ — взять карту\n🛑 — остановиться",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data in ["bj_hit", "bj_stand"])
def bj_user_action(call):
    session = bj_get_session(call.from_user.id)
    if not session or session["state"] != "user_turn":
        bot.answer_callback_query(call.id, "Нет активной игры!")
        return

    if call.data == "bj_hit":
        session["user"].append(session["deck"].pop())
        value = bj_hand_value(session["user"])
        if value > 21:
            bj_finish_game(call.message.chat.id, call.from_user.id, "user_bust")
        else:
            bj_show_user_hand(call.message.chat.id, call.from_user.id)
    elif call.data == "bj_stand":
        session["state"] = "bot_turn"
        bj_bot_turn(call.message.chat.id, call.from_user.id)

def bj_bot_turn(chat_id, user_id):
    session = bj_get_session(user_id)
    bot_hand = session["bot"]
    while bj_hand_value(bot_hand) < 17:
        bot_hand.append(session["deck"].pop())
    bj_finish_game(chat_id, user_id, "resolve")

def bj_finish_game(chat_id, user_id, reason):
    session = bj_get_session(user_id)
    user_hand = session["user"]
    bot_hand = session["bot"]
    user_val = bj_hand_value(user_hand)
    bot_val = bj_hand_value(bot_hand)
    bet = session["bet"]
    user = get_user(user_id)
    old_balance = user["balance"]

    # Итоговый текст
    user_str = bj_hand_str(user_hand)
    bot_str = bj_hand_str(bot_hand)
    result = ""
    win = 0

    if reason == "user_bust":
        result = f"😢 Перебор! Вы проиграли {bet} GW-coin."
        win = -bet
    elif user_val > 21:
        result = f"😢 Перебор! Вы проиграли {bet} GW-coin."
        win = -bet
    elif bot_val > 21:
        result = f"🏆 Бот перебрал! Вы выиграли {int(bet * 1.8)} GW-coin!"
        win = int(bet * 1.8)
    elif user_val > bot_val:
        result = f"🏆 Вы выиграли! +{int(bet * 1.8)} GW-coin."
        win = int(bet * 1.8)
    elif user_val == bot_val:
        result = "🤝 Ничья! Ставка возвращается."
        win = 0
    else:
        result = f"😢 Вы проиграли {bet} GW-coin."
        win = -bet

    user["balance"] += win
    set_user(user_id, user)
    # Логирование и прочее при необходимости

    markup = types.InlineKeyboardMarkup()
    for bet_value, emoji in POPULAR_BETS:
        markup.add(types.InlineKeyboardButton(f"{emoji} {bet_value}", callback_data=f"bj_bet_{bet_value}"))
    markup.add(types.InlineKeyboardButton("🎲 Другая сумма", callback_data="bj_bet_custom"))
    markup.add(types.InlineKeyboardButton("🏠 Уйти", callback_data="back_main"))

    bot.send_message(
        chat_id,
        f"<b>Ваши карты:</b> {user_str}\n"
        f"<b>Сумма:</b> {user_val}\n\n"
        f"<b>Карты бота:</b> {bot_str}\n"
        f"<b>Сумма бота:</b> {bot_val}\n\n"
        f"{result}\n"
        f"Ваш баланс: {user['balance']} GW-coin",
        parse_mode="HTML",
        reply_markup=markup
    )
    bj_remove_session(user_id)


# ========== РУЛЕТКА ==========
import random
from telebot import types

ROULETTE_COLORS = {
    0: "green",
    1: "red", 2: "black", 3: "red", 4: "black", 5: "red", 6: "black",
    7: "red", 8: "black", 9: "red", 10: "black", 11: "black", 12: "red",
    13: "black", 14: "red", 15: "black", 16: "red", 17: "black", 18: "red",
    19: "red", 20: "black", 21: "red", 22: "black", 23: "red", 24: "black",
    25: "red", 26: "black", 27: "red", 28: "black", 29: "black", 30: "red",
    31: "black", 32: "red", 33: "black", 34: "red", 35: "black", 36: "red"
}

ROULETTE_EMOJIS = {
    "red": "🟥", "black": "⬛️", "green": "🟩"
}

ROULETTE_BET_TYPES = [
    ("color_red", "🟥 Красное"),
    ("color_black", "⬛️ Чёрное"),
    ("even", "🔵 Чётное"),
    ("odd", "🟠 Нечётное"),
    ("number", "🔢 На число (1-36)"),
    ("zero", "🟩 Зеро (0)"),
]

POPULAR_BETS = [(10, "⭐️"), (50, "⭐"), (100, "⭐"), (500, "⭐")]
MIN_BET = 10
MAX_BET = 500

ROULETTE_SESSION = {}

def roulette_spin_animation(bot, chat_id, msg_id, steps=8):
    """Анимация вращения рулетки с постепенным замедлением."""
    for i in range(steps):
        num = random.randint(0, 36)
        color = ROULETTE_COLORS[num]
        emoji = ROULETTE_EMOJIS[color]
        dots = ''.join(['• ' for _ in range(steps - i)])
        try:
            bot.edit_message_text(
                f"🎡 Крутим рулетку...\n{dots}\nВыпадает: <b>{num} {emoji}</b>",
                chat_id,
                msg_id,
                parse_mode="HTML"
            )
        except Exception:
            pass
        import time
        time.sleep(0.15 + i * 0.09)  # ускорение анимации

def send_roulette_bet_type_keyboard(chat_id, user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    for key, name in ROULETTE_BET_TYPES:
        markup.add(types.InlineKeyboardButton(name, callback_data=f"roulette_type_{key}"))
    markup.add(types.InlineKeyboardButton("🎰 Завершить и крутить!", callback_data="roulette_finish_bets"))
    bot.send_message(chat_id, "Выберите тип ставки или завершите выбор:", reply_markup=markup)
    show_current_bets(chat_id, user_id)

def show_current_bets(chat_id, user_id):
    session = ROULETTE_SESSION.get(user_id)
    if not session or not session.get("bets"):
        return
    bets_text = "\n".join([format_bet(bet) for bet in session["bets"]])
    bot.send_message(chat_id, f"Ваши текущие ставки:\n{bets_text}")

def format_bet(bet):
    if bet["type"] == "color_red":
        return f"🟥 Красное — {bet['amount']} GW-coin"
    if bet["type"] == "color_black":
        return f"⬛️ Чёрное — {bet['amount']} GW-coin"
    if bet["type"] == "even":
        return f"🔵 Чётное — {bet['amount']} GW-coin"
    if bet["type"] == "odd":
        return f"🟠 Нечётное — {bet['amount']} GW-coin"
    if bet["type"] == "zero":
        return f"🟩 Зеро (0) — {bet['amount']} GW-coin"
    if bet["type"] == "number":
        return f"🔢 Число {bet['number']} — {bet['amount']} GW-coin"
    return "Ставка"

@bot.callback_query_handler(func=lambda call: call.data == "game_roulette")
def roulette_start(call):
    user_id = call.from_user.id
    bot.answer_callback_query(call.id)
    user = get_user(user_id)
    ROULETTE_SESSION[user_id] = {"bets": [], "balance_left": user["balance"]}
    send_roulette_bet_type_keyboard(call.message.chat.id, user_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("roulette_type_"))
def roulette_choose_type(call):
    user_id = call.from_user.id
    session = ROULETTE_SESSION.get(user_id)
    if not session:
        bot.send_message(call.message.chat.id, "Сессия не найдена, перезапустите игру.")
        return
    bet_type = call.data.replace("roulette_type_", "")
    session["pending_type"] = bet_type
    # Теперь спросим сумму
    markup = types.InlineKeyboardMarkup(row_width=2)
    for bet, emoji in POPULAR_BETS:
        markup.add(types.InlineKeyboardButton(f"{emoji} {bet}", callback_data=f"roulette_amt_{bet}"))
    markup.add(types.InlineKeyboardButton("🎲 Другая сумма", callback_data="roulette_amt_custom"))
    bot.send_message(call.message.chat.id, f"Выберите сумму для этой ставки (от {MIN_BET} до {MAX_BET} GW-coin):", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("roulette_amt_"))
def roulette_choose_amount(call):
    user_id = call.from_user.id
    session = ROULETTE_SESSION.get(user_id)
    if not session or "pending_type" not in session:
        bot.send_message(call.message.chat.id, "Ошибка сессии.")
        return
    if call.data == "roulette_amt_custom":
        msg = bot.send_message(call.message.chat.id, f"Введите свою сумму (от {MIN_BET} до {MAX_BET}):")
        bot.register_next_step_handler(msg, lambda m: roulette_set_amount_custom(m, user_id))
        return
    amount = int(call.data.replace("roulette_amt_", ""))
    roulette_add_bet(call.message, user_id, amount)

def roulette_set_amount_custom(message, user_id):
    try:
        amount = int(message.text)
        if amount < MIN_BET or amount > MAX_BET:
            raise ValueError
        roulette_add_bet(message, user_id, amount)
    except Exception:
        bot.send_message(message.chat.id, f"Ошибка! Введите число от {MIN_BET} до {MAX_BET}.")

def roulette_add_bet(message, user_id, amount):
    user = get_user(user_id)
    session = ROULETTE_SESSION.get(user_id)
    if not session or "pending_type" not in session:
        bot.send_message(message.chat.id, "Ошибка сессии.")
        return
    if amount > session["balance_left"]:
        bot.send_message(message.chat.id, "❗️ Недостаточно средств для этой ставки.")
        send_roulette_bet_type_keyboard(message.chat.id, user_id)
        return
    if amount < MIN_BET or amount > MAX_BET:
        bot.send_message(message.chat.id, f"🚫 Ставка должна быть от {MIN_BET} до {MAX_BET} GW-coin.")
        send_roulette_bet_type_keyboard(message.chat.id, user_id)
        return

    bet_type = session.pop("pending_type")
    bet = {"type": bet_type, "amount": amount}
    if bet_type == "number":
        msg = bot.send_message(message.chat.id, "Введите число от 1 до 36:")
        bot.register_next_step_handler(msg, lambda m: roulette_set_number(m, user_id, amount))
        # Не добавляем пока число не указано
        return
    session["bets"].append(bet)
    session["balance_left"] -= amount
    send_roulette_bet_type_keyboard(message.chat.id, user_id)

def roulette_set_number(message, user_id, amount):
    try:
        number = int(message.text)
        if number < 1 or number > 36:
            raise ValueError
        session = ROULETTE_SESSION.get(user_id)
        bet = {"type": "number", "amount": amount, "number": number}
        session["bets"].append(bet)
        session["balance_left"] -= amount
        send_roulette_bet_type_keyboard(message.chat.id, user_id)
    except Exception:
        bot.send_message(message.chat.id, "Нужно целое число от 1 до 36!")

@bot.callback_query_handler(func=lambda call: call.data == "roulette_finish_bets")
def roulette_finish_bets(call):
    user_id = call.from_user.id
    session = ROULETTE_SESSION.get(user_id)
    if not session or not session["bets"]:
        bot.send_message(call.message.chat.id, "Ставок нет. Сделайте хотя бы одну!")
        return
    user = get_user(user_id)

    # 1. Отправляешь первое сообщение ("крутим рулетку...")
    sent = bot.send_message(call.message.chat.id, "🎡 Крутим рулетку...")
    # 2. Анимация
    roulette_spin_animation(bot, call.message.chat.id, sent.message_id)
    # 3. Теперь определяем реальный результат
    spin_number = random.randint(0, 36)
    spin_color = ROULETTE_COLORS[spin_number]
    color_emoji = ROULETTE_EMOJIS[spin_color]
    total_win = 0
    results = []
    for bet in session["bets"]:
        result, win = check_roulette_bet(bet, spin_number, spin_color)
        results.append(result)
        total_win += win

    user["balance"] += total_win - sum(b["amount"] for b in session["bets"])
    set_user(user_id, user)

    msg = (f"🎡 <b>Рулетка!</b>\n"
           f"Выпало: <b>{spin_number} {color_emoji}</b> ({spin_color})\n\n"
           + "\n".join(results) +
           f"\n\nВаш баланс: <b>{user['balance']} GW-coin</b>")

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔁 Новая игра", callback_data="game_roulette"))
    markup.add(types.InlineKeyboardButton("🏠 Уйти", callback_data="back_main"))
    bot.edit_message_text(msg, call.message.chat.id, sent.message_id, parse_mode="HTML", reply_markup=markup)
    ROULETTE_SESSION.pop(user_id, None)

def check_roulette_bet(bet, spin_number, spin_color):
    win = 0
    if bet["type"] == "color_red":
        if spin_color == "red":
            win = bet["amount"] * 2
            return f"🟥 Красное: +{win} GW-coin", win
        else:
            return f"🟥 Красное: проигрыш", 0
    if bet["type"] == "color_black":
        if spin_color == "black":
            win = bet["amount"] * 2
            return f"⬛️ Чёрное: +{win} GW-coin", win
        else:
            return f"⬛️ Чёрное: проигрыш", 0
    if bet["type"] == "even":
        if spin_number != 0 and spin_number % 2 == 0:
            win = bet["amount"] * 2
            return f"🔵 Чётное: +{win} GW-coin", win
        else:
            return f"🔵 Чётное: проигрыш", 0
    if bet["type"] == "odd":
        if spin_number % 2 == 1:
            win = bet["amount"] * 2
            return f"🟠 Нечётное: +{win} GW-coin", win
        else:
            return f"🟠 Нечётное: проигрыш", 0
    if bet["type"] == "zero":
        if spin_number == 0:
            win = bet["amount"] * 36
            return f"🟩 Зеро (0): +{win} GW-coin", win
        else:
            return f"🟩 Зеро (0): проигрыш", 0
    if bet["type"] == "number":
        if spin_number == bet["number"]:
            win = bet["amount"] * 36
            return f"🔢 Число {bet['number']}: +{win} GW-coin", win
        else:
            return f"🔢 Число {bet['number']}: проигрыш", 0
    return "Неизвестная ставка", 0


# ========== ОБМЕН ==========
@bot.message_handler(func=lambda m: m.text == "💱 Обмен")
def exchange_menu(message):
    markup = types.InlineKeyboardMarkup()
    user = get_user(message.from_user.id)
    codes = all_dp_codes_left()
    for nominal in sorted(codes, key=lambda x: int(x)):
        dp = int(nominal)
        left = len(codes[nominal])
        need_gw = dp * 100
        if left > 0 and user['balance'] >= need_gw:
            markup.add(types.InlineKeyboardButton(f"{dp} DP ({left} кодов)", callback_data=f"try_exchange_{dp}"))
    markup.add(types.InlineKeyboardButton("Назад", callback_data="back_main"))
    bot.send_message(message.chat.id, "Выберите сколько DP вы хотите получить:", reply_markup=markup)

@bot.callback_query_handler(lambda call: call.data.startswith("try_exchange_"))
@ban_guard_callback
def confirm_exchange_menu(call):
    dp = int(call.data.split("_")[-1])
    gw = dp * 100
    user = get_user(call.from_user.id)
    if user["balance"] < gw:
        bot.answer_callback_query(call.id, "Недостаточно GW-coin.", show_alert=True)
        send_reply_main_menu(call.message.chat.id)
        return
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm_exchange_{dp}_{gw}"),
        types.InlineKeyboardButton("❌ Отмена", callback_data="exchange")
    )
    bot.send_message(call.message.chat.id, f"Обменять {gw} GW-coin на {dp} DP?", reply_markup=markup)

@bot.callback_query_handler(lambda call: call.data.startswith("confirm_exchange_"))
@ban_guard_callback
def process_exchange_confirm(call):
    try:
        parts = call.data.split("_")
        dp = int(parts[2])
        gw = int(parts[3])
    except Exception as e:
        bot.answer_callback_query(call.id, f"Ошибка обмена: {e}", show_alert=True)
        send_reply_main_menu(call.message.chat.id)
        return

    user = get_user(call.from_user.id)
    if user["balance"] < gw:
        bot.answer_callback_query(call.id, "Недостаточно GW-coin.", show_alert=True)
        send_reply_main_menu(call.message.chat.id)
        return
    code = get_dp_code(dp, user)
    if not code:
        bot.answer_callback_query(call.id, f"Коды номиналом {dp} DP закончились.", show_alert=True)
        send_reply_main_menu(call.message.chat.id)
        return
    old = user["balance"]
    user["balance"] -= gw
    user["exchanges"] = user.get("exchanges", 0) + 1
    set_user(call.from_user.id, user)
    log_gwcoin(user, None, old, user["balance"], -gw, f"Обмен на {dp} DP")
    log_dp(user, f"Обменял {gw} GW-coin на DP-код {code} ({dp} DP)")
    log_user_exchange(user.get("username", ""), gw, dp, code)
    bot.answer_callback_query(call.id, "Успешно!", show_alert=True)
    bot.send_message(
    call.message.chat.id,
    (
        f"Ваш DP-код на {dp} DP: `{code}`\n\n"
        f"Введите этот код на сайте в разделе <a href=\"https://classic.rp-gameworld.ru/donate/\">донат</a>, чтобы получить DP.\n"
        f"Ваш новый баланс: {user['balance']} GW-coin"
    ),
    parse_mode="HTML"
)
    send_reply_main_menu(call.message.chat.id)

# ========== БОНУС ==========
@bot.message_handler(func=lambda m: m.text == "🎁 Бонус")
def bonus_menu(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Получить бонус", callback_data="bonus"))
    markup.add(types.InlineKeyboardButton("Назад", callback_data="back_main"))
    bot.send_message(message.chat.id, "Бонус:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "bonus")
@ban_guard_callback
def bonus_handler_inline(call):
    user = get_user(call.from_user.id)
    now = datetime.utcnow()
    try:
        last = datetime.strptime(user.get("last_bonus", ""), "%Y-%m-%d")
    except Exception:
        last = datetime(1970,1,1)
    if user["balance"] >= BONUS_THRESHOLD:
        bot.answer_callback_query(call.id, f"Бонус доступен только если у вас меньше {BONUS_THRESHOLD} GW-coin!", show_alert=True)
        send_reply_main_menu(call.message.chat.id)
        return
    if last.date() == now.date():
        bot.answer_callback_query(call.id, "Бонус уже получен сегодня! Попробуйте завтра.", show_alert=True)
        send_reply_main_menu(call.message.chat.id)
        return
    old = user["balance"]
    user["balance"] += BONUS_AMOUNT
    user["last_bonus"] = now.strftime("%Y-%m-%d")
    user["bonuses"] = user.get("bonuses", 0) + 1
    set_user(call.from_user.id, user)
    log_bonus(user, BONUS_AMOUNT)
    bot.answer_callback_query(call.id, f"Вам начислен бонус: +{BONUS_AMOUNT} GW-coin!\nТеперь у вас: {user['balance']} GW-coin.", show_alert=True)
    send_reply_main_menu(call.message.chat.id)

# ========== БАЛАНС ==========
@bot.message_handler(func=lambda m: m.text == "🏆 Баланс")
def balance_menu(message):
    user = get_user(message.from_user.id)
    bot.send_message(message.chat.id, f"Ваш баланс: {user['balance']} GW-coin")
    send_reply_main_menu(message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data == "balance")
def balance_handler(call):
    user = get_user(call.from_user.id)
    text = f"Ваш баланс: {user['balance']} GW-coin."
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, text)
    send_reply_main_menu(call.message.chat.id)

# ========== ТОП ==========
@bot.message_handler(func=lambda m: m.text == "🏅 ТОП")
def top_menu(message):
    top = get_top_users()
    text = "🏅 Топ игроков:\n"
    for i, (user_id, info) in enumerate(top, 1):
        name = info.get("username") or f"ID:{user_id}"
        text += f"{i}. {name}: {info['balance']} GW-coin\n"
    bot.send_message(message.chat.id, text)
    send_reply_main_menu(message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data == "top")
@ban_guard_callback
def top_handler(call):
    top = get_top_users()
    text = "🏅 Топ игроков:\n"
    for i, (user_id, info) in enumerate(top, 1):
        name = info.get("username") or f"ID:{user_id}"
        text += f"{i}. {name}: {info['balance']} GW-coin\n"
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, text)
    send_reply_main_menu(call.message.chat.id)

# ======ПОКУПКА GW-COINS=====

@bot.message_handler(func=lambda m: m.text == "💸 Пополнить баланс")
def topup_request(message):
    user = get_user(message.from_user.id)
    username = user.get("username") or f"id{message.from_user.id}"

    # Проверка: если заявка от этого пользователя уже есть и не закрыта — не добавляем дубликат!
    for req in PENDING_TOPUPS:
        if req['user_id'] == message.from_user.id and req['taken_by'] is None:
            bot.send_message(message.chat.id, "Ваша заявка уже на рассмотрении. Ожидайте ответа администратора.")
            return

    bot.send_message(message.chat.id, "✅ Ваша заявка на пополнение отправлена администратору. Ожидайте инструкцию!")
    PENDING_TOPUPS.append({
        'user_id': message.from_user.id,
        'username': username,
        'taken_by': None,
        'time': datetime.utcnow()
    })
    send_topup_notifications()  # Не забудь реализовать эту функцию для уведомлений админов

@bot.message_handler(func=lambda m: m.text == "💸 Пополнить баланс GW-coins")
def topup_info(message):
    text = (
        "<b>GW-coins — внутриигровая валюта</b>\n\n"
        "Валюта GW-coins, используемая в данном проекте, является исключительно виртуальной и не имеет никакого отношения к реальным денежным средствам. "
        "Любое совпадение наименования и обозначения с реальными валютами является случайным.\n\n"
        "Нажимая кнопку «Пополнить баланс GW-coins», вы не осуществляете покупку реального товара или услуги. "
        "Все средства, списываемые с вашего личного счета, существуют только в рамках игрового процесса и не подлежат обмену на реальные деньги.\n\n"
        "Получение GW-coins возможно несколькими способами:\n"
        "— За участие в игровых активностях и конкурсах внутри проекта.\n"
        "— Путём добровольного пожертвования денежных средств на развитие проекта с последующим начислением GW-coins на ваш игровой баланс.\n\n"
        "Пожертвования являются добровольными и используются исключительно для поддержки и развития данного проекта.\n\n"
        "Если у вас остались вопросы, вы можете узнать подробнее у администрации проекта RPGW."
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✅ Подтвердить заявку", callback_data="topup_confirm"))
    markup.add(types.InlineKeyboardButton("❌ Отмена", callback_data="back_main"))
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")    

@bot.callback_query_handler(lambda call: call.data == "topup_confirm")
@ban_guard_callback
def topup_confirmed(call):
    user = get_user(call.from_user.id)
    username = user.get("username") or f"id{call.from_user.id}"
    bot.send_message(call.message.chat.id, "✅ Ваша заявка на пополнение отправлена администратору. Ожидайте инструкцию!")
    # Добавить заявку в очередь (как в реализации выше)
    from datetime import datetime
    PENDING_TOPUPS.append({
        'user_id': call.from_user.id,
        'username': username,
        'taken_by': None,
        'time': datetime.utcnow()
    })
    send_topup_notifications()  # реализуй или используй уже реализованную функцию для уведомлений админам

@bot.callback_query_handler(lambda call: call.data == "back_main")
def back_to_main(call):
    send_reply_main_menu(call.message.chat.id)

# ========== ПРОФИЛЬ ==========
@bot.message_handler(func=lambda m: m.text == "📜 Профиль")
def profile_menu(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Показать профиль", callback_data="profile"))
    markup.add(types.InlineKeyboardButton("Мои транзакции", callback_data="transactions"))
    markup.add(types.InlineKeyboardButton("Назад", callback_data="back_main"))
    bot.send_message(message.chat.id, "Профиль:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "profile")
def profile_handler(call):
    user = get_user(call.from_user.id)
    ensure_profile_fields(user)
    text = (
        f"👤 Профиль @{user['username']}\n"
        f"Баланс: {user['balance']} GW-coin\n"
        f"Обменов: {user.get('exchanges', 0)}\n"
        f"Выигрышей: {user.get('wins', 0)}\n"
        f"Бонусов: {user.get('bonuses', 0)}\n"
        f"Дата регистрации: {user.get('registered', 'N/A')}\n"
    )
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, text)
    send_reply_main_menu(call.message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data == "transactions")
def transactions_handler(call):
    logs = get_user_logs(call.from_user.id, count=20)
    text = "🧾 Ваши последние операции:\n" + "\n".join(logs) if logs else "Нет операций."
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, text)
    send_reply_main_menu(call.message.chat.id)



# ========== АДМИН-ПАНЕЛЬ ==========
@bot.message_handler(func=lambda m: m.text == "⚙️ Админ-панель")
def admin_menu(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "Нет доступа.")
        return
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Открыть админ-панель", callback_data="admin_panel"))
    markup.add(types.InlineKeyboardButton("Назад", callback_data="back_main"))
    bot.send_message(message.chat.id, "Админ-панель:", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data == "admin_topup_requests")
def admin_topup_requests_menu(call):
    if not is_admin(call.from_user.id):
        bot.send_message(call.message.chat.id, "Нет доступа.")
        return
    if not PENDING_TOPUPS:
        bot.send_message(call.message.chat.id, "Нет активных заявок на пополнение.")
        return

    for req in PENDING_TOPUPS:
        status = f"В работе у @{req['taken_by']}" if req['taken_by'] else "Ожидает исполнителя"
        text = (
            f"💳 Заявка:\n"
            f"Пользователь: @{req['username']} (ID: {req['user_id']})\n"
            f"Статус: {status}\n"
            f"Время заявки: {req['time'].strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        markup = types.InlineKeyboardMarkup()
        if not req['taken_by']:
            markup.add(types.InlineKeyboardButton("Взять в работу", callback_data=f"take_topup_{req['user_id']}"))
        bot.send_message(call.message.chat.id, text, reply_markup=markup)


# ======БАНЫ=======

def is_banned(user_id):
    user = get_user(user_id)
    username = user.get("username", "").lower()
    ban = USER_BANS.get(username)
    if ban:
        if datetime.utcnow() < ban['until']:
            return True
        else:
            del USER_BANS[username]
            save_bans(USER_BANS)
    return False

def unban_log(user_id, by):
    # Добавляет событие разбана в лог
    for log in reversed(BAN_LOG):
        if log['user_id'] == user_id and log['action'] == 'ban' and not log.get('unban_time'):
            log['action'] = 'ban+unban'
            log['unban_by'] = by
            log['unban_time'] = datetime.utcnow()
            break
    save_ban_log(BAN_LOG)


@bot.message_handler(commands=['ban'])
def ban_user(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "Нет доступа.")
        return
    try:
        parts = message.text.split(maxsplit=3)
        user_id = int(parts[1])
        minutes = int(parts[2])
        reason = parts[3] if len(parts) > 3 else "Без указания причины"
    except Exception:
        bot.send_message(message.chat.id, "Пример: /ban 123456789 60 флуд")
        return
    until = datetime.utcnow() + timedelta(minutes=minutes)
    admin = get_user(message.from_user.id, message.from_user)
    user_name = None
    try:
        user_chat = bot.get_chat(user_id)
        user_name = get_user(user_id, user_chat)
    except Exception:
        user_name = f"id{user_id}"
    USER_BANS[user_id] = {
        'until': until,
        'reason': reason,
        'by': admin,
        'time': datetime.utcnow(),
        'username': user_name
    }
    save_bans(USER_BANS)
    BAN_LOG.append({
        'user_id': user_id,
        'username': user_name,
        'by': admin,
        'reason': reason,
        'start': datetime.utcnow(),
        'until': until,
        'action': 'ban',
        'unban_by': None,
        'unban_time': None
    })
    save_ban_log(BAN_LOG)
    bot.send_message(message.chat.id, f"✅ Пользователь {user_id} забанен до {until.strftime('%Y-%m-%d %H:%M:%S')} UTC.\nПричина: {reason}")
    try:
        bot.send_message(user_id, f"⛔ Вы заблокированы до {until.strftime('%Y-%m-%d %H:%M:%S')} UTC.\nПричина: {reason}")
    except Exception:
        pass

@bot.message_handler(commands=['unban'])
def unban_user(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "Нет доступа.")
        return
    try:
        user_id = int(message.text.split()[1])
    except Exception:
        bot.send_message(message.chat.id, "Пример: /unban 123456789")
        return
    if user_id in USER_BANS:
        del USER_BANS[user_id]
        save_bans(USER_BANS)
        unban_log(user_id, get_user(message.from_user.id, message.from_user))
        bot.send_message(message.chat.id, f"Пользователь {user_id} разбанен.")
        try:
            bot.send_message(user_id, "⛔ Ваш бан снят. Теперь вы можете пользоваться ботом.")
        except Exception:
            pass
    else:
        bot.send_message(message.chat.id, "У пользователя нет активного бана.")    

def admin_bans_menu(call):
    text = "<b>Активные баны:</b>\n"
    if not USER_BANS:
        text += "Нет активных банов.\n"
    else:
        for uid, ban in USER_BANS.items():
            text += (
                f"🔻 <b>@{ban['username']}</b> (ID: {uid})\n"
                f"— До: {ban['until'].strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
                f"— Причина: {ban['reason']}\n"
                f"— Кем: @{ban['by']}\n\n"
            )
    text += "\n<b>История банов (последние 10):</b>\n"
    if not BAN_LOG:
        text += "История пуста.\n"
    else:
        for entry in BAN_LOG[-10:][::-1]:
            text += (
                f"🕓 <b>@{entry['username']}</b> (ID: {entry['user_id']})\n"
                f"— С: {entry['start'].strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"— До: {entry['until'].strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"— Причина: {entry['reason']}\n"
                f"— Кем: @{entry['by']}\n"
            )
            if entry.get('unban_time'):
                text += (
                    f"— Разбанен: {entry['unban_time'].strftime('%Y-%m-%d %H:%M:%S')} админом: @{entry['unban_by']}\n"
                )
            text += "\n"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Назад", callback_data="back_main"))
    bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")


@bot.callback_query_handler(func=lambda call: call.data == "admin_bans")
def admin_bans_menu_callback(call):
    if not is_admin(call.from_user.id):
        bot.send_message(call.message.chat.id, "Нет доступа.")
        return
    send_admin_bans_menu(call.message.chat.id)

# --- Забанить пользователя ---
@bot.callback_query_handler(func=lambda call: call.data == "ban_do")
def ban_do_callback(call):
    msg = bot.send_message(call.message.chat.id, "Введите username (@username), время в минутах и причину (через пробел, пример: @vasya 60 флуд):")
    bot.register_next_step_handler(msg, process_ban_input)

def process_ban_input(message):
    try:
        parts = message.text.strip().split(maxsplit=2)
        if len(parts) < 2:
            raise ValueError("Недостаточно аргументов")
        username = parts[0].lstrip("@").lower()
        minutes = int(parts[1])
        reason = parts[2] if len(parts) > 2 else "Без указания причины"

        user_id = find_userid_by_username(username)
        if not user_id:
            bot.send_message(message.chat.id, "Пользователь не найден!")
            send_admin_bans_menu(message.chat.id)
            return

        until = datetime.utcnow() + timedelta(minutes=minutes)
        USER_BANS[username] = {
            'until': until,
            'reason': reason,
            'by': get_user(message.from_user.id).get('username', f"id{message.from_user.id}"),
            'time': datetime.utcnow(),
            'user_id': user_id
        }
        BAN_LOG.append({
            'username': username,
            'user_id': user_id,
            'by': get_user(message.from_user.id).get('username', f"id{message.from_user.id}"),
            'reason': reason,
            'start': datetime.utcnow(),
            'until': until,
            'action': 'ban',
            'unban_by': None,
            'unban_time': None
        })
        save_bans(USER_BANS)
        save_ban_log(BAN_LOG)
        bot.send_message(message.chat.id, f"✅ @{username} забанен до {until.strftime('%Y-%m-%d %H:%M:%S')} UTC. Причина: {reason}")
        try:
            bot.send_message(user_id, f"⛔ Вы заблокированы до {until.strftime('%Y-%m-%d %H:%M:%S')} UTC. Причина: {reason}")
        except Exception:
            pass
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка ввода: {e}\nПример: @vasya 60 флуд")
    send_admin_bans_menu(message.chat.id)

# --- Разбанить пользователя ---
@bot.callback_query_handler(func=lambda call: call.data == "unban_do")
def unban_do_callback(call):
    msg = bot.send_message(call.message.chat.id, "Введите username для разбана (@username):")
    bot.register_next_step_handler(msg, process_unban_input)

def process_unban_input(message):
    username = message.text.strip().lstrip("@").lower()
    user_id = find_userid_by_username(username)
    if username in USER_BANS:
        del USER_BANS[username]
        save_bans(USER_BANS)
        # запись в лог
        for log in reversed(BAN_LOG):
            if log['username'] == username and log['action'] == 'ban' and not log.get('unban_time'):
                log['action'] = 'ban+unban'
                log['unban_by'] = get_user(message.from_user.id).get('username', f"id{message.from_user.id}")
                log['unban_time'] = datetime.utcnow()
                break
        save_ban_log(BAN_LOG)
        bot.send_message(message.chat.id, f"@{username} разбанен.")
        try:
            bot.send_message(user_id, f"✅ Ваш бан снят. Теперь вы можете пользоваться ботом.")
        except Exception:
            pass
    else:
        bot.send_message(message.chat.id, f"У пользователя @{username} нет активного бана.")
    admin_bans_menu_callback(message)

# --- Текущие баны ---
@bot.callback_query_handler(func=lambda call: call.data == "list_bans")
def list_bans_callback(call):
    if not USER_BANS:
        text = "Нет активных банов."
    else:
        text = "<b>Активные баны:</b>\n"
        for uname, ban in USER_BANS.items():
            text += (
                f"🔻 <b>@{uname}</b> (ID: {ban['user_id']})\n"
                f"— До: {ban['until'].strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
                f"— Причина: {ban['reason']}\n"
                f"— Кем: @{ban['by']}\n\n"
            )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Назад", callback_data="admin_bans"))
    bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")

# --- Лог банов (последние 10) ---
@bot.callback_query_handler(func=lambda call: call.data == "log_bans")
def log_bans_callback(call):
    text = "<b>История банов (последние 10):</b>\n"
    if not BAN_LOG:
        text += "История пуста."
    else:
        for entry in BAN_LOG[-10:][::-1]:
            text += (
                f"🕓 <b>@{entry['username']}</b> (ID: {entry['user_id']})\n"
                f"— С: {entry['start'].strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"— До: {entry['until'].strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"— Причина: {entry['reason']}\n"
                f"— Кем: @{entry['by']}\n"
            )
            if entry.get('unban_time'):
                text += (
                    f"— Разбанен: {entry['unban_time'].strftime('%Y-%m-%d %H:%M:%S')} админом: @{entry['unban_by']}\n"
                )
            text += "\n"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Назад", callback_data="admin_bans"))
    bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode="HTML")


def send_admin_bans_menu(chat_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🚫 Забанить", callback_data="ban_do"))
    markup.add(types.InlineKeyboardButton("🔓 Разбанить", callback_data="unban_do"))
    markup.add(types.InlineKeyboardButton("📋 Текущие баны", callback_data="list_bans"))
    markup.add(types.InlineKeyboardButton("📜 Лог банов", callback_data="log_bans"))
    markup.add(types.InlineKeyboardButton("Назад", callback_data="admin_panel"))
    bot.send_message(chat_id, "Меню управления банами:", reply_markup=markup)    

def send_topup_notifications():
    for req in PENDING_TOPUPS:
        if req['taken_by'] is None:
            text = (
                f"🚨 Запрос на пополнение баланса!\n"
                f"Пользователь: @{req['username']} (ID: {req['user_id']})\n"
                # f"Ссылка: tg://user?id={req['user_id']}\n"
                f"Он хочет пополнить баланс GW-coin.\n\n"
                f"Нажмите кнопку ниже, если берете заявку в работу!"
            )
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("Взять заявку в работу", callback_data=f"take_topup_{req['user_id']}"))
            for admin_id in ADMIN_IDS:
                try:
                    bot.send_message(admin_id, text, reply_markup=markup)
                except Exception as e:
                    print(f"Ошибка отправки админу {admin_id}: {e}")

def send_topup_notification_for_request(req, reminder=False):
    text = (
        f"🚨 Запрос на пополнение баланса!\n"
        f"Пользователь: @{req['username']} (ID: {req['user_id']})\n"
        f"Ссылка: tg://user?id={req['user_id']}\n"
        f"Он хочет пополнить баланс GW-coin.\n"
    )
    if reminder:
        text += "\n⚠️ <b>ВНИМАНИЕ! Заявка все еще не взята в работу, возьмите ее в ближайшее время!</b>\n"
    text += "\nНажмите кнопку ниже, если берете заявку в работу!"
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Взять заявку в работу", callback_data=f"take_topup_{req['user_id']}"))
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, text, reply_markup=markup, parse_mode="HTML")
        except Exception as e:
            print(f"Ошибка отправки админу {admin_id}: {e}")


def topup_reminder_loop():
    while True:
        now = datetime.utcnow()
        for req in PENDING_TOPUPS:
            # Если никто не взял заявку и прошло более 30 минут — только по этой заявке шлем напоминание!
            if req['taken_by'] is None and (now - req['time']).total_seconds() > 1800:
                send_topup_notification_for_request(req, reminder=True)
                req['time'] = now  # чтобы не спамить каждую минуту
        time.sleep(60)  # Проверяем раз в минуту

# Запускаем фоновый поток
reminder_thread = threading.Thread(target=topup_reminder_loop, daemon=True)
reminder_thread.start()


@bot.message_handler(commands=['topup_status'])
def show_topup_status(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "Нет доступа.")
        return
    if not PENDING_TOPUPS:
        bot.send_message(message.chat.id, "Нет активных заявок.")
        return
    text = "Активные заявки:\n"
    for req in PENDING_TOPUPS:
        status = f"В работе у @{req['taken_by']}" if req['taken_by'] else "Ожидает исполнителя"
        text += f"@{req['username']} (ID: {req['user_id']}): {status}\n"
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.text == "💸 Пополнить баланс")
def topup_request(message):
    user = get_user(message.from_user.id)
    username = user.get("username") or f"id{message.from_user.id}"
    bot.send_message(message.chat.id, "✅ Ваша заявка на пополнение отправлена администратору. Ожидайте инструкцию!")
    
    # Добавляем заявку в очередь
    PENDING_TOPUPS.append({
        'user_id': message.from_user.id,
        'username': username,
        'taken_by': None,
        'time': datetime.now(timezone.utc)
    })
    
    send_topup_notifications()    

@bot.callback_query_handler(lambda call: call.data.startswith("take_topup_"))
def take_topup(call):
    user_id = int(call.data.split("_")[-1])
    admin_user = get_user(call.from_user.id)
    admin_username = admin_user.get("username") or f"id{call.from_user.id}"
    for req in PENDING_TOPUPS:
        if req['user_id'] == user_id and req['taken_by'] is None:
            req['taken_by'] = admin_username
            bot.send_message(call.from_user.id, f"Вы взяли заявку пользователя @{req['username']} (ID: {user_id}) в работу.")
            bot.send_message(user_id, f"Ваша заявка на пополнение в работе! Вам напишет администратор @{admin_username}.")
            for aid in ADMIN_IDS:
                if aid != call.from_user.id:
                    try:
                        bot.send_message(aid, f"Заявку пользователя @{req['username']} взял в работу @{admin_username}.")
                    except Exception as e:
                        print(e)
            break
    bot.answer_callback_query(call.id, "Заявка взята в работу.")

@bot.callback_query_handler(func=lambda call: call.data == "admin_panel")
def admin_panel_inline(call):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("💰 GW-coin", callback_data="admin_gwcoin_menu"))
    markup.add(types.InlineKeyboardButton("🏷️ DP-коды", callback_data="admin_dp_menu"))
    markup.add(types.InlineKeyboardButton("👤 Пользователи", callback_data="admin_user_menu"))
    markup.add(types.InlineKeyboardButton("💳 Заявки на пополнение", callback_data="admin_topup_requests"))
    markup.add(types.InlineKeyboardButton("🚫 Баны пользователей", callback_data="admin_bans"))
    markup.add(types.InlineKeyboardButton("Назад", callback_data="back_main"))
    bot.send_message(call.message.chat.id, "Админ-панель. Выберите раздел:", reply_markup=markup)

# --- GW-coin submenu ---
@bot.callback_query_handler(func=lambda call: call.data == "admin_gwcoin_menu")
def admin_gwcoin_menu_inline(call):
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("➕ Выдать GW-coin", callback_data="admin_give_gw"),
        types.InlineKeyboardButton("➖ Отнять GW-coin", callback_data="admin_take_gw"),
    )
    markup.add(
        types.InlineKeyboardButton("🔄 Обнулить баланс", callback_data="admin_reset_gw"),
        types.InlineKeyboardButton("🗒️ Лог GW-coin", callback_data="admin_log_gw")
    )
    markup.add(types.InlineKeyboardButton("Назад", callback_data="admin_panel"))
    bot.send_message(call.message.chat.id, "Операции с GW-coin:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_give_gw")
def admin_give_gw_inline(call):
    msg = bot.send_message(call.message.chat.id, "Введите username (@username) и сумму через пробел (пример: @vasya 500):")
    bot.register_next_step_handler(msg, process_give)

def process_give(message):
    admin_user = get_user(message.from_user.id)
    try:
        username, amount = message.text.strip().split()
        amount = int(amount)
        if username.startswith("@"):
            username = username[1:]
        user_id = find_userid_by_username(username)
        if user_id:
            user = get_user(user_id)
            old = user["balance"]
            user["balance"] += amount
            set_user(user_id, user)
            log_gwcoin(user, admin_user, old, user["balance"], amount, "Админ-выдача")
            bot.send_message(message.chat.id, f"Начислено {amount} GW-coin пользователю @{username}.")
        else:
            bot.send_message(message.chat.id, "Пользователь с таким username не найден!")
    except Exception:
        bot.send_message(message.chat.id, "Ошибка. Проверьте ввод.")
    admin_gwcoin_menu_inline(message)

@bot.callback_query_handler(func=lambda call: call.data == "admin_take_gw")
def admin_take_gw_inline(call):
    msg = bot.send_message(call.message.chat.id, "Введите username (@username) и сумму через пробел (пример: @vasya 500):")
    bot.register_next_step_handler(msg, process_take)

def process_take(message):
    admin_user = get_user(message.from_user.id)
    try:
        username, amount = message.text.strip().split()
        amount = int(amount)
        if username.startswith("@"):
            username = username[1:]
        user_id = find_userid_by_username(username)
        if user_id:
            user = get_user(user_id)
            old = user["balance"]
            user["balance"] -= amount
            set_user(user_id, user)
            log_gwcoin(user, admin_user, old, user["balance"], -amount, "Админ-отнятие")
            bot.send_message(message.chat.id, f"Списано {amount} GW-coin у пользователя @{username}.")
        else:
            bot.send_message(message.chat.id, "Пользователь с таким username не найден!")
    except Exception:
        bot.send_message(message.chat.id, "Ошибка. Проверьте ввод.")
    admin_gwcoin_menu_inline(message)

@bot.callback_query_handler(func=lambda call: call.data == "admin_reset_gw")
def admin_reset_gw_inline(call):
    msg = bot.send_message(call.message.chat.id, "Введите username (@username) для обнуления баланса:")
    bot.register_next_step_handler(msg, process_reset)

def process_reset(message):
    admin_user = get_user(message.from_user.id)
    try:
        username = message.text.strip()
        if username.startswith("@"):
            username = username[1:]
        user_id = find_userid_by_username(username)
        if user_id:
            user = get_user(user_id)
            old = user["balance"]
            user["balance"] = 0
            set_user(user_id, user)
            log_gwcoin(user, admin_user, old, 0, -old, "Админ-сброс")
            bot.send_message(message.chat.id, f"Баланс пользователя @{username} обнулён.")
        else:
            bot.send_message(message.chat.id, "Пользователь с таким username не найден!")
    except Exception:
        bot.send_message(message.chat.id, "Ошибка. Проверьте ввод.")
    admin_gwcoin_menu_inline(message)

@bot.callback_query_handler(func=lambda call: call.data == "admin_log_gw")
def admin_log_gw_inline(call):
    lines = show_log_lines("GWCOIN")
    if not lines:
        bot.send_message(call.message.chat.id, "Лог GW-coin пуст.")
    else:
        bot.send_message(call.message.chat.id, "".join(lines))
    admin_gwcoin_menu_inline(call)



# --- DP-коды submenu ---
@bot.callback_query_handler(func=lambda call: call.data == "admin_dp_menu")
def admin_dp_menu_inline(call):
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🆕 Добавить DP-коды", callback_data="admin_add_dp"),
        types.InlineKeyboardButton("🔍 Остаток DP-кодов", callback_data="admin_left_dp"),
    )
    markup.add(
        types.InlineKeyboardButton("🗒️ Лог DP-кодов", callback_data="admin_log_dp")
    )
    markup.add(types.InlineKeyboardButton("Назад", callback_data="admin_panel"))
    bot.send_message(call.message.chat.id, "Операции с DP-кодами:", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data == "admin_add_dp")
def admin_add_dp_inline(call):
    msg = bot.send_message(call.message.chat.id, "Введите номинал DP (например, 10) и коды через пробел (например: 10 abcd1234 efgh5678):")
    bot.register_next_step_handler(msg, process_add_dpcodes)

def admin_dp_menu_message(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🆕 Добавить DP-коды", callback_data="admin_add_dp"),
        types.InlineKeyboardButton("🔍 Остаток DP-кодов", callback_data="admin_left_dp"),
    )
    markup.add(
        types.InlineKeyboardButton("🗒️ Лог DP-кодов", callback_data="admin_log_dp")
    )
    markup.add(types.InlineKeyboardButton("Назад", callback_data="admin_panel"))
    bot.send_message(message.chat.id, "Операции с DP-кодами:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_add_dp")
def admin_add_dp_inline(call):
    msg = bot.send_message(call.message.chat.id, "Введите номинал DP (например, 10) и коды через пробел (например: 10 abcd1234 efgh5678):")
    bot.register_next_step_handler(msg, process_add_dpcodes)

def process_add_dpcodes(message):
    admin_user = get_user(message.from_user.id)
    try:
        parts = message.text.strip().split()
        nominal = int(parts[0])
        codes = parts[1:]
        if not codes:
            bot.send_message(message.chat.id, "Вы не указали коды!")
            admin_dp_menu_message(message)
            return
        add_dp_codes(nominal, codes, admin_user)
        bot.send_message(message.chat.id, f"Добавлено {len(codes)} DP-кодов номиналом {nominal} DP.")
    except Exception:
        bot.send_message(message.chat.id, "Ошибка. Проверьте ввод. Пример: 10 abcd1234 efgh5678")
    admin_dp_menu_message(message)

@bot.callback_query_handler(func=lambda call: call.data == "admin_left_dp")
def admin_left_dp_inline(call):
    codes = all_dp_codes_left()
    if not codes:
        text = "Нет загруженных DP-кодов."
    else:
        text = "Остатки DP-кодов по номиналам:\n"
        for nominal in sorted(codes, key=lambda x: int(x)):
            text += f"\n{nominal} DP ({len(codes[nominal])} код(ов)):\n"
            text += "\n".join([f"  - {c}" for c in codes[nominal]])
    bot.send_message(call.message.chat.id, text)
    admin_dp_menu_inline(call)

@bot.callback_query_handler(func=lambda call: call.data == "admin_log_dp")
def admin_log_dp_inline(call):
    lines = show_log_lines("DP")
    if not lines:
        bot.send_message(call.message.chat.id, "Лог DP-кодов пуст.")
    else:
        bot.send_message(call.message.chat.id, "".join(lines))
    admin_dp_menu_inline(call)

# --- Пользователи submenu ---
@bot.callback_query_handler(func=lambda call: call.data == "admin_user_menu")
def admin_user_menu_inline(call):
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🔝 Сделать админом", callback_data="admin_make_admin"),
        types.InlineKeyboardButton("🗒️ Лог пользователей", callback_data="admin_log_user"),
    )
    markup.add(types.InlineKeyboardButton("Назад", callback_data="admin_panel"))
    bot.send_message(call.message.chat.id, "Операции с пользователями:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_make_admin")
def admin_make_admin_inline(call):
    msg = bot.send_message(call.message.chat.id, "Введите username (@username) для назначения админом:")
    bot.register_next_step_handler(msg, process_make_admin)

def process_make_admin(message):
    admin_user = get_user(message.from_user.id)
    try:
        username = message.text.strip()
        if username.startswith("@"):
            username = username[1:]
        user_id = find_userid_by_username(username)
        if user_id:
            user = get_user(user_id)
            user["is_admin"] = True
            set_user(user_id, user)
            log_user(admin_user, f"Назначил @{username} админом")
            bot.send_message(message.chat.id, f"Пользователь @{username} теперь админ.")
        else:
            bot.send_message(message.chat.id, "Пользователь с таким username не найден!")
    except Exception:
        bot.send_message(message.chat.id, "Ошибка. Проверьте ввод.")
    admin_user_menu_inline(message)

@bot.callback_query_handler(func=lambda call: call.data == "admin_log_user")
def admin_log_user_inline(call):
    lines = show_log_lines("USER")
    if not lines:
        bot.send_message(call.message.chat.id, "Лог пользователей пуст.")
    else:
        bot.send_message(call.message.chat.id, "".join(lines))
    admin_user_menu_inline(call)

# ========== Экспорт логов ==========
@bot.message_handler(commands=['exportlogs'])
def export_logs(message):
    if is_admin(message.from_user.id):
        if os.path.isfile(LOG_FILE):
            with open(LOG_FILE, "rb") as f:
                bot.send_document(message.chat.id, f)
        else:
            bot.send_message(message.chat.id, "Лог-файл пуст.")
    else:
        bot.send_message(message.chat.id, "Нет доступа.")

@bot.message_handler(commands=['help'])
def help_command(message):
    bot.send_message(message.chat.id, "Используйте меню или инлайн-кнопки. Для админов доступна команда /exportlogs.")

if __name__ == "__main__":
    bot.infinity_polling()
