import asyncio
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import logging
import html
import logging.handlers
import os
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Импорт клавиатур
from keyboards import (
    get_user_keyboard, 
    get_admin_keyboard, 
    get_mixed_keyboard,
    get_payment_confirmation_keyboard,
    get_admin_payment_confirmation_keyboard,
    get_cancel_keyboard,
    get_back_keyboard
)

# Загрузка переменных окружения
load_dotenv()

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен в .env файле")

# Преобразование строки с ID админов в список
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
ADMINS = [int(admin_id.strip()) for admin_id in ADMIN_IDS_STR.split(",") if admin_id.strip()]
if not ADMINS:
    raise ValueError("ADMIN_IDS не установлен в .env файле")

# Дни на оплату
PAYMENT_TIMEOUT_DAYS = int(os.getenv("PAYMENT_TIMEOUT_DAYS", "1"))

# Эмодзи для визуального оформления
EMOJI = {
    'success': '✅',
    'error': '❌',
    'warning': '⚠️',
    'info': '💡',
    'money': '💰',
    'clock': '⏰',
    'calendar': '📅',
    'user': '👤',
    'admin': '👨‍💼',
    'chat': '💬',
    'stats': '📊',
    'settings': '⚙️',
    'back': '🔙',
    'cancel': '❌',
    'add': '➕',
    'remove': '➖',
    'list': '📋',
    'search': '🔍',
    'alert': '🚨',
    'bell': '🔔',
    'check': '✔️',
    'loading': '⏳',
    'rocket': '🚀',
    'broadcast': '📢'
}

# Состояния для FSM
class AdminStates(StatesGroup):
    waiting_user_id = State()
    waiting_day = State()
    waiting_time = State()
    waiting_message = State()
    waiting_unlink_user = State()
    waiting_confirm_payment = State()
    waiting_alias = State()
    waiting_default_message = State()
    chatting_with_user = State()

class UserStates(StatesGroup):
    chatting_with_admin = State()
    admin_mode = State()
    user_mode = State()

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

# Вспомогательные функции для форматирования
def escape_html(text: str) -> str:
    """Экранирует HTML-специальные символы"""
    return html.escape(str(text))

def format_user_info(user_id: int, user_name: str = None, username: str = None) -> str:
    """Форматирует информацию о пользователе"""
    info = f"{EMOJI['user']} "
    if user_name:
        info += f"<b>{escape_html(user_name)}</b> "
    if username:
        info += f"(@{escape_html(username)}) "
    info += f"\n🆔 ID: <code>{user_id}</code>"
    return info

def format_date(date: datetime) -> str:
    """Форматирует дату в читаемый вид"""
    return date.strftime('%d.%m.%Y %H:%M')

def format_payment_info(day: int, time: str, message: str) -> str:
    """Форматирует информацию о платеже"""
    return (
        f"{EMOJI['calendar']} <b>День:</b> {day} число\n"
        f"{EMOJI['clock']} <b>Время:</b> {time}\n"
        f"{EMOJI['chat']} <b>Сообщение:</b> <i>{escape_html(message[:100])}"
        f"{'...' if len(message) > 100 else ''}</i>"
    )

# Заменяем все случаи использования разделительной линии
def format_divider() -> str:
    """Возвращает отформатированную разделительную линию"""
    return f"{'━' * 20}\n\n"

# База данных
def init_db():
    conn = sqlite3.connect('payment_bot.db')
    cursor = conn.cursor()
    
    # Таблица настроек админов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_settings (
            admin_id INTEGER PRIMARY KEY,
            alias TEXT NOT NULL DEFAULT 'Администратор',
            default_message TEXT NOT NULL DEFAULT 'Время оплаты! Пожалуйста, оплатите услуги.',
            show_notifications BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица связей админ-пользователь
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_admin_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            admin_id INTEGER NOT NULL,
            payment_day INTEGER NOT NULL,
            payment_time TEXT NOT NULL,
            payment_message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, admin_id)
        )
    ''')
    
    # Таблица платежей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            admin_id INTEGER NOT NULL,
            payment_date DATE NOT NULL,
            confirmed BOOLEAN DEFAULT FALSE,
            amount REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица для хранения ID сообщений для удаления
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pending_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            admin_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            due_date TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица активных чатов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS active_chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            admin_id INTEGER NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, admin_id)
        )
    ''')
    
    # Таблица истории сообщений
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS message_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user_id INTEGER NOT NULL,
            to_user_id INTEGER NOT NULL,
            message_type TEXT NOT NULL,
            message_content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

# Утилиты для работы с базой данных
def get_db_connection():
    return sqlite3.connect('payment_bot.db')

def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

def get_admin_for_user(user_id: int) -> Optional[int]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT admin_id FROM user_admin_links WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def get_users_for_admin(admin_id: int) -> List[tuple]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, payment_day, payment_time, payment_message 
        FROM user_admin_links WHERE admin_id = ?
        ORDER BY payment_day, payment_time
    ''', (admin_id,))
    result = cursor.fetchall()
    conn.close()
    return result

def add_user_to_admin(user_id: int, admin_id: int, day: int, time: str, message: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO user_admin_links 
        (user_id, admin_id, payment_day, payment_time, payment_message)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, admin_id, day, time, message))
    conn.commit()
    conn.close()

def remove_user_from_admin(user_id: int, admin_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM user_admin_links WHERE user_id = ? AND admin_id = ?', 
                   (user_id, admin_id))
    conn.commit()
    conn.close()

def get_payment_stats(admin_id: int) -> Dict:
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Общая статистика
    cursor.execute('''
        SELECT COUNT(*) FROM payments WHERE admin_id = ? AND confirmed = TRUE
    ''', (admin_id,))
    confirmed_payments = cursor.fetchone()[0]
    
    cursor.execute('''
        SELECT COUNT(*) FROM payments WHERE admin_id = ? AND confirmed = FALSE
    ''', (admin_id,))
    pending_payments = cursor.fetchone()[0]
    
    cursor.execute('''
        SELECT COUNT(*) FROM user_admin_links WHERE admin_id = ?
    ''', (admin_id,))
    total_users = cursor.fetchone()[0]
    
    # Статистика за текущий месяц
    current_month = datetime.now().replace(day=1)
    cursor.execute('''
        SELECT COUNT(*) FROM payments 
        WHERE admin_id = ? AND confirmed = TRUE AND payment_date >= ?
    ''', (admin_id, current_month))
    month_payments = cursor.fetchone()[0]
    
    # Просроченные платежи
    cursor.execute('''
        SELECT COUNT(*) FROM pending_payments 
        WHERE admin_id = ? AND due_date <= ?
    ''', (admin_id, datetime.now()))
    overdue_payments = cursor.fetchone()[0]
    
    # Сумма платежей за месяц
    cursor.execute('''
        SELECT SUM(amount) FROM payments 
        WHERE admin_id = ? AND confirmed = TRUE AND payment_date >= ?
    ''', (admin_id, current_month))
    month_amount = cursor.fetchone()[0] or 0
    
    conn.close()
    
    return {
        'confirmed': confirmed_payments,
        'pending': pending_payments,
        'total_users': total_users,
        'month_payments': month_payments,
        'overdue': overdue_payments,
        'month_amount': month_amount
    }

def get_admin_settings(admin_id: int) -> tuple:
    """Получить настройки админа"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT alias, default_message, show_notifications FROM admin_settings WHERE admin_id = ?', (admin_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return result
    else:
        # Создаем настройки по умолчанию
        create_admin_settings(admin_id)
        return ('Администратор', 'Время оплаты! Пожалуйста, оплатите услуги.', True)

def create_admin_settings(admin_id: int):
    """Создать настройки админа по умолчанию"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR IGNORE INTO admin_settings (admin_id, alias, default_message, show_notifications)
        VALUES (?, ?, ?, ?)
    ''', (admin_id, 'Администратор', 'Время оплаты! Пожалуйста, оплатите услуги.', True))
    conn.commit()
    conn.close()

def update_admin_alias(admin_id: int, alias: str):
    """Обновить псевдоним админа"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE admin_settings SET alias = ? WHERE admin_id = ?
    ''', (alias, admin_id))
    if cursor.rowcount == 0:
        cursor.execute('''
            INSERT INTO admin_settings (admin_id, alias) VALUES (?, ?)
        ''', (admin_id, alias))
    conn.commit()
    conn.close()

def update_admin_default_message(admin_id: int, message: str):
    """Обновить сообщение по умолчанию"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE admin_settings SET default_message = ? WHERE admin_id = ?
    ''', (message, admin_id))
    if cursor.rowcount == 0:
        cursor.execute('''
            INSERT INTO admin_settings (admin_id, default_message) VALUES (?, ?)
        ''', (admin_id, message))
    conn.commit()
    conn.close()

def start_chat_session(user_id: int, admin_id: int):
    """Начать сессию чата"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO active_chats (user_id, admin_id)
        VALUES (?, ?)
    ''', (user_id, admin_id))
    conn.commit()
    conn.close()

def end_chat_session(user_id: int, admin_id: int):
    """Завершить сессию чата"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM active_chats WHERE user_id = ? AND admin_id = ?
    ''', (user_id, admin_id))
    conn.commit()
    conn.close()

def get_active_chats_for_admin(admin_id: int) -> List[int]:
    """Получить список активных чатов для админа"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM active_chats WHERE admin_id = ?', (admin_id,))
    result = [row[0] for row in cursor.fetchall()]
    conn.close()
    return result

