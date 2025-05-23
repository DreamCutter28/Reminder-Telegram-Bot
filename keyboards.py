from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import datetime

# Эмодзи для консистентности с main.py
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
    'help': '❓',
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
    'rocket': '🚀'
}

def get_user_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура для обычных пользователей"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=f"{EMOJI['stats']} Мой статус"), 
                KeyboardButton(text=f"{EMOJI['chat']} Связь с админом")
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Выберите действие..."
    )
    return keyboard

def get_admin_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура для администраторов"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=f"{EMOJI['list']} Список пользователей"), 
                KeyboardButton(text=f"{EMOJI['stats']} Статистика оплат")
            ],
            [
                KeyboardButton(text=f"{EMOJI['add']} Добавить пользователя"), 
                KeyboardButton(text=f"{EMOJI['remove']} Удалить пользователя")
            ],
            [
                KeyboardButton(text=f"{EMOJI['search']} Неоплатившие"), 
                KeyboardButton(text=f"{EMOJI['alert']} Просроченные")
            ],
            [
                KeyboardButton(text=f"{EMOJI['check']} Подтвердить оплаты"), 
                KeyboardButton(text=f"{EMOJI['chat']} Активные чаты")
            ],
            [
                KeyboardButton(text=f"{EMOJI['settings']} Настройки админа")
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Выберите действие..."
    )
    return keyboard

def get_mixed_keyboard(is_admin: bool) -> ReplyKeyboardMarkup:
    """Смешанная клавиатура (админ может переключаться между режимами)"""
    if is_admin:
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(text=f"{EMOJI['settings']} Админ-панель")
                ]
            ],
            resize_keyboard=True,
            one_time_keyboard=False,
            input_field_placeholder="Выберите действие..."
        )
    else:
        return get_user_keyboard()
    
    return keyboard

def get_payment_confirmation_keyboard(admin_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для подтверждения оплаты пользователем"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{EMOJI['success']} Оплатил", 
            callback_data=f"paid_{admin_id}"
        )],
        [InlineKeyboardButton(
            text=f"{EMOJI['chat']} Связаться с админом", 
            callback_data=f"contact_admin_{admin_id}"
        )]
    ])
    return keyboard

def get_admin_payment_confirmation_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для админа для подтверждения/отклонения оплаты"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"{EMOJI['success']} Подтвердить", 
                callback_data=f"confirm_{user_id}"
            ),
            InlineKeyboardButton(
                text=f"{EMOJI['error']} Отклонить", 
                callback_data=f"reject_{user_id}"
            )
        ],
        [InlineKeyboardButton(
            text=f"{EMOJI['chat']} Начать чат", 
            callback_data=f"start_chat_{user_id}"
        )]
    ])
    return keyboard

