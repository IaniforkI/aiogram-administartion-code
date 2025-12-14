from typing import List, Tuple, Optional, Dict, Any
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime
import html

from .models import User, Chat, BotAdmin
from .config import AdminLevel, ChatAdminLevel

def create_keyboard(buttons: List[Tuple[str, str]], columns: int = 2) -> InlineKeyboardMarkup:
    """
    Создать клавиатуру из списка кнопок
    
    Args:
        buttons: Список кортежей (текст, callback_data)
        columns: Количество колонок
    
    Returns:
        InlineKeyboardMarkup
    """
    keyboard = []
    
    for i in range(0, len(buttons), columns):
        row = buttons[i:i + columns]
        keyboard.append([
            InlineKeyboardButton(text=text, callback_data=callback_data)
            for text, callback_data in row
        ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def create_pagination_keyboard(
    current_page: int,
    total_pages: int,
    prefix: str,
    additional_buttons: Optional[List[Tuple[str, str]]] = None
) -> InlineKeyboardMarkup:
    """
    Создать клавиатуру с пагинацией
    
    Args:
        current_page: Текущая страница (начиная с 0)
        total_pages: Всего страниц
        prefix: Префикс для callback_data
        additional_buttons: Дополнительные кнопки
    
    Returns:
        InlineKeyboardMarkup
    """
    buttons = []
    
    # Кнопки навигации
    nav_buttons = []
    
    if current_page > 0:
        nav_buttons.append(
            InlineKeyboardButton(
                text="◀️ Назад", 
                callback_data=f"{prefix}_{current_page - 1}"
            )
        )
    
    nav_buttons.append(
        InlineKeyboardButton(
            text=f"{current_page + 1}/{total_pages}", 
            callback_data=f"{prefix}_info"
        )
    )
    
    if current_page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton(
                text="Вперед ▶️", 
                callback_data=f"{prefix}_{current_page + 1}"
            )
        )
    
    if nav_buttons:
        buttons.append(nav_buttons)
    
    # Дополнительные кнопки
    if additional_buttons:
        for i in range(0, len(additional_buttons), 2):
            row = additional_buttons[i:i + 2]
            buttons.append([
                InlineKeyboardButton(text=text, callback_data=callback_data)
                for text, callback_data in row
            ])
    
    # Кнопка возврата в меню
    buttons.append([
        InlineKeyboardButton(text="◀️ В меню", callback_data="admin_menu")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def create_confirmation_keyboard(
    confirm_text: str = "✅ Да",
    cancel_text: str = "❌ Нет",
    confirm_data: str = "confirm",
    cancel_data: str = "cancel"
) -> InlineKeyboardMarkup:
    """
    Создать клавиатуру подтверждения
    
    Args:
        confirm_text: Текст кнопки подтверждения
        cancel_text: Текст кнопки отмены
        confirm_data: Callback_data для подтверждения
        cancel_data: Callback_data для отмены
    
    Returns:
        InlineKeyboardMarkup
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=confirm_text, callback_data=confirm_data),
            InlineKeyboardButton(text=cancel_text, callback_data=cancel_data)
        ]
    ])

def create_admin_menu(admin_level: int) -> InlineKeyboardMarkup:
    """
    Создать главное меню админ-панели в зависимости от уровня админа
    
    Args:
        admin_level: Уровень админа (1-3)
    
    Returns:
        InlineKeyboardMarkup
    """
    # Базовые кнопки для всех уровней
    buttons = [
        [
            InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users"),
            InlineKeyboardButton(text="💬 Чаты", callback_data="admin_chats")
        ],
        [
            InlineKeyboardButton(text="📈 Статистика", callback_data="admin_stats"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin_settings")
        ]
    ]
    
    # Кнопки для уровня 2 и выше
    if admin_level >= 2:
        buttons.append([
            InlineKeyboardButton(text="📢 Рассылки", callback_data="admin_broadcast"),
            InlineKeyboardButton(text="🛡️ Модерация", callback_data="admin_moderation")
        ])
    
    # Кнопки для уровня 3
    if admin_level >= 3:
        buttons.append([
            InlineKeyboardButton(text="🎮 Дополнительно", callback_data="admin_extras")
        ])
    
    # Кнопка обновления/выхода
    buttons.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_menu"),
        InlineKeyboardButton(text="🚪 Выход", callback_data="admin_logout")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def format_user_info(user: User) -> str:
    """
    Форматировать информацию о пользователе
    
    Args:
        user: Объект пользователя
    
    Returns:
        Отформатированная строка
    """
    # Экранирование HTML
    def escape(text):
        return html.escape(str(text))
    
    # Статус пользователя
    status_icons = {
        1: "✅ Активен",
        2: "❌ Заблокирован",
        3: "⏸️ Временно заблокирован",
        4: "💤 Неактивен"
    }
    
    status = status_icons.get(user.status.value, "❓ Неизвестно")
    
    # Форматирование дат
    reg_date = user.registration_date.strftime("%d.%m.%Y %H:%M")
    last_activity = user.last_activity.strftime("%d.%m.%Y %H:%M")
    
    text = f"👤 Информация о пользователе\n\n"
    text += f"🆔 ID: <code>{user.user_id}</code>\n"
    text += f"📛 Имя: {escape(user.full_name)}\n"
    
    if user.username:
        text += f"📱 Username: @{escape(user.username)}\n"
    
    text += f"🌐 Язык: {escape(user.language_code)}\n"
    text += f"👑 Премиум: {'✅ Да' if user.is_premium else '❌ Нет'}\n"
    text += f"⭐ Рейтинг: {user.rating}\n"
    text += f"⚠️ Предупреждения: {user.warnings}\n"
    text += f"📊 Статус: {status}\n"
    
    if user.email:
        text += f"📧 Email: {escape(user.email)}\n"
    
    if user.phone:
        text += f"📱 Телефон: {escape(user.phone)}\n"
    
    text += f"📅 Регистрация: {reg_date}\n"
    text += f"⏰ Последняя активность: {last_activity}\n"
    
    # Дополнительные данные из metadata
    if user.metadata:
        if 'chats_count' in user.metadata:
            text += f"💬 Чатов: {user.metadata['chats_count']}\n"
        
        if 'messages_count' in user.metadata:
            text += f"📨 Сообщений: {user.metadata['messages_count']:,}\n"
    
    return text

def format_chat_info(chat: Chat) -> str:
    """
    Форматировать информацию о чате
    
    Args:
        chat: Объект чата
    
    Returns:
        Отформатированная строка
    """
    # Экранирование HTML
    def escape(text):
        return html.escape(str(text))
    
    # Тип чата
    type_icons = {
        "private": "🔒 Приватный",
        "group": "👥 Группа",
        "supergroup": "👑 Супергруппа",
        "channel": "📢 Канал"
    }
    
    chat_type = type_icons.get(chat.chat_type, chat.chat_type)
    
    # Форматирование дат
    join_date = chat.join_date.strftime("%d.%m.%Y %H:%M")
    last_activity = chat.last_activity.strftime("%d.%m.%Y %H:%M")
    
    text = f"💬 Информация о чате\n\n"
    text += f"🆔 ID: <code>{chat.chat_id}</code>\n"
    text += f"📛 Название: {escape(chat.title)}\n"
    text += f"📋 Тип: {chat_type}\n"
    
    if chat.username:
        text += f"📱 Username: @{escape(chat.username)}\n"
    
    text += f"👥 Участников: {chat.members_count:,}\n"
    
    if chat.owner_id:
        text += f"👑 Владелец: <code>{chat.owner_id}</code>\n"
    
    text += f"🤖 Бот добавлен: {join_date}\n"
    text += f"⏰ Последняя активность: {last_activity}\n"
    
    # Настройки
    settings = chat.settings
    if settings:
        text += f"\n⚙️ Настройки:\n"
        
        if settings.get("automoderation_enabled"):
            text += f"• 🤖 Автомодерация: ✅\n"
        
        if settings.get("warnings_enabled"):
            max_warnings = settings.get("max_warnings", 3)
            text += f"• ⚠️ Макс. предупреждений: {max_warnings}\n"
        
        if settings.get("statistics_enabled"):
            text += f"• 📊 Статистика: ✅\n"
        
        if settings.get("rules_enabled"):
            text += f"• 📜 Правила: ✅\n"
    
    return text

def format_bot_admin_info(admin: BotAdmin) -> str:
    """
    Форматировать информацию об админе бота
    
    Args:
        admin: Объект админа бота
    
    Returns:
        Отформатированная строка
    """
    # Уровень админа
    level_texts = {
        1: "👶 Младший админ",
        2: "👨‍💼 Старший админ",
        3: "👑 Главный админ"
    }
    
    level_text = level_texts.get(admin.level, f"Уровень {admin.level}")
    
    # Форматирование даты
    added_date = admin.added_date.strftime("%d.%m.%Y %H:%M")
    
    text = f"👑 Информация об админе бота\n\n"
    text += f"🆔 ID: <code>{admin.user_id}</code>\n"
    text += f"📊 Уровень: {level_text}\n"
    text += f"📅 Назначен: {added_date}\n"
    
    if admin.added_by:
        text += f"👤 Назначил: <code>{admin.added_by}</code>\n"
    
    # Разрешения
    if admin.permissions:
        text += f"\n🔐 Разрешения:\n"
        for perm in admin.permissions[:10]:  # Ограничиваем 10 разрешениями
            text += f"• {perm}\n"
        
        if len(admin.permissions) > 10:
            text += f"• ... и еще {len(admin.permissions) - 10}\n"
    
    return text

def format_chat_admin_info(admin: ChatAdmin) -> str:
    """
    Форматировать информацию об админе чата
    
    Args:
        admin: Объект админа чата
    
    Returns:
        Отформатированная строка
    """
    # Уровень админа чата
    level_texts = {
        1: "👀 Наблюдатель",
        2: "👶 Помощник модератора",
        3: "🛡️ Модератор",
        4: "👨‍💼 Старший модератор",
        5: "👑 Владелец"
    }
    
    level_text = level_texts.get(admin.level, f"Уровень {admin.level}")
    
    # Форматирование дат
    added_date = admin.added_date.strftime("%d.%m.%Y %H:%M")
    
    text = f"🛡️ Информация об админе чата\n\n"
    text += f"💬 ID чата: <code>{admin.chat_id}</code>\n"
    text += f"👤 ID пользователя: <code>{admin.user_id}</code>\n"
    text += f"📊 Уровень: {level_text}\n"
    text += f"📅 Назначен: {added_date}\n"
    
    if admin.added_by:
        text += f"👤 Назначил: <code>{admin.added_by}</code>\n"
    
    if admin.expires_at:
        expires_date = admin.expires_at.strftime("%d.%m.%Y %H:%M")
        text += f"⏰ Истекает: {expires_date}\n"
        
        # Проверка истекшего срока
        if admin.is_expired:
            text += f"❌ Истек!\n"
    
    # Разрешения
    if admin.permissions:
        text += f"\n🔐 Разрешения:\n"
        for perm in admin.permissions[:10]:
            text += f"• {perm}\n"
    
    return text

def create_user_actions_keyboard(user_id: int, admin_level: int) -> InlineKeyboardMarkup:
    """
    Создать клавиатуру действий с пользователем
    
    Args:
        user_id: ID пользователя
        admin_level: Уровень админа
    
    Returns:
        InlineKeyboardMarkup
    """
    buttons = []
    
    # Базовые действия для всех админов
    buttons.append([
        InlineKeyboardButton(text="👁️ Просмотреть", callback_data=f"user_view:{user_id}"),
        InlineKeyboardButton(text="📊 Статистика", callback_data=f"user_stats:{user_id}")
    ])
    
    # Действия для уровня 2 и выше
    if admin_level >= 2:
        buttons.append([
            InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"user_edit:{user_id}"),
            InlineKeyboardButton(text="⚠️ Предупредить", callback_data=f"user_warn:{user_id}")
        ])
        
        buttons.append([
            InlineKeyboardButton(text="🔒 Заблокировать", callback_data=f"user_block:{user_id}"),
            InlineKeyboardButton(text="🔓 Разблокировать", callback_data=f"user_unblock:{user_id}")
        ])
    
    # Действия для уровня 3
    if admin_level >= 3:
        buttons.append([
            InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"user_delete:{user_id}"),
            InlineKeyboardButton(text="📨 Написать", callback_data=f"user_message:{user_id}")
        ])
    
    # Кнопка возврата
    buttons.append([
        InlineKeyboardButton(text="◀️ Назад", callback_data="admin_users_list_0")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def create_chat_actions_keyboard(chat_id: int, admin_level: int) -> InlineKeyboardMarkup:
    """
    Создать клавиатуру действий с чатом
    
    Args:
        chat_id: ID чата
        admin_level: Уровень админа
    
    Returns:
        InlineKeyboardMarkup
    """
    buttons = []
    
    # Базовые действия
    buttons.append([
        InlineKeyboardButton(text="👁️ Просмотреть", callback_data=f"chat_view:{chat_id}"),
        InlineKeyboardButton(text="📊 Статистика", callback_data=f"chat_stats:{chat_id}")
    ])
    
    # Действия для уровня 2 и выше
    if admin_level >= 2:
        buttons.append([
            InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"chat_settings:{chat_id}"),
            InlineKeyboardButton(text="🛡️ Админы", callback_data=f"chat_admins:{chat_id}")
        ])
    
    # Действия для уровня 3
    if admin_level >= 3:
        buttons.append([
            InlineKeyboardButton(text="📨 Рассылка", callback_data=f"chat_broadcast:{chat_id}"),
            InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"chat_delete:{chat_id}")
        ])
    
    # Кнопка возврата
    buttons.append([
        InlineKeyboardButton(text="◀️ Назад", callback_data="admin_chats_list_0")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def format_number(number: int) -> str:
    """
    Форматировать число с разделителями
    
    Args:
        number: Число
    
    Returns:
        Отформатированная строка
    """
    return f"{number:,}".replace(",", " ")

def format_duration(seconds: int) -> str:
    """
    Форматировать длительность
    
    Args:
        seconds: Количество секунд
    
    Returns:
        Отформатированная строка
    """
    if seconds < 60:
        return f"{seconds} сек"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} мин"
    elif seconds < 86400:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours} ч {minutes} мин"
    else:
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        return f"{days} д {hours} ч"

def format_file_size(bytes_size: int) -> str:
    """
    Форматировать размер файла
    
    Args:
        bytes_size: Размер в байтах
    
    Returns:
        Отформатированная строка
    """
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} PB"

def create_yes_no_keyboard(yes_data: str = "yes", no_data: str = "no") -> InlineKeyboardMarkup:
    """
    Создать клавиатуру Да/Нет
    
    Args:
        yes_data: Callback_data для Да
        no_data: Callback_data для Нет
    
    Returns:
        InlineKeyboardMarkup
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да", callback_data=yes_data),
            InlineKeyboardButton(text="❌ Нет", callback_data=no_data)
        ]
    ])