def is_chat_active(user_id: int, admin_id: int) -> bool:
    """Проверить активен ли чат"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM active_chats WHERE user_id = ? AND admin_id = ?', (user_id, admin_id))
    result = cursor.fetchone()[0] > 0
    conn.close()
    return result

def add_message_to_history(from_user_id: int, to_user_id: int, message_type: str, content: str = None):
    """Добавить сообщение в историю"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO message_history (from_user_id, to_user_id, message_type, message_content)
        VALUES (?, ?, ?, ?)
    ''', (from_user_id, to_user_id, message_type, content))
    conn.commit()
    conn.close()

# Обработчики команд
@dp.message(Command("start"))
async def start_handler(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_name = message.from_user.full_name or "Пользователь"
    username = message.from_user.username
    
    await state.clear()  # Очищаем состояние
    
    # Отправляем индикатор набора
    await bot.send_chat_action(user_id, "typing")
    
    if is_admin(user_id):
        await state.set_state(UserStates.user_mode)  # По умолчанию пользовательский режим
        keyboard = get_mixed_keyboard(True)
        
        text = (
            f"{EMOJI['rocket']} <b>Добро пожаловать в систему управления платежами!</b>\n\n"
            f"{format_user_info(user_id, user_name, username)}\n\n"
            f"{EMOJI['admin']} <b>Статус:</b> Администратор\n\n"
            f"{EMOJI['info']} Используйте кнопки меню для управления системой."
        )
        
        await message.answer(text, reply_markup=keyboard, parse_mode='HTML')
    else:
        admin_id = get_admin_for_user(user_id)
        keyboard = get_user_keyboard()
        
        if admin_id:
            admin_alias, _, _ = get_admin_settings(admin_id)
            
            # Получаем информацию о настройках платежа
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT payment_day, payment_time FROM user_admin_links 
                WHERE user_id = ? AND admin_id = ?
            ''', (user_id, admin_id))
            payment_info = cursor.fetchone()
            conn.close()
            
            text = (
                f"{EMOJI['rocket']} <b>Добро пожаловать в систему управления платежами!</b>\n\n"
                f"{format_user_info(user_id, user_name, username)}\n\n"
                f"{EMOJI['success']} <b>Статус:</b> Активный пользователь\n"
                f"{EMOJI['admin']} <b>Ваш администратор:</b> {escape_html(admin_alias)}\n"
            )
            
            if payment_info:
                day, time = payment_info
                text += (
                    f"\n{EMOJI['bell']} <b>Напоминания об оплате:</b>\n"
                    f"• Каждое <b>{day} число</b> месяца\n"
                    f"• В <b>{time}</b> по вашему времени\n"
                )
            
            await message.answer(text, reply_markup=keyboard, parse_mode='HTML')
        else:
            text = (
                f"{EMOJI['rocket']} <b>Добро пожаловать в систему управления платежами!</b>\n\n"
                f"{format_user_info(user_id, user_name, username)}\n\n"
                f"{EMOJI['info']} <b>Статус:</b> Ожидание подключения\n\n"
                f"{EMOJI['loading']} Администраторы уведомлены о вашем запросе.\n"
                f"Как только один из них добавит вас в систему, вы получите уведомление.\n\n"
            )
            
            await message.answer(text, reply_markup=keyboard, parse_mode='HTML')
            
            # Уведомляем всех админов о новом пользователе
            admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text=f"{EMOJI['add']} Добавить пользователя",
                    callback_data=f"add_new_user_{user_id}"
                )]
            ])
            
            admin_text = (
                f"{EMOJI['bell']} <b>Новый пользователь в системе!</b>\n"
                f"{format_divider()}"
                f"{format_user_info(user_id, user_name, username)}\n\n"
                f"{EMOJI['info']} Нажмите кнопку ниже, чтобы добавить пользователя к себе."
            )
            
            for admin_id in ADMINS:
                try:
                    _, _, show_notifications = get_admin_settings(admin_id)
                    if show_notifications:
                        await bot.send_message(
                            admin_id,
                            admin_text,
                            reply_markup=admin_keyboard,
                            parse_mode='HTML'
                        )
                except Exception as e:
                    logging.error(f"Ошибка отправки уведомления админу {admin_id}: {e}")

# Обработчик команд для быстрого чата
@dp.message(lambda message: message.text and message.text.startswith('/chat_') and is_admin(message.from_user.id))
async def quick_chat_command(message: Message, state: FSMContext):
    try:
        user_id = int(message.text.split('_')[1])
        
        # Проверяем что чат активен
        if not is_chat_active(user_id, message.from_user.id):
            await message.answer(f"{EMOJI['error']} Чат с этим пользователем не активен.")
            return
        
        await state.set_state(AdminStates.chatting_with_user)
        await state.update_data(chat_user_id=user_id)
        
        try:
            user_info = await bot.get_chat(user_id)
            user_name = user_info.full_name or "Без имени"
            username = user_info.username
        except:
            user_name = "Недоступен"
            username = None
        
        keyboard = get_back_keyboard()
        
        text = (
            f"{EMOJI['chat']} <b>Чат с пользователем</b>\n\n"
            f"{format_user_info(user_id, user_name, username)}\n\n"
            f"{EMOJI['info']} Отправляйте сообщения, и они будут мгновенно переданы пользователю.\n"
            f"Используйте кнопку '{EMOJI['back']} Назад' для завершения чата."
        )
        
        await message.answer(text, reply_markup=keyboard, parse_mode='HTML')
        
        # Уведомляем пользователя
        admin_alias, _, _ = get_admin_settings(message.from_user.id)
        await bot.send_message(
            user_id,
            f"{EMOJI['admin']} <b>{escape_html(admin_alias)}</b> присоединился к чату",
            parse_mode='HTML'
        )
        
    except (ValueError, IndexError):
        await message.answer(f"{EMOJI['error']} Неверный формат команды. Используйте: /chat_USER_ID")

