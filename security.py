import hashlib
import time
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
import asyncio
from functools import wraps

from aiogram import Bot
from aiogram.types import User as TelegramUser

from .config import AdminLevel, ChatAdminLevel, SecurityConfig
from .models import BotAdmin, ChatAdmin, User, Chat, ActionLog, ActionType

@dataclass
class Permission:
    """Разрешение системы"""
    name: str
    description: str
    required_level: int = 1
    category: str = "general"

class SecurityManager:
    """Менеджер безопасности и проверки прав"""
    
    # Системные разрешения
    PERMISSIONS = {
        # Управление пользователями
        "users.view": Permission("users.view", "Просмотр пользователей", 1, "users"),
        "users.search": Permission("users.search", "Поиск пользователей", 1, "users"),
        "users.block": Permission("users.block", "Блокировка пользователей", 2, "users"),
        "users.unblock": Permission("users.unblock", "Разблокировка пользователей", 2, "users"),
        "users.edit": Permission("users.edit", "Редактирование пользователей", 2, "users"),
        "users.delete": Permission("users.delete", "Удаление пользователей", 3, "users"),
        
        # Управление чатами
        "chats.view": Permission("chats.view", "Просмотр чатов", 1, "chats"),
        "chats.edit": Permission("chats.edit", "Редактирование чатов", 2, "chats"),
        "chats.delete": Permission("chats.delete", "Удаление чатов", 3, "chats"),
        "chats.admins.manage": Permission("chats.admins.manage", "Управление админами чатов", 2, "chats"),
        
        # Управление админами бота
        "admins.view": Permission("admins.view", "Просмотр админов бота", 2, "admins"),
        "admins.add": Permission("admins.add", "Добавление админов", 3, "admins"),
        "admins.remove": Permission("admins.remove", "Удаление админов", 3, "admins"),
        "admins.edit": Permission("admins.edit", "Редактирование админов", 3, "admins"),
        
        # Статистика
        "stats.view": Permission("stats.view", "Просмотр статистики", 1, "statistics"),
        "stats.export": Permission("stats.export", "Экспорт статистики", 2, "statistics"),
        "stats.charts": Permission("stats.charts", "Просмотр графиков", 1, "statistics"),
        
        # Рассылки
        "broadcast.send": Permission("broadcast.send", "Отправка рассылок", 2, "broadcasting"),
        "broadcast.schedule": Permission("broadcast.schedule", "Планирование рассылок", 2, "broadcasting"),
        "broadcast.view": Permission("broadcast.view", "Просмотр истории рассылок", 1, "broadcasting"),
        
        # Настройки
        "settings.view": Permission("settings.view", "Просмотр настроек", 1, "settings"),
        "settings.edit": Permission("settings.edit", "Редактирование настроек", 2, "settings"),
        "settings.backup": Permission("settings.backup", "Резервное копирование", 3, "settings"),
        
        # Модерация
        "moderation.warn": Permission("moderation.warn", "Выдача предупреждений", 2, "moderation"),
        "moderation.ban": Permission("moderation.ban", "Бан пользователей", 2, "moderation"),
        "moderation.delete": Permission("moderation.delete", "Удаление сообщений", 1, "moderation"),
        "moderation.reports": Permission("moderation.reports", "Управление жалобами", 2, "moderation"),
        
        # Система
        "system.restart": Permission("system.restart", "Перезагрузка системы", 3, "system"),
        "system.update": Permission("system.update", "Обновление системы", 3, "system"),
        "system.logs": Permission("system.logs", "Просмотр логов", 3, "system"),
    }
    
    # Права админов чата
    CHAT_PERMISSIONS = {
        # Уровень 1 - Наблюдатель
        1: {"reports.view", "stats.view.own", "rules.view"},
        # Уровень 2 - Помощник модератора
        2: {"messages.delete", "warn.issue", "members.view"},
        # Уровень 3 - Модератор
        3: {"ban.temporary", "warn.manage", "stats.view.all", "messages.purge"},
        # Уровень 4 - Старший модератор
        4: {"admins.manage", "automoderation.manage", "rules.edit", "stats.full"},
        # Уровень 5 - Владелец
        5: {"*"},  # Все права
    }
    
    def __init__(self, config: SecurityConfig, bot_id: int = 0):
        self.config = config
        self.bot_id = bot_id
        
        # Кэш для проверки прав
        self._admin_cache: Dict[int, BotAdmin] = {}
        self._chat_admin_cache: Dict[Tuple[int, int], ChatAdmin] = {}
        
        # Троттлинг
        self._throttle_data: Dict[int, List[float]] = {}
        self._throttle_lock = asyncio.Lock()
        
        # Сессии
        self._sessions: Dict[int, Dict] = {}
        
    async def check_bot_admin(self, user_id: int, bot_id: Optional[int] = None) -> Optional[BotAdmin]:
        """Проверить, является ли пользователь админом бота"""
        from .database import DatabaseManager
        
        bot_id = bot_id or self.bot_id
        
        # Проверка кэша
        cache_key = (user_id, bot_id)
        if cache_key in self._admin_cache:
            return self._admin_cache[cache_key]
        
        # Запрос к БД
        db = DatabaseManager.get_instance()
        admin = await db.get_bot_admin(user_id, bot_id)
        
        if admin:
            self._admin_cache[cache_key] = admin
        
        return admin
    
    async def check_chat_admin(self, user_id: int, chat_id: int, bot_id: Optional[int] = None) -> Optional[ChatAdmin]:
        """Проверить, является ли пользователь админом чата"""
        from .database import DatabaseManager
        
        bot_id = bot_id or self.bot_id
        
        # Проверка кэша
        cache_key = (user_id, chat_id, bot_id)
        if cache_key in self._chat_admin_cache:
            admin = self._chat_admin_cache[cache_key]
            if admin.is_expired:
                # Удалить просроченного админа
                await db.remove_chat_admin(chat_id, user_id, bot_id)
                del self._chat_admin_cache[cache_key]
                return None
            return admin
        
        # Запрос к БД
        db = DatabaseManager.get_instance()
        admin = await db.get_chat_admin(chat_id, user_id, bot_id)
        
        if admin and not admin.is_expired:
            self._chat_admin_cache[cache_key] = admin
            return admin
        
        return None
    
    async def has_permission(self, user_id: int, permission: str, chat_id: Optional[int] = None) -> bool:
        """Проверить наличие прав у пользователя"""
        
        # Проверка админа бота
        admin = await self.check_bot_admin(user_id)
        if admin:
            perm_obj = self.PERMISSIONS.get(permission)
            if perm_obj and admin.level >= perm_obj.required_level:
                return True
        
        # Проверка админа чата (если указан chat_id)
        if chat_id:
            chat_admin = await self.check_chat_admin(user_id, chat_id)
            if chat_admin:
                perms = self.CHAT_PERMISSIONS.get(chat_admin.level, set())
                if "*" in perms or permission in perms:
                    return True
        
        return False
    
    async def throttle(self, user_id: int, action_type: str = "default") -> bool:
        """Проверка троттлинга для пользователя"""
        if not self.config.throttling_enabled:
            return True
        
        async with self._throttle_lock:
            now = time.time()
            key = f"{user_id}_{action_type}"
            
            # Очистка старых записей
            if key in self._throttle_data:
                self._throttle_data[key] = [
                    ts for ts in self._throttle_data[key]
                    if now - ts < 60  # Сохраняем только записи за последнюю минуту
                ]
            else:
                self._throttle_data[key] = []
            
            # Получение лимитов
            admin = await self.check_bot_admin(user_id)
            if admin:
                if admin.level == AdminLevel.MAIN:
                    limits = self.config.limits["admin_main"]
                elif admin.level == AdminLevel.SENIOR:
                    limits = self.config.limits["admin_senior"]
                else:
                    limits = self.config.limits["admin_junior"]
            else:
                limits = self.config.limits["user"]
            
            # Проверка лимитов
            per_second_limit = limits.get("per_second", 5)
            per_minute_limit = limits.get("per_minute", 30)
            
            # Проверка запросов в секунду
            recent_requests = [ts for ts in self._throttle_data[key] if now - ts < 1]
            if len(recent_requests) >= per_second_limit:
                return False
            
            # Проверка запросов в минуту
            minute_requests = [ts for ts in self._throttle_data[key] if now - ts < 60]
            if len(minute_requests) >= per_minute_limit:
                return False
            
            # Добавление нового запроса
            self._throttle_data[key].append(now)
            return True
    
    async def create_session(self, user_id: int, data: Dict) -> str:
        """Создание сессии для пользователя"""
        session_id = hashlib.sha256(f"{user_id}_{time.time()}".encode()).hexdigest()[:16]
        self._sessions[session_id] = {
            "user_id": user_id,
            "created_at": datetime.now(),
            "data": data
        }
        return session_id
    
    async def validate_session(self, session_id: str) -> Optional[int]:
        """Проверка валидности сессии"""
        if session_id not in self._sessions:
            return None
        
        session = self._sessions[session_id]
        session_age = datetime.now() - session["created_at"]
        
        if session_age.total_seconds() > self.config.session_timeout_minutes * 60:
            # Сессия истекла
            del self._sessions[session_id]
            return None
        
        # Обновление времени сессии
        session["created_at"] = datetime.now()
        
        return session["user_id"]
    
    async def log_action(
        self,
        user_id: int,
        action_type: ActionType,
        action_data: Dict,
        chat_id: Optional[int] = None
    ):
        """Логирование действия"""
        from .database import DatabaseManager
        
        log = ActionLog(
            user_id=user_id,
            chat_id=chat_id,
            action_type=action_type,
            action_data=action_data,
            bot_id=self.bot_id
        )
        
        db = DatabaseManager.get_instance()
        await db.add_action_log(log)
    
    async def check_ip_ban(self, ip_address: str) -> bool:
        """Проверка IP на бан"""
        # Здесь можно интегрировать с внешними сервисами
        # или использовать локальную БД для бана IP
        return False
    
    def clear_cache(self, user_id: Optional[int] = None, chat_id: Optional[int] = None):
        """Очистка кэша"""
        if user_id:
            # Очистка кэша для конкретного пользователя
            keys_to_remove = []
            for key in self._admin_cache:
                if key[0] == user_id:
                    keys_to_remove.append(key)
            
            for key in keys_to_remove:
                del self._admin_cache[key]
        
        if chat_id:
            # Очистка кэша для конкретного чата
            keys_to_remove = []
            for key in self._chat_admin_cache:
                if key[1] == chat_id:
                    keys_to_remove.append(key)
            
            for key in keys_to_remove:
                del self._chat_admin_cache[key]
    
    def get_all_permissions(self) -> Dict[str, Permission]:
        """Получить все разрешения системы"""
        return self.PERMISSIONS
    
    def get_permissions_for_level(self, level: int) -> List[str]:
        """Получить разрешения для уровня админа"""
        return [
            perm_name for perm_name, perm in self.PERMISSIONS.items()
            if perm.required_level <= level
        ]
    
    def get_chat_permissions_for_level(self, level: int) -> Set[str]:
        """Получить права для уровня админа чата"""
        perms = set()
        for lvl in range(1, level + 1):
            perms.update(self.CHAT_PERMISSIONS.get(lvl, set()))
        return perms