def get_message_choice_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для выбора типа сообщения при добавлении пользователя"""
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
    return keyboard

def get_user_selection_keyboard(users: list[tuple]) -> InlineKeyboardMarkup:
    """Клавиатура для выбора пользователя из списка"""
    buttons = []
    
    for user_id, day, time, msg in users:
        # Форматируем текст кнопки
        button_text = f"{EMOJI['user']} ID: {user_id} ({day} число, {time})"
        if len(button_text) > 60:  # Ограничение длины текста кнопки
            button_text = f"{EMOJI['user']} ID: {user_id}"
        
        buttons.append([InlineKeyboardButton(
            text=button_text,
            callback_data=f"select_user_{user_id}"
        )])
    
    # Добавляем кнопку отмены
    buttons.append([InlineKeyboardButton(
        text=f"{EMOJI['cancel']} Отмена",
        callback_data="cancel_selection"
    )])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return keyboard

def get_back_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура с кнопкой назад"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=f"{EMOJI['back']} Назад")]
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Нажмите 'Назад' для выхода..."
    )
    return keyboard

def get_cancel_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура для отмены операции"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=f"{EMOJI['cancel']} Отмена")]
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        input_field_placeholder="Введите данные или отмените операцию..."
    )
    return keyboard

def get_settings_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура настроек администратора"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{EMOJI['user']} Изменить псевдоним",
            callback_data="change_alias"
        )],
        [InlineKeyboardButton(
            text=f"{EMOJI['chat']} Изменить сообщение по умолчанию",
            callback_data="change_default_message"
        )],
        [InlineKeyboardButton(
            text=f"{EMOJI['bell']} Настройки уведомлений",
            callback_data="notification_settings"
        )],
        [InlineKeyboardButton(
            text=f"{EMOJI['back']} Назад",
            callback_data="back_to_admin"
        )]
    ])
    return keyboard

def get_notification_settings_keyboard(current_status: bool) -> InlineKeyboardMarkup:
    """Клавиатура настроек уведомлений"""
    status_text = "Выключить" if current_status else "Включить"
    status_emoji = EMOJI['error'] if current_status else EMOJI['success']
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{status_emoji} {status_text} уведомления",
            callback_data="toggle_notifications"
        )],
        [InlineKeyboardButton(
            text=f"{EMOJI['back']} Назад",
            callback_data="back_to_settings"
        )]
    ])
    return keyboard

def get_chat_actions_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура действий в чате"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"{EMOJI['info']} Информация",
                callback_data=f"chat_info_{user_id}"
            ),
            InlineKeyboardButton(
                text=f"{EMOJI['stats']} Статистика",
                callback_data=f"chat_stats_{user_id}"
            )
        ],
        [InlineKeyboardButton(
            text=f"{EMOJI['calendar']} История платежей",
            callback_data=f"payment_history_{user_id}"
        )],
        [InlineKeyboardButton(
            text=f"{EMOJI['bell']} Отправить напоминание",
            callback_data=f"send_reminder_{user_id}"
        )]
    ])
    return keyboard

def get_payment_history_keyboard(user_id: int, page: int = 0, total_pages: int = 1) -> InlineKeyboardMarkup:
    """Клавиатура для навигации по истории платежей"""
    buttons = []
    
    # Навигация
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(
            text="◀️ Назад",
            callback_data=f"history_page_{user_id}_{page-1}"
        ))
    
    nav_buttons.append(InlineKeyboardButton(
        text=f"{page+1}/{total_pages}",
        callback_data="history_current_page"
    ))
    
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(
            text="Вперед ▶️",
            callback_data=f"history_page_{user_id}_{page+1}"
        ))
    
    if nav_buttons:
        buttons.append(nav_buttons)
    
    # Кнопка закрытия
    buttons.append([InlineKeyboardButton(
        text=f"{EMOJI['back']} Закрыть",
        callback_data="close_history"
    )])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return keyboard

def get_quick_actions_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура быстрых действий для админа"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=f"{EMOJI['bell']} Напомнить всем"),
                KeyboardButton(text=f"{EMOJI['stats']} Быстрая статистика")
            ],
            [
                KeyboardButton(text=f"{EMOJI['search']} Проверить оплаты"),
                KeyboardButton(text=f"{EMOJI['back']} Главное меню")
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=False
    )
    return keyboard

def get_user_actions_keyboard(user_id: int, is_admin: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура действий с пользователем для админа"""
    buttons = [
        [InlineKeyboardButton(
            text=f"{EMOJI['chat']} Начать чат",
            callback_data=f"start_chat_{user_id}"
        )],
        [InlineKeyboardButton(
            text=f"{EMOJI['stats']} Статистика платежей",
            callback_data=f"user_payment_stats_{user_id}"
        )],
        [InlineKeyboardButton(
            text=f"{EMOJI['settings']} Изменить настройки",
            callback_data=f"edit_user_settings_{user_id}"
        )]
    ]
    
    if is_admin:
        buttons.append([
            InlineKeyboardButton(
                text=f"{EMOJI['bell']} Отправить напоминание",
                callback_data=f"send_reminder_now_{user_id}"
            )
        ])
        buttons.append([
            InlineKeyboardButton(
                text=f"{EMOJI['remove']} Удалить пользователя",
                callback_data=f"delete_user_{user_id}"
            )
        ])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return keyboard

def get_confirmation_keyboard(action: str, data: str) -> InlineKeyboardMarkup:
    """Универсальная клавиатура подтверждения действия"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"{EMOJI['success']} Да, подтверждаю",
                callback_data=f"confirm_{action}_{data}"
            ),
            InlineKeyboardButton(
                text=f"{EMOJI['cancel']} Отмена",
                callback_data=f"cancel_{action}"
            )
        ]
    ])
    return keyboard