# Обработчики кнопок
@dp.message(F.text == f"{EMOJI['settings']} Админ-панель")
async def admin_panel_button(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer(f"{EMOJI['error']} У вас нет прав администратора.")
        return
    
    await bot.send_chat_action(message.chat.id, "typing")
    await state.set_state(UserStates.admin_mode)
    keyboard = get_admin_keyboard()
    
    stats = get_payment_stats(message.from_user.id)
    
    text = (
        f"{EMOJI['settings']} <b>Панель администратора</b>\n"
        f"{format_divider()}"
        f"{EMOJI['stats']} <b>Краткая статистика:</b>\n"
        f"• Пользователей: <b>{stats['total_users']}</b>\n"
        f"• Платежей за месяц: <b>{stats['month_payments']}</b>\n"
        f"• Ожидают подтверждения: <b>{stats['pending']}</b>\n"
    )
    
    if stats['overdue'] > 0:
        text += f"{format_divider()}"
        text += f"• {EMOJI['alert']} Просроченных: <b>{stats['overdue']}</b>\n"
    
    text += f"{format_divider()}"
    text += f"\nИспользуйте кнопки ниже для управления системой:"
    
    await message.answer(text, reply_markup=keyboard, parse_mode='HTML')

@dp.message(F.text == f"{EMOJI['stats']} Мой статус")
async def status_button(message: Message):
    user_id = message.from_user.id
    admin_id = get_admin_for_user(user_id)
    
    await bot.send_chat_action(message.chat.id, "typing")
    
    if is_admin(user_id):
        # Статус админа
        users = get_users_for_admin(user_id)
        stats = get_payment_stats(user_id)
        alias, default_message, show_notifications = get_admin_settings(user_id)
        
        text = f"{EMOJI['admin']} <b>Ваш статус: Администратор</b>\n"
        text += format_divider()
        
        text += f"{EMOJI['user']} <b>Профиль:</b>\n"
        text += f"• Псевдоним: <b>{escape_html(alias)}</b>\n"
        text += f"• Уведомления: <b>{'Включены' if show_notifications else 'Выключены'}</b>\n\n"
        
        text += f"{EMOJI['stats']} <b>Статистика:</b>\n"
        text += f"• Пользователей: <b>{stats['total_users']}</b>\n"
        text += f"• Всего платежей: <b>{stats['confirmed']}</b>\n"
        text += f"• За текущий месяц: <b>{stats['month_payments']}</b>\n"
        
        if stats['month_amount'] > 0:
            text += f"• Сумма за месяц: <b>{stats['month_amount']:.2f} ₽</b>\n"
        
        if stats['pending'] > 0:
            text += f"{format_divider()}"
            text += f"\n{EMOJI['loading']} Ожидают подтверждения: <b>{stats['pending']}</b>\n"
        
        if stats['overdue'] > 0:
            text += f"{format_divider()}"
            text += f"{EMOJI['alert']} Просроченных: <b>{stats['overdue']}</b>\n"
        
        # Процент успешных платежей
        if stats['confirmed'] + stats['pending'] > 0:
            success_rate = (stats['confirmed'] / (stats['confirmed'] + stats['pending']) * 100)
            text += f"{format_divider()}"
            text += f"{EMOJI['success']} Успешность платежей: <b>{success_rate:.1f}%</b>"
        
    else:
        # Статус пользователя
        if admin_id:
            # Получаем информацию о настройках
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT payment_day, payment_time, payment_message 
                FROM user_admin_links WHERE user_id = ? AND admin_id = ?
            ''', (user_id, admin_id))
            result = cursor.fetchone()
            
            # Статистика платежей пользователя
            cursor.execute('''
                SELECT COUNT(*) FROM payments 
                WHERE user_id = ? AND admin_id = ? AND confirmed = TRUE
            ''', (user_id, admin_id))
            confirmed_count = cursor.fetchone()[0]
            
            cursor.execute('''
                SELECT COUNT(*) FROM payments 
                WHERE user_id = ? AND admin_id = ? AND confirmed = FALSE
            ''', (user_id, admin_id))
            pending_count = cursor.fetchone()[0]
            
            # Последний платеж
            cursor.execute('''
                SELECT payment_date FROM payments 
                WHERE user_id = ? AND admin_id = ? AND confirmed = TRUE
                ORDER BY payment_date DESC LIMIT 1
            ''', (user_id, admin_id))
            last_payment = cursor.fetchone()
            
            conn.close()
            
            # Получаем псевдоним админа
            admin_alias, _, _ = get_admin_settings(admin_id)
            
            if result:
                day, time, msg = result
                
                text = f"{EMOJI['user']} <b>Ваш статус</b>\n"
                text += format_divider()
                
                text += f"{EMOJI['success']} <b>Активный пользователь</b>\n"
                text += f"{EMOJI['admin']} Администратор: <b>{escape_html(admin_alias)}</b>\n\n"
                
                text += f"{EMOJI['bell']} <b>График напоминаний:</b>\n"
                text += f"• День: <b>{day} число</b> каждого месяца\n"
                text += f"• Время: <b>{time}</b>\n\n"
                
                text += f"{EMOJI['money']} <b>История платежей:</b>\n"
                text += f"• Подтверждено: <b>{confirmed_count}</b>\n"
                
                if pending_count > 0:
                    text += f"• Ожидают: <b>{pending_count}</b>\n"
                
                if last_payment:
                    last_date = datetime.strptime(last_payment[0], '%Y-%m-%d')
                    days_ago = (datetime.now() - last_date).days
                    text += f"{format_divider()}"
                    text += f"{EMOJI['calendar']} Последний платеж: <b>{days_ago} дн. назад</b>\n\n"
                
                # Следующее напоминание
                next_reminder = calculate_next_reminder(day, time)
                if next_reminder:
                    text += f"{format_divider()}"
                    text += f"{EMOJI['rocket']} <b>Первое напоминание:</b> {format_date(next_reminder)}\n\n"
                
                text += f"{format_divider()}"
                text += f"{EMOJI['info']} Используйте /start для просмотра вашего статуса."
            else:
                text = f"{EMOJI['error']} Ошибка получения данных"
        else:
            text = (
                f"{EMOJI['user']} <b>Ваш статус</b>\n"
                f"{format_divider()}"
                f"{EMOJI['warning']} <b>Не привязан к администратору</b>\n\n"
                f"Передайте ваш ID администратору:\n"
                f"<code>{user_id}</code>"
            )
    
    await message.answer(text, parse_mode='HTML')

def calculate_next_reminder(day: int, time_str: str) -> Optional[datetime]:
    """Вычисляет дату следующего напоминания"""
    try:
        now = datetime.now()
        hour, minute = map(int, time_str.split(':'))
        
        # Создаем дату для текущего месяца
        try:
            next_date = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError:
            # Если день не существует в текущем месяце (например, 31 февраля)
            # Используем последний день месяца
            import calendar
            last_day = calendar.monthrange(now.year, now.month)[1]
            next_date = now.replace(day=min(day, last_day), hour=hour, minute=minute, second=0, microsecond=0)
        
        # Если дата уже прошла, переносим на следующий месяц
        if next_date <= now:
            if now.month == 12:
                next_date = next_date.replace(year=now.year + 1, month=1)
            else:
                next_date = next_date.replace(month=now.month + 1)
            
            # Проверяем день для следующего месяца
            try:
                next_date = next_date.replace(day=day)
            except ValueError:
                import calendar
                last_day = calendar.monthrange(next_date.year, next_date.month)[1]
                next_date = next_date.replace(day=min(day, last_day))
        
        return next_date
    except:
        return None

@dp.message(F.text == f"{EMOJI['chat']} Связь с админом")
async def chat_button(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    if is_admin(user_id):
        await message.answer(f"{EMOJI['admin']} Вы администратор! Пользователи могут связаться с вами через эту функцию.")
        return
    
    admin_id = get_admin_for_user(user_id)
    
    if not admin_id:
        await message.answer(
            f"{EMOJI['error']} Вы не привязаны к администратору.\n\n"
            f"Передайте ваш ID администратору для добавления в систему:\n"
            f"<code>{user_id}</code>",
            parse_mode='HTML'
        )
        return
    
    # Начинаем сессию чата
    start_chat_session(user_id, admin_id)
    
    await state.set_state(UserStates.chatting_with_admin)
    await state.update_data(admin_id=admin_id)
    
    keyboard = get_back_keyboard()
    admin_alias, _, _ = get_admin_settings(admin_id)
    
    text = (
        f"{EMOJI['chat']} <b>Чат с {escape_html(admin_alias)}</b>\n"
        f"{format_divider()}"
        f"{EMOJI['success']} Соединение установлено!\n\n"
        f"Отправляйте текст, фото, видео или документы.\n"
        f"Администратор получит их мгновенно.\n\n"
        f"Используйте кнопку '{EMOJI['back']} Назад' для завершения."
    )
    
    await message.answer(text, reply_markup=keyboard, parse_mode='HTML')
    
    # Уведомляем админа о начале чата
    try:
        user_info = await bot.get_chat(user_id)
        user_name = user_info.full_name or "Без имени"
        username = user_info.username
        
        await bot.send_message(
            admin_id,
            f"{EMOJI['bell']} <b>Новый чат!</b>\n\n"
            f"{format_user_info(user_id, user_name, username)}\n\n"
            f"{EMOJI['chat']} Используйте:\n"
            f"• Команду /chat_{user_id}\n"
            f"• Или кнопку '{EMOJI['chat']} Активные чаты'",
            parse_mode='HTML'
        )
    except Exception as e:
        logging.error(f"Ошибка уведомления админа: {e}")

@dp.message(F.text == f"{EMOJI['back']} Назад")
async def back_button(message: Message, state: FSMContext):
    current_state = await state.get_state()
    user_id = message.from_user.id
    
    if current_state == UserStates.chatting_with_admin.state:
        data = await state.get_data()
        admin_id = data.get('admin_id')
        
        if admin_id:
            # Завершаем сессию чата
            end_chat_session(user_id, admin_id)
            
            # Уведомляем админа
            try:
                user_info = await bot.get_chat(user_id)
                user_name = user_info.full_name or "Без имени"
                
                await bot.send_message(
                    admin_id,
                    f"{EMOJI['info']} {escape_html(user_name)} завершил чат",
                    parse_mode='HTML'
                )
            except:
                pass
        
        await state.clear()
        is_admin_user = is_admin(user_id)
        keyboard = get_mixed_keyboard(is_admin_user) if is_admin_user else get_user_keyboard()
        await message.answer(f"{EMOJI['success']} Чат завершен.", reply_markup=keyboard)
    
    elif current_state == AdminStates.chatting_with_user.state:
        data = await state.get_data()
        chat_user_id = data.get('chat_user_id')
        
        if chat_user_id:
            # Уведомляем пользователя
            admin_alias, _, _ = get_admin_settings(user_id)
            try:
                await bot.send_message(
                    chat_user_id,
                    f"{EMOJI['info']} {escape_html(admin_alias)} завершил чат",
                    parse_mode='HTML'
                )
            except:
                pass
        
        await state.clear()
        keyboard = get_admin_keyboard()
        await message.answer(f"{EMOJI['success']} Чат с пользователем завершен.", reply_markup=keyboard)
    
    else:
        await start_handler(message, state)

@dp.message(F.text == f"{EMOJI['settings']} Настройки админа")
async def admin_settings_button(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(f"{EMOJI['error']} У вас нет прав администратора.")
        return
    
    alias, default_message, show_notifications = get_admin_settings(message.from_user.id)
    
    text = (
        f"{EMOJI['settings']} <b>Настройки администратора</b>\n"
        f"{format_divider()}"
        f"{EMOJI['user']} <b>Псевдоним:</b> {escape_html(alias)}\n"
        f"{EMOJI['bell']} <b>Уведомления:</b> {'Включены' if show_notifications else 'Выключены'}\n\n"
        f"{EMOJI['chat']} <b>Сообщение по умолчанию:</b>\n"
        f"<i>{escape_html(default_message)}</i>\n\n"
        f"Выберите, что хотите изменить:"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{EMOJI['user']} Изменить псевдоним", callback_data="change_alias")],
        [InlineKeyboardButton(text=f"{EMOJI['chat']} Изменить сообщение", callback_data="change_default_message")],
        [InlineKeyboardButton(
            text=f"{EMOJI['bell']} {'Выключить' if show_notifications else 'Включить'} уведомления", 
            callback_data="toggle_notifications"
        )]
    ])
    
    await message.answer(text, reply_markup=keyboard, parse_mode='HTML')

@dp.message(F.text == f"{EMOJI['chat']} Активные чаты")
async def active_chats_button(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(f"{EMOJI['error']} У вас нет прав администратора.")
        return
    
    active_chats = get_active_chats_for_admin(message.from_user.id)
    
    if not active_chats:
        await message.answer(f"{EMOJI['info']} Нет активных чатов с пользователями.")
        return
    
    text = f"{EMOJI['chat']} <b>Активные чаты:</b>\n"
    text += format_divider()
    
    buttons = []
    for i, user_id in enumerate(active_chats, 1):
        try:
            user_info = await bot.get_chat(user_id)
            user_name = user_info.full_name or "Без имени"
            username = user_info.username
            
            text += f"{i}. {format_user_info(user_id, user_name, username)}\n\n"
            
            buttons.append([InlineKeyboardButton(
                text=f"{EMOJI['chat']} {user_name}",
                callback_data=f"start_chat_{user_id}"
            )])
        except:
            text += f"{i}. {EMOJI['user']} Недоступен (ID: <code>{user_id}</code>)\n\n"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(text, reply_markup=keyboard, parse_mode='HTML')

@dp.message(F.text == f"{EMOJI['add']} Добавить пользователя")
async def add_user_button(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer(f"{EMOJI['error']} У вас нет прав администратора.")
        return
    
    await state.set_state(AdminStates.waiting_user_id)
    keyboard = get_cancel_keyboard()
    
    text = (
        f"{EMOJI['add']} <b>Добавление пользователя</b>\n"
        f"{format_divider()}"
        f"Введите ID пользователя, которого хотите добавить.\n\n"
        f"{EMOJI['info']} <b>Подсказка:</b>\n"
        f"Пользователь может узнать свой ID, написав боту /start"
    )
    
    await message.answer(text, reply_markup=keyboard, parse_mode='HTML')

@dp.message(F.text == f"{EMOJI['list']} Список пользователей")
async def list_users_button(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(f"{EMOJI['error']} У вас нет прав администратора.")
        return
    
    await bot.send_chat_action(message.chat.id, "typing")
    users = get_users_for_admin(message.from_user.id)
    
    if not users:
        await message.answer(f"{EMOJI['info']} У вас нет привязанных пользователей.")
        return
    
    text = f"{EMOJI['list']} <b>Ваши пользователи ({len(users)}):</b>\n"
    text += format_divider()
    
    for i, (user_id, day, time, msg) in enumerate(users, 1):
        try:
            user_info = await bot.get_chat(user_id)
            username = user_info.username or "нет"
            full_name = user_info.full_name or "Без имени"
        except:
            username = "недоступен"
            full_name = "Недоступен"
        
        text += f"<b>{i}. {escape_html(full_name)}</b>\n"
        text += f"   @{escape_html(username)} | ID: <code>{user_id}</code>\n"
        text += f"   {EMOJI['calendar']} {day} число, {EMOJI['clock']} {time}\n\n"
        
        # Разбиваем на части если слишком длинное
        if len(text) > 3500:
            await message.answer(text, parse_mode='HTML')
            text = ""
    
    if text:
        await message.answer(text, parse_mode='HTML')

@dp.message(F.text == f"{EMOJI['stats']} Статистика оплат")
async def payment_stats_button(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(f"{EMOJI['error']} У вас нет прав администратора.")
        return
    
    await bot.send_chat_action(message.chat.id, "typing")
    stats = get_payment_stats(message.from_user.id)
    
    text = f"{EMOJI['stats']} <b>Статистика оплат</b>\n"
    text += format_divider()
    
    text += f"{EMOJI['user']} <b>Пользователи:</b>\n"
    text += f"• Всего в системе: <b>{stats['total_users']}</b>\n\n"
    
    text += f"{EMOJI['money']} <b>Платежи:</b>\n"
    text += f"• Всего подтверждено: <b>{stats['confirmed']}</b>\n"
    text += f"• Ожидают подтверждения: <b>{stats['pending']}</b>\n"
    text += f"• За текущий месяц: <b>{stats['month_payments']}</b>\n"
    
    if stats['month_amount'] > 0:
        text += f"• Сумма за месяц: <b>{stats['month_amount']:.2f} ₽</b>\n"
    
    if stats['overdue'] > 0:
        text += f"{format_divider()}"
        text += f"\n{EMOJI['alert']} Просроченных: <b>{stats['overdue']}</b>\n"
    
    text += f"{format_divider()}"
    text += f"\n{EMOJI['stats']} <b>Аналитика:</b>\n"
    
    if stats['total_users'] > 0:
        # Процент оплативших в этом месяце
        month_rate = (stats['month_payments'] / stats['total_users'] * 100)
        text += f"{format_divider()}"
        text += f"• Оплатили в этом месяце: <b>{month_rate:.1f}%</b>\n"
    
    if stats['confirmed'] + stats['pending'] > 0:
        success_rate = (stats['confirmed'] / (stats['confirmed'] + stats['pending']) * 100)
        text += f"{format_divider()}"
        text += f"• Успешность платежей: <b>{success_rate:.1f}%</b>\n"
    
    await message.answer(text, parse_mode='HTML')

@dp.message(F.text == f"{EMOJI['remove']} Удалить пользователя")
async def remove_user_button(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer(f"{EMOJI['error']} У вас нет прав администратора.")
        return
    
    users = get_users_for_admin(message.from_user.id)
    
    if not users:
        await message.answer(f"{EMOJI['info']} У вас нет привязанных пользователей.")
        return
    
    await state.set_state(AdminStates.waiting_unlink_user)
    
    text = f"{EMOJI['remove']} <b>Удаление пользователя</b>\n"
    text += format_divider()
    
    for user_id, _, _, _ in users:
        try:
            user_info = await bot.get_chat(user_id)
            name = user_info.full_name or "Без имени"
            text += f"• <code>{user_id}</code> - {escape_html(name)}\n"
        except:
            text += f"• <code>{user_id}</code> - Недоступен\n"
    
    text += f"{format_divider()}"
    text += f"\n{EMOJI['info']} Введите ID пользователя для удаления:"
    
    keyboard = get_cancel_keyboard()
    await message.answer(text, reply_markup=keyboard, parse_mode='HTML')

@dp.message(F.text == f"{EMOJI['search']} Неоплатившие")
async def unpaid_users_button(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(f"{EMOJI['error']} У вас нет прав администратора.")
        return
    
    await bot.send_chat_action(message.chat.id, "typing")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Пользователи без платежей в текущем месяце
    current_month = datetime.now().replace(day=1)
    cursor.execute('''
        SELECT DISTINCT u.user_id 
        FROM user_admin_links u
        LEFT JOIN payments p ON u.user_id = p.user_id 
                             AND u.admin_id = p.admin_id 
                             AND p.payment_date >= ?
                             AND p.confirmed = TRUE
        WHERE u.admin_id = ? AND p.user_id IS NULL
        ORDER BY u.user_id
    ''', (current_month, message.from_user.id))
    
    unpaid_users = cursor.fetchall()
    conn.close()
    
    if not unpaid_users:
        await message.answer(f"{EMOJI['success']} Все пользователи оплатили в этом месяце!")
        return
    
    text = f"{EMOJI['search']} <b>Не оплатили в {datetime.now().strftime('%B %Y')}:</b>\n"
    text += format_divider()
    
    for i, (user_id,) in enumerate(unpaid_users, 1):
        try:
            user_info = await bot.get_chat(user_id)
            name = user_info.full_name or "Без имени"
            username = user_info.username
            text += f"{i}. {format_user_info(user_id, name, username)}\n\n"
        except:
            text += f"{i}. {EMOJI['user']} Недоступен (ID: <code>{user_id}</code>)\n\n"
    
    text += f"{format_divider()}"
    text += f"{EMOJI['info']} Всего: <b>{len(unpaid_users)}</b> пользователей"
    
    await message.answer(text, parse_mode='HTML')

@dp.message(F.text == f"{EMOJI['alert']} Просроченные")
async def overdue_payments_button(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(f"{EMOJI['error']} У вас нет прав администратора.")
        return
    
    await bot.send_chat_action(message.chat.id, "typing")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT DISTINCT user_id, due_date
        FROM pending_payments 
        WHERE admin_id = ? AND due_date <= ?
        ORDER BY due_date
    ''', (message.from_user.id, datetime.now()))
    
    overdue = cursor.fetchall()
    conn.close()
    
    if not overdue:
        await message.answer(f"{EMOJI['success']} Нет просроченных платежей!")
        return
    
    text = f"{EMOJI['alert']} <b>Просроченные платежи:</b>\n"
    text += format_divider()
    
    for user_id, due_date in overdue:
        try:
            user_info = await bot.get_chat(user_id)
            name = user_info.full_name or "Без имени"
            username = user_info.username
        except:
            name = "Недоступен"
            username = None
        
        due_dt = datetime.fromisoformat(due_date.replace('Z', '+00:00') if 'Z' in due_date else due_date)
        days_overdue = (datetime.now() - due_dt).days
        
        text += f"{format_user_info(user_id, name, username)}\n"
        text += f"{EMOJI['clock']} Просрочка: <b>{days_overdue} дн.</b>\n\n"
    
    text += f"{format_divider()}"
    text += f"{EMOJI['warning']} Всего просрочено: <b>{len(overdue)}</b>"
    
    await message.answer(text, parse_mode='HTML')