# Декораторы для проверки прав
def require_admin(level: AdminLevel = AdminLevel.JUNIOR):
    """Декоратор для проверки прав админа бота"""
    def decorator(func):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            from aiogram.types import Message, CallbackQuery
            
            # Получение сообщения или callback
            if args and isinstance(args[0], (Message, CallbackQuery)):
                event = args[0]
                user_id = event.from_user.id
                
                # Получение security manager из self или контекста
                security = getattr(self, 'security', None)
                if not security:
                    from .admin_system import AdminSystem
                    security = AdminSystem.get_instance().security
                
                admin = await security.check_bot_admin(user_id)
                
                if not admin or admin.level < level.value:
                    if isinstance(event, Message):
                        await event.answer("❌ У вас недостаточно прав для выполнения этой команды.")
                    elif isinstance(event, CallbackQuery):
                        await event.answer("❌ Недостаточно прав.", show_alert=True)
                    return
            
            return await func(self, *args, **kwargs)
        return wrapper
    return decorator


def require_chat_admin(level: ChatAdminLevel = ChatAdminLevel.OBSERVER):
    """Декоратор для проверки прав админа чата"""
    def decorator(func):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            from aiogram.types import Message, CallbackQuery
            
            if args and isinstance(args[0], Message):
                message = args[0]
                user_id = message.from_user.id
                chat_id = message.chat.id
                
                # Проверка, что это групповой чат
                if message.chat.type == "private":
                    await message.answer("❌ Эта команда работает только в чатах.")
                    return
                
                security = getattr(self, 'security', None)
                if not security:
                    from .admin_system import AdminSystem
                    security = AdminSystem.get_instance().security
                
                chat_admin = await security.check_chat_admin(user_id, chat_id)
                
                if not chat_admin or chat_admin.level < level.value:
                    await message.answer("❌ У вас недостаточно прав в этом чате.")
                    return
            
            return await func(self, *args, **kwargs)
        return wrapper
    return decorator


def throttle_command():
    """Декоратор для троттлинга команд"""
    def decorator(func):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            from aiogram.types import Message, CallbackQuery
            
            if args and isinstance(args[0], (Message, CallbackQuery)):
                event = args[0]
                user_id = event.from_user.id
                
                security = getattr(self, 'security', None)
                if not security:
                    from .admin_system import AdminSystem
                    security = AdminSystem.get_instance().security
                
                if not await security.throttle(user_id, func.__name__):
                    if isinstance(event, Message):
                        await event.answer("⏳ Слишком много запросов. Пожалуйста, подождите.")
                    elif isinstance(event, CallbackQuery):
                        await event.answer("⏳ Слишком много запросов.", show_alert=True)
                    return
            
            return await func(self, *args, **kwargs)
        return wrapper
    return decorator