def create_back_keyboard(back_data: str = "back") -> InlineKeyboardMarkup:
    """
    Создать клавиатуру с кнопкой Назад
    
    Args:
        back_data: Callback_data для Назад
    
    Returns:
        InlineKeyboardMarkup
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=back_data)]
    ])

def create_main_menu_keyboard() -> InlineKeyboardMarkup:
    """
    Создать клавиатуру главного меню
    
    Returns:
        InlineKeyboardMarkup
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🛠️ Админ-панель", callback_data="admin_menu"),
            InlineKeyboardButton(text="👤 Профиль", callback_data="user_profile")
        ],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="user_stats"),
            InlineKeyboardButton(text="📋 Команды", callback_data="user_commands")
        ],
        [
            InlineKeyboardButton(text="❓ Помощь", callback_data="help"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="user_settings")
        ]
    ])

def format_time_ago(timestamp: datetime) -> str:
    """
    Форматировать время в формате "сколько времени назад"
    
    Args:
        timestamp: Временная метка
    
    Returns:
        Отформатированная строка
    """
    now = datetime.now()
    diff = now - timestamp
    
    if diff.days > 365:
        years = diff.days // 365
        return f"{years} год назад" if years == 1 else f"{years} лет назад"
    elif diff.days > 30:
        months = diff.days // 30
        return f"{months} месяц назад" if months == 1 else f"{months} месяцев назад"
    elif diff.days > 0:
        return f"{diff.days} день назад" if diff.days == 1 else f"{diff.days} дней назад"
    elif diff.seconds > 3600:
        hours = diff.seconds // 3600
        return f"{hours} час назад" if hours == 1 else f"{hours} часов назад"
    elif diff.seconds > 60:
        minutes = diff.seconds // 60
        return f"{minutes} минуту назад" if minutes == 1 else f"{minutes} минут назад"
    else:
        return "только что"

def create_inline_url_keyboard(text: str, url: str) -> InlineKeyboardMarkup:
    """
    Создать клавиатуру с одной URL кнопкой
    
    Args:
        text: Текст кнопки
        url: URL для перехода
    
    Returns:
        InlineKeyboardMarkup
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, url=url)]
    ])

def create_multiple_url_keyboard(buttons: List[Tuple[str, str]]) -> InlineKeyboardMarkup:
    """
    Создать клавиатуру с несколькими URL кнопками
    
    Args:
        buttons: Список кортежей (текст, url)
    
    Returns:
        InlineKeyboardMarkup
    """
    keyboard = []
    
    for i in range(0, len(buttons), 2):
        row = buttons[i:i + 2]
        keyboard.append([
            InlineKeyboardButton(text=text, url=url)
            for text, url in row
        ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def truncate_text(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """
    Обрезать текст до указанной длины
    
    Args:
        text: Текст
        max_length: Максимальная длина
        suffix: Суффикс для обрезанного текста
    
    Returns:
        Обрезанный текст
    """
    if len(text) <= max_length:
        return text
    
    return text[:max_length - len(suffix)] + suffix