@dp.message(F.text == f"{EMOJI['check']} Подтвердить оплаты")
async def confirm_payments_button(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(f"{EMOJI['error']} У вас нет прав администратора.")
        return
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Получаем неподтвержденные платежи
    cursor.execute('''
        SELECT user_id, payment_date 
        FROM payments 
        WHERE admin_id = ? AND confirmed = FALSE
        ORDER BY payment_date DESC
        LIMIT 10
    ''', (message.from_user.id,))
    
    pending = cursor.fetchall()
    
    if not pending:
        await message.answer(f"{EMOJI['success']} Нет платежей, ожидающих подтверждения!")
        return
    
    text = f"{EMOJI['check']} <b>Ожидают подтверждения:</b>\n"
    text += format_divider()
    
    for user_id, payment_date in pending:
        try:
            user_info = await bot.get_chat(user_id)
            name = user_info.full_name or "Без имени"
            username = user_info.username
        except:
            name = "Недоступен"
            username = None
        
        text += f"{format_user_info(user_id, name, username)}\n"
        text += f"{EMOJI['calendar']} Дата: <b>{payment_date}</b>\n\n"
    
    text += f"{EMOJI['info']} Используйте кнопки в уведомлениях для подтверждения."
    
    conn.close()
    await message.answer(text, parse_mode='HTML')

@dp.message(F.text == f"{EMOJI['cancel']} Отмена")
async def cancel_button(message: Message, state: FSMContext):
    await state.clear()
    
    if is_admin(message.from_user.id):
        keyboard = get_admin_keyboard()
        await message.answer(f"{EMOJI['info']} Операция отменена.", reply_markup=keyboard)
    else:
        keyboard = get_user_keyboard()
        await message.answer(f"{EMOJI['info']} Операция отменена.", reply_markup=keyboard)

# Обработчики callback'ов для настроек
@dp.callback_query(F.data == "change_alias")
async def change_alias_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer(f"{EMOJI['error']} У вас нет прав администратора.", show_alert=True)
        return
    
    await state.set_state(AdminStates.waiting_alias)
    await callback.message.edit_text(
        f"{EMOJI['user']} <b>Изменение псевдонима</b>\n\n"
        f"Введите новый псевдоним, который будут видеть пользователи.\n\n"
        f"{EMOJI['info']} Максимум 50 символов",
        parse_mode='HTML'
    )

@dp.callback_query(F.data == "change_default_message")
async def change_default_message_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer(f"{EMOJI['error']} У вас нет прав администратора.", show_alert=True)
        return
    
    await state.set_state(AdminStates.waiting_default_message)
    await callback.message.edit_text(
        f"{EMOJI['chat']} <b>Изменение сообщения по умолчанию</b>\n\n"
        f"Введите новое сообщение, которое будет отправляться пользователям при напоминании об оплате.\n\n"
        f"{EMOJI['info']} Максимум 500 символов",
        parse_mode='HTML'
    )

@dp.callback_query(F.data == "toggle_notifications")
async def toggle_notifications_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(f"{EMOJI['error']} У вас нет прав администратора.", show_alert=True)
        return
    
    # Получаем текущий статус
    _, _, show_notifications = get_admin_settings(callback.from_user.id)
    
    # Переключаем
    new_status = not show_notifications
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE admin_settings SET show_notifications = ? WHERE admin_id = ?
    ''', (new_status, callback.from_user.id))
    conn.commit()
    conn.close()
    
    # Обновляем сообщение с настройками
    alias, default_message, _ = get_admin_settings(callback.from_user.id)
    
    text = (
        f"{EMOJI['settings']} <b>Настройки администратора</b>\n"
        f"{format_divider()}"
        f"{EMOJI['user']} <b>Псевдоним:</b> {escape_html(alias)}\n"
        f"{EMOJI['bell']} <b>Уведомления:</b> {'Включены' if new_status else 'Выключены'}\n\n"
        f"{EMOJI['chat']} <b>Сообщение по умолчанию:</b>\n"
        f"<i>{escape_html(default_message)}</i>\n\n"
        f"Выберите, что хотите изменить:"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{EMOJI['user']} Изменить псевдоним", callback_data="change_alias")],
        [InlineKeyboardButton(text=f"{EMOJI['chat']} Изменить сообщение", callback_data="change_default_message")],
        [InlineKeyboardButton(
            text=f"{EMOJI['bell']} {'Выключить' if new_status else 'Включить'} уведомления", 
            callback_data="toggle_notifications"
        )]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode='HTML')
    await callback.answer(
        f"{EMOJI['success']} Уведомления {'включены' if new_status else 'выключены'}!",
        show_alert=True
    )

@dp.callback_query(F.data.startswith("start_chat_"))
async def start_chat_with_user_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer(f"{EMOJI['error']} У вас нет прав администратора.", show_alert=True)
        return
    
    user_id = int(callback.data.split("_")[2])
    
    # Проверяем что чат все еще активен
    if not is_chat_active(user_id, callback.from_user.id):
        await callback.answer(f"{EMOJI['error']} Чат больше не активен.", show_alert=True)
        return
    
    await state.set_state(AdminStates.chatting_with_user)
    await state.update_data(chat_user_id=user_id)
    
    try:
        user_info = await bot.get_chat(user_id)
        user_name = user_info.full_name or "Без имени"
        username = user_info.username
    except:
        user_name = "Недоступен"
        username = None
    
    await callback.message.edit_text(
        f"{EMOJI['chat']} <b>Чат с пользователем</b>\n\n"
        f"{format_user_info(user_id, user_name, username)}\n\n"
        f"{EMOJI['info']} Отправляйте сообщения для связи с пользователем.",
        parse_mode='HTML'
    )
    
    keyboard = get_back_keyboard()
    await bot.send_message(
        callback.from_user.id,
        "Используйте клавиатуру для навигации:",
        reply_markup=keyboard
    )
    
    # Уведомляем пользователя
    admin_alias, _, _ = get_admin_settings(callback.from_user.id)
    await bot.send_message(
        user_id,
        f"{EMOJI['admin']} <b>{escape_html(admin_alias)}</b> присоединился к чату",
        parse_mode='HTML'
    )

# Обработчики состояний для изменения настроек
@dp.message(StateFilter(AdminStates.waiting_alias))
async def process_alias_change(message: Message, state: FSMContext):
    if message.text == f"{EMOJI['cancel']} Отмена":
        await cancel_button(message, state)
        return
    
    new_alias = message.text.strip()
    if len(new_alias) > 50:
        await message.answer(f"{EMOJI['error']} Псевдоним слишком длинный. Максимум 50 символов.")
        return
    
    if len(new_alias) < 2:
        await message.answer(f"{EMOJI['error']} Псевдоним слишком короткий. Минимум 2 символа.")
        return
    
    update_admin_alias(message.from_user.id, new_alias)
    await state.clear()
    
    keyboard = get_admin_keyboard()
    await message.answer(
        f"{EMOJI['success']} <b>Псевдоним изменен!</b>\n\n"
        f"Новый псевдоним: <b>{escape_html(new_alias)}</b>",
        reply_markup=keyboard,
        parse_mode='HTML'
    )

@dp.message(StateFilter(AdminStates.waiting_default_message))
async def process_default_message_change(message: Message, state: FSMContext):
    if message.text == f"{EMOJI['cancel']} Отмена":
        await cancel_button(message, state)
        return
    
    new_message = message.text.strip()
    if len(new_message) > 500:
        await message.answer(f"{EMOJI['error']} Сообщение слишком длинное. Максимум 500 символов.")
        return
    
    if len(new_message) < 10:
        await message.answer(f"{EMOJI['error']} Сообщение слишком короткое. Минимум 10 символов.")
        return
    
    update_admin_default_message(message.from_user.id, new_message)
    await state.clear()
    
    keyboard = get_admin_keyboard()
    await message.answer(
        f"{EMOJI['success']} <b>Сообщение по умолчанию изменено!</b>\n\n"
        f"Новое сообщение:\n<i>{escape_html(new_message)}</i>",
        reply_markup=keyboard,
        parse_mode='HTML'
    )

# Обработчики состояний для добавления пользователей
@dp.message(StateFilter(AdminStates.waiting_user_id))
async def process_user_id(message: Message, state: FSMContext):
    if message.text == f"{EMOJI['cancel']} Отмена":
        await cancel_button(message, state)
        return
    
    try:
        user_id = int(message.text.strip())
        
        # Проверяем что пользователь не добавляет сам себя
        if user_id == message.from_user.id:
            await message.answer(f"{EMOJI['error']} Вы не можете добавить самого себя!")
            return
        
        # Проверяем что пользователь еще не добавлен
        existing_admin = get_admin_for_user(user_id)
        if existing_admin:
            if existing_admin == message.from_user.id:
                await message.answer(f"{EMOJI['error']} Этот пользователь уже привязан к вам!")
            else:
                await message.answer(f"{EMOJI['error']} Этот пользователь уже привязан к другому администратору!")
            return
        
        # Проверяем существование пользователя
        try:
            user_info = await bot.get_chat(user_id)
            user_name = user_info.full_name or "Без имени"
            username = user_info.username
        except:
            await message.answer(
                f"{EMOJI['error']} Пользователь с ID <code>{user_id}</code> не найден.\n\n"
                f"{EMOJI['info']} Убедитесь, что пользователь начал диалог с ботом.",
                parse_mode='HTML'
            )
            return
        
        await state.update_data(user_id=user_id, user_name=user_name)
        await state.set_state(AdminStates.waiting_day)
        
        keyboard = get_cancel_keyboard()
        await message.answer(
            f"{EMOJI['success']} Пользователь найден!\n\n"
            f"{format_user_info(user_id, user_name, username)}\n\n"
            f"{EMOJI['calendar']} Введите день месяца для отправки напоминания (1-31):",
            reply_markup=keyboard,
            parse_mode='HTML'
        )
    except ValueError:
        await message.answer(f"{EMOJI['error']} Неверный формат ID. Введите число.")

@dp.message(StateFilter(AdminStates.waiting_day))
async def process_day(message: Message, state: FSMContext):
    if message.text == f"{EMOJI['cancel']} Отмена":
        await cancel_button(message, state)
        return
    
    try:
        day = int(message.text.strip())
        if not 1 <= day <= 31:
            await message.answer(f"{EMOJI['error']} День должен быть от 1 до 31.")
            return
        
        await state.update_data(day=day)
        await state.set_state(AdminStates.waiting_time)
        
        keyboard = get_cancel_keyboard()
        await message.answer(
            f"{EMOJI['success']} День: <b>{day} число</b>\n\n"
            f"{EMOJI['clock']} Введите время отправки в формате ЧЧ:ММ\n"
            f"Например: 10:00, 14:30, 09:15",
            reply_markup=keyboard,
            parse_mode='HTML'
        )
    except ValueError:
        await message.answer(f"{EMOJI['error']} Неверный формат дня. Введите число от 1 до 31.")

@dp.message(StateFilter(AdminStates.waiting_time))
async def process_time(message: Message, state: FSMContext):
    if message.text == f"{EMOJI['cancel']} Отмена":
        await cancel_button(message, state)
        return
    
    time_str = message.text.strip()
    
    # Проверяем формат времени
    try:
        datetime.strptime(time_str, "%H:%M")
        await state.update_data(time=time_str)
        
        # Получаем дефолтное сообщение админа
        _, default_message, _ = get_admin_settings(message.from_user.id)
        
        # Предлагаем выбор
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"{EMOJI['chat']} Ввести своё сообщение", 
                callback_data="enter_custom_msg"
            )],
            [InlineKeyboardButton(
                text=f"{EMOJI['success']} Использовать сообщение по умолчанию", 
                callback_data="use_default_msg"
            )]
        ])
        
        data = await state.get_data()
        
        text = (
            f"{EMOJI['success']} Время: <b>{time_str}</b>\n\n"
            f"{EMOJI['info']} <b>Итоговые настройки:</b>\n"
            f"• Пользователь: <b>{escape_html(data.get('user_name', 'Неизвестен'))}</b>\n"
            f"• День: <b>{data.get('day')} число</b>\n"
            f"• Время: <b>{time_str}</b>\n\n"
            f"{EMOJI['chat']} <b>Выберите сообщение для напоминания:</b>\n\n"
            f"<b>Ваше сообщение по умолчанию:</b>\n"
            f"<i>{escape_html(default_message)}</i>"
        )
        
        await message.answer(text, reply_markup=keyboard, parse_mode='HTML')
    except ValueError:
        await message.answer(
            f"{EMOJI['error']} Неверный формат времени.\n\n"
            f"Используйте формат ЧЧ:ММ\n"
            f"Примеры: 09:00, 14:30, 23:45"
        )

@dp.message(StateFilter(AdminStates.waiting_message))
async def process_message(message: Message, state: FSMContext):
    if message.text == f"{EMOJI['cancel']} Отмена":
        await cancel_button(message, state)
        return
    
    payment_message = message.text.strip()
    
    if len(payment_message) < 5:
        await message.answer(f"{EMOJI['error']} Сообщение слишком короткое. Минимум 5 символов.")
        return
    
    if len(payment_message) > 500:
        await message.answer(f"{EMOJI['error']} Сообщение слишком длинное. Максимум 500 символов.")
        return
    
    data = await state.get_data()
    user_id = data['user_id']
    day = data['day']
    time = data['time']
    
    await complete_user_addition(
        message.from_user.id, user_id, day, time, payment_message, state, message
    )

async def complete_user_addition(admin_id: int, user_id: int, day: int, time: str, payment_message: str, state: FSMContext, message: Message):
    """Завершает добавление пользователя"""
    # Сохраняем в базу данных
    add_user_to_admin(user_id, admin_id, day, time, payment_message)
    
    # Добавляем задачу в планировщик
    hour, minute = map(int, time.split(':'))
    scheduler.add_job(
        send_payment_reminder,
        CronTrigger(day=day, hour=hour, minute=minute),
        args=[user_id, admin_id, payment_message],
        id=f"payment_{user_id}_{admin_id}",
        replace_existing=True
    )
    
    await state.clear()
    
    try:
        user_info = await bot.get_chat(user_id)
        user_name = user_info.full_name or "Без имени"
        username = user_info.username
    except:
        user_name = "Недоступен"
        username = None
    
    admin_alias, _, _ = get_admin_settings(admin_id)
    
    keyboard = get_admin_keyboard()
    
    text = (
        f"{EMOJI['success']} <b>Пользователь успешно добавлен!</b>\n"
        f"{format_divider()}"
        f"{format_user_info(user_id, user_name, username)}\n\n"
        f"{format_payment_info(day, time, payment_message)}\n\n"
        f"{EMOJI['rocket']} Первое напоминание будет отправлено по расписанию."
    )
    
    await message.answer(text, reply_markup=keyboard, parse_mode='HTML')
    
    # Уведомляем пользователя
    try:
        next_reminder = calculate_next_reminder(day, time)
        
        text = (
            f"{EMOJI['bell']} <b>Вы добавлены в систему напоминаний!</b>\n"
            f"{format_divider()}"
            f"{format_divider()}"
            f"{EMOJI['admin']} <b>Администратор:</b> {escape_html(admin_alias)}\n"
            f"{EMOJI['calendar']} <b>День напоминания:</b> {day} число\n"
            f"{EMOJI['clock']} <b>Время:</b> {time}\n\n"
        )
        
        if next_reminder:
            text += f"{EMOJI['rocket']} <b>Первое напоминание:</b> {format_date(next_reminder)}\n\n"
        
        text += f"{EMOJI['info']} Используйте /start для просмотра вашего статуса."
        
        await bot.send_message(
            user_id,
            text,
            reply_markup=get_user_keyboard(),
            parse_mode='HTML'
        )
    except:
        await message.answer(
            f"{EMOJI['warning']} Не удалось уведомить пользователя.\n"
            f"Возможно, он не начал диалог с ботом."
        )

@dp.message(StateFilter(AdminStates.waiting_unlink_user))
async def process_unlink_user(message: Message, state: FSMContext):
    if message.text == f"{EMOJI['cancel']} Отмена":
        await cancel_button(message, state)
        return
    
    try:
        user_id = int(message.text.strip())
        
        # Проверяем что пользователь привязан к этому админу
        users = get_users_for_admin(message.from_user.id)
        user_ids = [u[0] for u in users]
        
        if user_id not in user_ids:
            await message.answer(f"{EMOJI['error']} Этот пользователь не привязан к вам!")
            return
        
        remove_user_from_admin(user_id, message.from_user.id)
        
        # Удаляем задачу из планировщика
        try:
            scheduler.remove_job(f"payment_{user_id}_{message.from_user.id}")
        except:
            pass
        
        # Завершаем активный чат если есть
        if is_chat_active(user_id, message.from_user.id):
            end_chat_session(user_id, message.from_user.id)
        
        await state.clear()
        
        keyboard = get_admin_keyboard()
        await message.answer(
            f"{EMOJI['success']} Пользователь <code>{user_id}</code> успешно удален из системы.",
            reply_markup=keyboard,
            parse_mode='HTML'
        )
        
        # Уведомляем пользователя
        try:
            await bot.send_message(
                user_id,
                f"{EMOJI['warning']} Вы были удалены из системы напоминаний об оплате.\n\n"
                f"Вы больше не будете получать напоминания.",
                reply_markup=get_user_keyboard()
            )
        except:
            pass
            
    except ValueError:
        await message.answer(f"{EMOJI['error']} Неверный формат ID. Введите число.")

# Обработчик чата пользователя с админом
@dp.message(StateFilter(UserStates.chatting_with_admin))
async def forward_to_admin(message: Message, state: FSMContext):
    if message.text == f"{EMOJI['back']} Назад":
        await back_button(message, state)
        return
    
    data = await state.get_data()
    admin_id = data['admin_id']
    
    user_info = message.from_user
    admin_alias, _, show_notifications = get_admin_settings(admin_id)
    
    if not show_notifications:
        # Если уведомления выключены, просто подтверждаем отправку
        await message.answer(f"{EMOJI['success']} Сообщение отправлено")
        return
    
    try:
        # Обработка различных типов сообщений
        if message.text:
            text = (
                f"{EMOJI['chat']} <b>Новое сообщение</b>\n"
                f"{format_divider()}"
                f"{format_user_info(user_info.id, user_info.full_name, user_info.username)}\n\n"
                f"{EMOJI['chat']} <b>Текст:</b>\n{escape_html(message.text)}\n\n"
                f"{EMOJI['info']} Используйте /chat_{user_info.id} для ответа"
            )
            await bot.send_message(admin_id, text, parse_mode='HTML')
            add_message_to_history(user_info.id, admin_id, 'text', message.text)
        
        elif message.photo:
            caption = message.caption or ""
            text = (
                f"{EMOJI['chat']} <b>Новое фото</b>\n"
                f"{format_divider()}"
                f"{format_user_info(user_info.id, user_info.full_name, user_info.username)}\n\n"
                f"{EMOJI['chat']} Подпись: {escape_html(caption)}" if caption else text
            )
            await bot.send_photo(
                admin_id,
                message.photo[-1].file_id,
                caption=f"{text}\n\n{EMOJI['chat']} Подпись: {escape_html(caption)}" if caption else text,
                parse_mode='HTML'
            )
            add_message_to_history(user_info.id, admin_id, 'photo', caption)
        
        elif message.video:
            caption = message.caption or ""
            text = (
                f"{EMOJI['chat']} <b>Новое видео</b>\n"
                f"{format_divider()}"
                f"{format_user_info(user_info.id, user_info.full_name, user_info.username)}\n\n"
                f"{EMOJI['chat']} Подпись: {escape_html(caption)}" if caption else text
            )
            await bot.send_video(
                admin_id,
                message.video.file_id,
                caption=f"{text}\n\n{EMOJI['chat']} Подпись: {escape_html(caption)}" if caption else text,
                parse_mode='HTML'
            )
            add_message_to_history(user_info.id, admin_id, 'video', caption)
        
        elif message.document:
            caption = message.caption or ""
            text = (
                f"{EMOJI['chat']} <b>Новый документ</b>\n"
                f"{format_divider()}"
                f"{format_user_info(user_info.id, user_info.full_name, user_info.username)}\n\n"
                f"{EMOJI['chat']} Подпись: {escape_html(caption)}" if caption else text
            )
            await bot.send_document(
                admin_id,
                message.document.file_id,
                caption=f"{text}\n\n{EMOJI['chat']} Подпись: {escape_html(caption)}" if caption else text,
                parse_mode='HTML'
            )
            add_message_to_history(user_info.id, admin_id, 'document', message.document.file_name)
        
        elif message.voice:
            text = (
                f"{EMOJI['chat']} <b>Голосовое сообщение</b>\n"
                f"{format_divider()}"
                f"{format_user_info(user_info.id, user_info.full_name, user_info.username)}\n\n"
                f"{EMOJI['chat']} Подпись: {escape_html(message.voice.caption)}" if message.voice.caption else text
            )
            await bot.send_voice(
                admin_id,
                message.voice.file_id,
                caption=text,
                parse_mode='HTML'
            )
            add_message_to_history(user_info.id, admin_id, 'voice', message.voice.caption)
        
        elif message.video_note:
            await bot.send_video_note(admin_id, message.video_note.file_id)
            text = (
                f"{EMOJI['chat']} <b>Видеосообщение от:</b>\n"
                f"{format_user_info(user_info.id, user_info.full_name, user_info.username)}\n\n"
                f"{EMOJI['chat']} Подпись: {escape_html(message.video_note.caption)}" if message.video_note.caption else text
            )
            await bot.send_message(admin_id, text, parse_mode='HTML')
            add_message_to_history(user_info.id, admin_id, 'video_note', message.video_note.caption)
        
        await message.answer(f"{EMOJI['success']} Сообщение доставлено")
    except Exception as e:
        await message.answer(f"{EMOJI['error']} Не удалось отправить сообщение. Попробуйте позже.")
        logging.error(f"Ошибка при пересылке сообщения админу: {e}")

# Обработчик чата админа с пользователем  
@dp.message(StateFilter(AdminStates.chatting_with_user))
async def forward_to_user(message: Message, state: FSMContext):
    if message.text == f"{EMOJI['back']} Назад":
        await back_button(message, state)
        return
    
    data = await state.get_data()
    user_id = data.get('chat_user_id')
    
    if not user_id:
        await message.answer(f"{EMOJI['error']} Ошибка: ID пользователя не найден.")
        return
    
    # Проверяем что чат все еще активен
    if not is_chat_active(user_id, message.from_user.id):
        await message.answer(
            f"{EMOJI['error']} Чат больше не активен.\n"
            f"Пользователь завершил сессию."
        )
        await state.clear()
        keyboard = get_admin_keyboard()
        await bot.send_message(
            message.from_user.id,
            "Возвращаемся в админ-панель:",
            reply_markup=keyboard
        )
        return
    
    admin_alias, _, _ = get_admin_settings(message.from_user.id)
    
    try:
        # Обработка различных типов сообщений
        if message.text:
            text = f"{EMOJI['admin']} <b>{escape_html(admin_alias)}:</b>\n\n{escape_html(message.text)}\n\n"
            await bot.send_message(user_id, text, parse_mode='HTML')
            add_message_to_history(message.from_user.id, user_id, 'text', message.text)
        
        elif message.photo:
            caption = message.caption or ""
            text = f"{EMOJI['admin']} <b>Фото от {escape_html(admin_alias)}</b>\n\n{escape_html(caption)}\n\n"
            await bot.send_photo(
                user_id,
                message.photo[-1].file_id,
                caption=f"{text}\n\n{escape_html(caption)}" if caption else text,
                parse_mode='HTML'
            )
            add_message_to_history(message.from_user.id, user_id, 'photo', caption)
        
        elif message.video:
            caption = message.caption or ""
            text = f"{EMOJI['admin']} <b>Видео от {escape_html(admin_alias)}</b>\n\n{escape_html(caption)}\n\n"
            await bot.send_video(
                user_id,
                message.video.file_id,
                caption=f"{text}\n\n{escape_html(caption)}" if caption else text,
                parse_mode='HTML'
            )
            add_message_to_history(message.from_user.id, user_id, 'video', caption)
        
        elif message.document:
            caption = message.caption or ""
            text = f"{EMOJI['admin']} <b>Документ от {escape_html(admin_alias)}</b>\n\n{escape_html(caption)}\n\n"
            await bot.send_document(
                user_id,
                message.document.file_id,
                caption=f"{text}\n\n{escape_html(caption)}" if caption else text,
                parse_mode='HTML'
            )
            add_message_to_history(message.from_user.id, user_id, 'document', message.document.file_name)
        
        elif message.voice:
            text = f"{EMOJI['admin']} <b>Голосовое от {escape_html(admin_alias)}</b>\n\n{escape_html(message.voice.caption)}\n\n"
            await bot.send_voice(
                user_id,
                message.voice.file_id,
                caption=text,
                parse_mode='HTML'
            )
            add_message_to_history(message.from_user.id, user_id, 'voice', message.voice.caption)
        
        elif message.video_note:
            await bot.send_video_note(user_id, message.video_note.file_id)
            text = f"{EMOJI['admin']} <b>Видеосообщение от {escape_html(admin_alias)}</b>\n\n{escape_html(message.video_note.caption)}\n\n"
            await bot.send_message(user_id, text, parse_mode='HTML')
            add_message_to_history(message.from_user.id, user_id, 'video_note', message.video_note.caption)
        
        await message.answer(f"{EMOJI['success']} Доставлено")
    except Exception as e:
        await message.answer(
            f"{EMOJI['error']} Не удалось отправить сообщение.\n"
            f"Возможно, пользователь заблокировал бота."
        )
        logging.error(f"Ошибка при пересылке сообщения пользователю: {e}")

# Функции для отправки напоминаний
async def send_payment_reminder(user_id: int, admin_id: int, message_text: str):
    keyboard = get_payment_confirmation_keyboard(admin_id)
    admin_alias, _, _ = get_admin_settings(admin_id)
    
    try:
        # Проверяем что пользователь все еще привязан к админу
        current_admin = get_admin_for_user(user_id)
        if current_admin != admin_id:
            logging.warning(f"Пользователь {user_id} больше не привязан к админу {admin_id}")
            return
        
        text = (
            f"{EMOJI['money']} <b>Напоминание об оплате!</b>\n"
            f"{format_divider()}"
            f"{escape_html(message_text)}\n\n"
            f"{format_divider()}"
            f"{escape_html(message_text)}\n\n"
            f"{EMOJI['admin']} От: <b>{escape_html(admin_alias)}</b>\n"
            f"{EMOJI['clock']} Время: <b>{format_date(datetime.now())}</b>"
        )
        
        sent_message = await bot.send_message(
            user_id,
            text,
            reply_markup=keyboard,
            parse_mode='HTML'
        )
        
        # Сохраняем информацию о ожидающем платеже
        conn = get_db_connection()
        cursor = conn.cursor()
        due_date = datetime.now() + timedelta(days=PAYMENT_TIMEOUT_DAYS)
        cursor.execute('''
            INSERT INTO pending_payments (user_id, admin_id, message_id, due_date)
            VALUES (?, ?, ?, ?)
        ''', (user_id, admin_id, sent_message.message_id, due_date))
        conn.commit()
        conn.close()
        
        # Планируем проверку просрочки
        scheduler.add_job(
            check_overdue_payment,
            'date',
            run_date=due_date,
            args=[user_id, admin_id],
            id=f"overdue_{user_id}_{admin_id}_{sent_message.message_id}"
        )
        
        # Уведомляем админа об отправке
        await bot.send_message(
            admin_id,
            f"{EMOJI['success']} Напоминание отправлено пользователю ID: <code>{user_id}</code>",
            parse_mode='HTML'
        )
        
    except Exception as e:
        logging.error(f"Ошибка отправки напоминания пользователю {user_id}: {e}")
        
        # Уведомляем админа об ошибке
        try:
            await bot.send_message(
                admin_id,
                f"{EMOJI['error']} Не удалось отправить напоминание пользователю ID: <code>{user_id}</code>\n"
                f"Возможно, пользователь заблокировал бота.",
                parse_mode='HTML'
            )
        except:
            pass

async def check_overdue_payment(user_id: int, admin_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Проверяем, есть ли неподтвержденные платежи
    cursor.execute('''
        SELECT COUNT(*) FROM pending_payments 
        WHERE user_id = ? AND admin_id = ? AND due_date <= ?
    ''', (user_id, admin_id, datetime.now()))
    
    overdue_count = cursor.fetchone()[0]
    
    if overdue_count > 0:
        try:
            user_info = await bot.get_chat(user_id)
            user_name = user_info.full_name or "Без имени"
            username = user_info.username
        except:
            user_name = "Недоступен"
            username = None
        
        _, _, show_notifications = get_admin_settings(admin_id)
        
        if show_notifications:
            await bot.send_message(
                admin_id,
                f"{EMOJI['alert']} <b>ПРОСРОЧКА ПЛАТЕЖА!</b>\n"
                f"{format_divider()}"
                f"{format_user_info(user_id, user_name, username)}\n\n"
                f"{EMOJI['clock']} Просрочено на: <b>{PAYMENT_TIMEOUT_DAYS} дн.</b>\n\n"
                f"{EMOJI['info']} Свяжитесь с пользователем для уточнения.",
                parse_mode='HTML'
            )
    
    conn.close()

# Обработка подтверждения оплаты
@dp.callback_query(F.data.startswith("paid_"))
async def payment_confirmation(callback: CallbackQuery):
    admin_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    # Проверяем что платеж еще не был подтвержден
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT COUNT(*) FROM payments 
        WHERE user_id = ? AND admin_id = ? 
        AND DATE(payment_date) = DATE('now') 
        AND confirmed = FALSE
    ''', (user_id, admin_id))
    
    existing_payment = cursor.fetchone()[0]
    
    if existing_payment > 0:
        await callback.answer(
            f"{EMOJI['warning']} Вы уже отправили подтверждение сегодня. Ожидайте ответа администратора.",
            show_alert=True
        )
        return
    
    # Отмечаем как оплаченное (но неподтвержденное)
    cursor.execute('''
        INSERT INTO payments (user_id, admin_id, payment_date, confirmed)
        VALUES (?, ?, DATE('now'), FALSE)
    ''', (user_id, admin_id))
    conn.commit()
    conn.close()
    
    # Убираем кнопку
    await callback.message.edit_reply_markup()
    
    # Обновляем сообщение
    new_text = callback.message.text + f"\n\n{EMOJI['success']} <b>Подтверждение отправлено!</b>\nОжидайте ответа администратора."
    await callback.message.edit_text(new_text, parse_mode='HTML')
    
    # Создаем кнопки для админа
    keyboard = get_admin_payment_confirmation_keyboard(user_id)
    
    try:
        user_info = await bot.get_chat(user_id)
        user_name = user_info.full_name or "Без имени"
        username = user_info.username
    except:
        user_name = "Недоступен"
        username = None
    
    admin_alias, _, show_notifications = get_admin_settings(admin_id)
    
    if show_notifications:
        await bot.send_message(
            admin_id,
            f"{EMOJI['money']} <b>Новое подтверждение оплаты!</b>\n"
            f"{format_divider()}"
            f"{format_user_info(user_id, user_name, username)}\n\n"
            f"{EMOJI['calendar']} Дата: <b>{datetime.now().strftime('%d.%m.%Y')}</b>\n"
            f"{EMOJI['clock']} Время: <b>{datetime.now().strftime('%H:%M')}</b>\n\n"
            f"Подтвердите или отклоните платеж:",
            reply_markup=keyboard,
            parse_mode='HTML'
        )
    
    await callback.answer(f"{EMOJI['success']} Подтверждение отправлено администратору!")

# Подтверждение/отклонение админом
@dp.callback_query(F.data.startswith("confirm_"))
async def confirm_payment(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(f"{EMOJI['error']} У вас нет прав администратора.", show_alert=True)
        return
    
    user_id = int(callback.data.split("_")[1])
    
    # Подтверждаем платеж
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE payments SET confirmed = TRUE 
        WHERE user_id = ? AND admin_id = ? AND confirmed = FALSE
        AND DATE(payment_date) = DATE('now')
    ''', (user_id, callback.from_user.id))
    
    updated_rows = cursor.rowcount
    
    if updated_rows == 0:
        await callback.answer(
            f"{EMOJI['warning']} Платеж уже был обработан или не найден.",
            show_alert=True
        )
        return
    
    # Удаляем ожидающие платежи
    cursor.execute('''
        DELETE FROM pending_payments 
        WHERE user_id = ? AND admin_id = ?
    ''', (user_id, callback.from_user.id))
    
    conn.commit()
    conn.close()
    
    # Обновляем сообщение
    await callback.message.edit_text(
        callback.message.text + f"\n\n{EMOJI['success']} <b>ПЛАТЕЖ ПОДТВЕРЖДЕН</b>\n{format_date(datetime.now())}",
        parse_mode='HTML'
    )
    
    admin_alias, _, _ = get_admin_settings(callback.from_user.id)
    
    try:
        await bot.send_message(
            user_id,
            f"{EMOJI['success']} <b>Ваш платеж подтвержден!</b>\n\n"
            f"{EMOJI['admin']} Администратор: <b>{escape_html(admin_alias)}</b>\n"
            f"{EMOJI['calendar']} Дата: <b>{datetime.now().strftime('%d.%m.%Y %H:%M')}</b>\n\n"
            f"Спасибо за своевременную оплату!",
            parse_mode='HTML'
        )
    except:
        pass
    
    await callback.answer(f"{EMOJI['success']} Платеж подтвержден!")

@dp.callback_query(F.data.startswith("reject_"))
async def reject_payment(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(f"{EMOJI['error']} У вас нет прав администратора.", show_alert=True)
        return
    
    user_id = int(callback.data.split("_")[1])
    
    # Удаляем неподтвержденный платеж
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM payments 
        WHERE user_id = ? AND admin_id = ? AND confirmed = FALSE
        AND DATE(payment_date) = DATE('now')
    ''', (user_id, callback.from_user.id))
    
    deleted_rows = cursor.rowcount
    conn.commit()
    conn.close()
    
    if deleted_rows == 0:
        await callback.answer(
            f"{EMOJI['warning']} Платеж уже был обработан или не найден.",
            show_alert=True
        )
        return
    
    # Обновляем сообщение
    await callback.message.edit_text(
        callback.message.text + f"\n\n{EMOJI['error']} <b>ПЛАТЕЖ ОТКЛОНЕН</b>\n{format_date(datetime.now())}",
        parse_mode='HTML'
    )
    
    admin_alias, _, _ = get_admin_settings(callback.from_user.id)
    
    try:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"{EMOJI['chat']} Связаться с администратором",
                callback_data=f"contact_admin_{callback.from_user.id}"
            )]
        ])
        
        await bot.send_message(
            user_id,
            f"{EMOJI['error']} <b>Ваш платеж был отклонен</b>\n\n"
            f"{EMOJI['admin']} Администратор: <b>{escape_html(admin_alias)}</b>\n"
            f"{EMOJI['calendar']} Дата: <b>{datetime.now().strftime('%d.%m.%Y %H:%M')}</b>\n\n"
            f"{EMOJI['info']} Свяжитесь с администратором для уточнения деталей.",
            reply_markup=keyboard,
            parse_mode='HTML'
        )
    except:
        pass
    
    await callback.answer(f"{EMOJI['info']} Платеж отклонен")

# Обработчик кнопки связи с админом из отклоненного платежа
@dp.callback_query(F.data.startswith("contact_admin_"))
async def contact_admin_from_rejection(callback: CallbackQuery, state: FSMContext):
    admin_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    
    # Проверяем что пользователь привязан к этому админу
    current_admin = get_admin_for_user(user_id)
    if current_admin != admin_id:
        await callback.answer(
            f"{EMOJI['error']} Вы больше не привязаны к этому администратору.",
            show_alert=True
        )
        return
    
    # Начинаем чат
    start_chat_session(user_id, admin_id)
    await state.set_state(UserStates.chatting_with_admin)
    await state.update_data(admin_id=admin_id)
    
    keyboard = get_back_keyboard()
    admin_alias, _, _ = get_admin_settings(admin_id)
    
    await callback.message.answer(
        f"{EMOJI['chat']} <b>Чат с {escape_html(admin_alias)}</b>\n\n"
        f"Отправьте сообщение для уточнения деталей по платежу.",
        reply_markup=keyboard,
        parse_mode='HTML'
    )
    
    # Уведомляем админа
    try:
        user_info = await bot.get_chat(user_id)
        user_name = user_info.full_name or "Без имени"
        username = user_info.username
        
        await bot.send_message(
            admin_id,
            f"{EMOJI['bell']} <b>Пользователь хочет уточнить детали отклоненного платежа</b>\n\n"
            f"{format_user_info(user_id, user_name, username)}\n\n"
            f"Используйте /chat_{user_id} для ответа",
            parse_mode='HTML'
        )
    except:
        pass
    
    await callback.answer()

# Обработчики callback'ов для выбора сообщения
@dp.callback_query(F.data == "use_default_msg")
async def use_default_message_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer(f"{EMOJI['error']} У вас нет прав администратора.", show_alert=True)
        return
    
    data = await state.get_data()
    user_id = data['user_id']
    day = data['day']
    time = data['time']
    
    # Получаем сообщение по умолчанию
    _, default_message, _ = get_admin_settings(callback.from_user.id)
    
    # Завершаем добавление пользователя
    await complete_user_addition(
        callback.from_user.id,
        user_id,
        day,
        time,
        default_message,
        state,
        callback.message
    )

@dp.callback_query(F.data == "enter_custom_msg")
async def enter_custom_message_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer(f"{EMOJI['error']} У вас нет прав администратора.", show_alert=True)
        return
    
    await state.set_state(AdminStates.waiting_message)
    keyboard = get_cancel_keyboard()
    
    await callback.message.edit_text(
        f"{EMOJI['chat']} <b>Введите текст напоминания</b>\n\n"
        f"Это сообщение будет отправляться пользователю каждый месяц.\n\n"
        f"{EMOJI['info']} Максимум 500 символов",
        parse_mode='HTML'
    )
    
    await bot.send_message(
        callback.from_user.id,
        "Введите текст:",
        reply_markup=keyboard
    )

async def setup_bot_commands():
    """Настройка команд бота в меню"""
    commands = [
        BotCommand(command="start", description=f"{EMOJI['rocket']} Начать работу с ботом"),
    ]
    
    # Добавляем команды для админов
    for admin_id in ADMINS:
        try:
            active_chats = get_active_chats_for_admin(admin_id)
            if active_chats:
                admin_commands = commands.copy()
                for user_id in active_chats[:10]:  # Максимум 10 команд
                    try:
                        user_info = await bot.get_chat(user_id)
                        user_name = user_info.full_name or "ID " + str(user_id)
                        user_name = user_name[:30]  # Ограничиваем длину
                        admin_commands.append(
                            BotCommand(
                                command=f"chat_{user_id}",
                                description=f"{EMOJI['chat']} Чат с {user_name}"
                            )
                        )
                    except:
                        pass
                
                await bot.set_my_commands(admin_commands, scope={'type': 'chat', 'chat_id': admin_id})
        except:
            pass
    
    await bot.set_my_commands(commands)

# Обработчик всех остальных сообщений
@dp.message()
async def handle_unknown_message(message: Message, state: FSMContext):
    current_state = await state.get_state()
    
    # Если пользователь в процессе какого-то действия, игнорируем
    if current_state:
        return
    
    # Для неизвестных команд
    if message.text and message.text.startswith('/'):
        await message.answer(
            f"{EMOJI['error']} Неизвестная команда.\n\n"
            f"Используйте /start для начала работы."
        )
        return
    
    # Для обычных сообщений
    user_id = message.from_user.id
    
    if is_admin(user_id):
        keyboard = get_mixed_keyboard(True)
        await message.answer(
            f"{EMOJI['info']} Используйте кнопки меню для навигации.\n\n"
            f"Для начала работы нажмите /start",
            reply_markup=keyboard
        )
    else:
        keyboard = get_user_keyboard()
        await message.answer(
            f"{EMOJI['info']} Я не понимаю это сообщение.\n\n"
            f"Используйте кнопки меню или команду /start",
            reply_markup=keyboard
        )

# Функция запуска бота
async def main():
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.handlers.RotatingFileHandler(
                'bot.log',
                maxBytes=10*1024*1024,  # 10 MB
                backupCount=5,
                encoding='utf-8'
            ),
            logging.StreamHandler()
        ]
    )
    
    # Инициализация базы данных
    init_db()
    
    # Создание настроек по умолчанию для всех админов
    for admin_id in ADMINS:
        create_admin_settings(admin_id)
    
    # Загрузка существующих задач в планировщик
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, admin_id, payment_day, payment_time, payment_message FROM user_admin_links')
    links = cursor.fetchall()
    conn.close()
    
    for user_id, admin_id, day, time, message in links:
        try:
            hour, minute = map(int, time.split(':'))
            scheduler.add_job(
                send_payment_reminder,
                CronTrigger(day=day, hour=hour, minute=minute),
                args=[user_id, admin_id, message],
                id=f"payment_{user_id}_{admin_id}",
                replace_existing=True
            )
            logging.info(f"Загружено напоминание для пользователя {user_id} на {day} число в {time}")
        except Exception as e:
            logging.error(f"Ошибка загрузки напоминания для {user_id}: {e}")
    
    # Запуск планировщика
    scheduler.start()
    
    # Настройка команд бота
    await setup_bot_commands()
    
    # Информационное сообщение
    logging.info("Бот запущен и готов к работе!")
    
    # Уведомляем админов о запуске
    for admin_id in ADMINS:
        try:
            await bot.send_message(
                admin_id,
                f"{EMOJI['rocket']} <b>Бот запущен!</b>\n\n"
                f"{EMOJI['success']} Система готова к работе.\n"
                f"{EMOJI['info']} Используйте /start для начала.",
                parse_mode='HTML',
                disable_notification=True
            )
        except:
            logging.warning(f"Не удалось отправить уведомление админу {admin_id}")
    
    # Запуск бота
    await dp.start_polling(bot)

# Обработчик кнопки быстрого добавления пользователя
@dp.callback_query(F.data.startswith("add_new_user_"))
async def add_new_user_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer(f"{EMOJI['error']} У вас нет прав администратора.", show_alert=True)
        return
    
    user_id = int(callback.data.split("_")[3])
    
    # Проверяем что пользователь еще не добавлен
    existing_admin = get_admin_for_user(user_id)
    if existing_admin:
        if existing_admin == callback.from_user.id:
            await callback.answer(f"{EMOJI['error']} Этот пользователь уже привязан к вам!", show_alert=True)
        else:
            await callback.answer(f"{EMOJI['error']} Этот пользователь уже привязан к другому администратору!", show_alert=True)
        return
    
    # Проверяем существование пользователя
    try:
        user_info = await bot.get_chat(user_id)
        user_name = user_info.full_name or "Без имени"
        username = user_info.username
    except:
        await callback.answer(
            f"{EMOJI['error']} Пользователь больше не доступен.",
            show_alert=True
        )
        return
    
    await state.update_data(user_id=user_id, user_name=user_name)
    await state.set_state(AdminStates.waiting_day)
    
    keyboard = get_cancel_keyboard()
    text = (
        f"{EMOJI['success']} Добавление пользователя:\n\n"
        f"{format_user_info(user_id, user_name, username)}\n\n"
        f"{EMOJI['calendar']} Введите день месяца для отправки напоминания (1-31):"
    )
    
    # Редактируем исходное сообщение, убирая кнопку
    await callback.message.edit_text(
        callback.message.text + f"\n\n{EMOJI['success']} <b>Процесс добавления начат!</b>",
        parse_mode='HTML'
    )
    
    # Отправляем новое сообщение с запросом дня
    await bot.send_message(
        callback.from_user.id,
        text,
        reply_markup=keyboard,
        parse_mode='HTML'
    )
    
    # Уведомляем пользователя о начале процесса добавления
    admin_alias, _, _ = get_admin_settings(callback.from_user.id)
    try:
        await bot.send_message(
            user_id,
            f"{EMOJI['info']} Администратор <b>{escape_html(admin_alias)}</b> начал процесс добавления вас в систему.\n"
            f"Пожалуйста, подождите немного...",
            parse_mode='HTML'
        )
    except:
        pass

if __name__ == "__main__":
    asyncio.run(main())