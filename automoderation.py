import re
import asyncio
import logging
from typing import Dict, List, Optional, Set, Tuple
from datetime import datetime, timedelta
from enum import Enum

from aiogram import Bot
from aiogram.types import Message, CallbackQuery, ChatPermissions
from aiogram.filters import Filter

from .models import Chat, User, ActionType
from .ui import create_keyboard
from .security import require_chat_admin

logger = logging.getLogger(__name__)

class FilterType(Enum):
    """Типы фильтров автомодерации"""
    ANTI_SPAM = "anti_spam"
    ANTI_MAT = "anti_mat"
    ANTI_LINKS = "anti_links"
    ANTI_FLOOD = "anti_flood"
    ANTI_CAPS = "anti_caps"
    ANTI_STICKERS = "anti_stickers"
    ANTI_VOICE = "anti_voice"

class ActionType(Enum):
    """Типы действий при нарушении"""
    DELETE = "delete"
    WARN = "warn"
    MUTE = "mute"
    BAN = "ban"
    NOTIFY = "notify"

class Violation:
    """Нарушение правил"""
    
    def __init__(self, user_id: int, chat_id: int, filter_type: FilterType, 
                 message: Optional[Message] = None, details: Optional[Dict] = None):
        self.user_id = user_id
        self.chat_id = chat_id
        self.filter_type = filter_type
        self.message = message
        self.details = details or {}
        self.timestamp = datetime.now()
    
    def to_dict(self) -> Dict:
        """Конвертация в словарь"""
        return {
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "filter_type": self.filter_type.value,
            "details": self.details,
            "timestamp": self.timestamp.isoformat()
        }

class AutoModerationManager:
    """Менеджер автомодерации"""
    
    def __init__(self, admin_system):
        self.admin_system = admin_system
        self.bot = admin_system.bot
        
        # Кэш для проверки флуда
        self._flood_cache: Dict[Tuple[int, int], List[datetime]] = {}
        
        # Стандартные настройки
        self.default_settings = {
            "enabled": True,
            "filters": {
                FilterType.ANTI_SPAM.value: {
                    "enabled": True,
                    "max_similar_messages": 3,
                    "max_messages_per_minute": 10,
                    "max_message_length": 2000,
                    "actions": [ActionType.DELETE.value, ActionType.WARN.value]
                },
                FilterType.ANTI_MAT.value: {
                    "enabled": True,
                    "word_list": self._load_bad_words(),
                    "partial_match": True,
                    "actions": [ActionType.DELETE.value, ActionType.WARN.value]
                },
                FilterType.ANTI_LINKS.value: {
                    "enabled": True,
                    "allowed_domains": [],
                    "blocked_domains": [],
                    "allow_all": False,
                    "actions": [ActionType.DELETE.value]
                },
                FilterType.ANTI_FLOOD.value: {
                    "enabled": True,
                    "max_messages_per_minute": 5,
                    "max_stickers_per_minute": 3,
                    "max_voice_per_minute": 2,
                    "actions": [ActionType.MUTE.value]
                },
                FilterType.ANTI_CAPS.value: {
                    "enabled": True,
                    "max_caps_percentage": 70,
                    "min_message_length": 5,
                    "actions": [ActionType.DELETE.value]
                },
                FilterType.ANTI_STICKERS.value: {
                    "enabled": False,
                    "max_per_minute": 5,
                    "actions": [ActionType.DELETE.value]
                },
                FilterType.ANTI_VOICE.value: {
                    "enabled": False,
                    "max_per_minute": 3,
                    "actions": [ActionType.DELETE.value]
                }
            },
            "mute_duration": 300,  # 5 минут в секундах
            "warnings_to_ban": 3,
            "notify_admins": True,
            "whitelist": {
                "users": [],
                "words": [],
                "links": []
            }
        }
    
    def _load_bad_words(self) -> List[str]:
        """Загрузка списка плохих слов"""
        # Здесь можно загрузить из файла или БД
        # Для примера возвращаем небольшой список
        return [
            "плохоеслово1", "плохоеслово2", "оскорбление",
            "мат", "брань", "ругательство"
        ]
    
    async def check_message(self, message: Message) -> Optional[Violation]:
        """Проверить сообщение на нарушения"""
        if not message.text and not message.sticker and not message.voice:
            return None
        
        chat_id = message.chat.id
        user_id = message.from_user.id
        
        # Получение настроек чата
        settings = await self.get_chat_settings(chat_id)
        if not settings.get("enabled", False):
            return None
        
        # Проверка белого списка
        if await self._is_whitelisted(user_id, chat_id, message, settings):
            return None
        
        # Проверка всех фильтров
        filters = settings.get("filters", {})
        
        # Анти-спам
        if filters.get(FilterType.ANTI_SPAM.value, {}).get("enabled", False):
            violation = await self._check_anti_spam(user_id, chat_id, message, filters[FilterType.ANTI_SPAM.value])
            if violation:
                return violation
        
        # Анти-мат
        if filters.get(FilterType.ANTI_MAT.value, {}).get("enabled", False) and message.text:
            violation = await self._check_anti_mat(user_id, chat_id, message, filters[FilterType.ANTI_MAT.value])
            if violation:
                return violation
        
        # Анти-ссылки
        if filters.get(FilterType.ANTI_LINKS.value, {}).get("enabled", False) and message.text:
            violation = await self._check_anti_links(user_id, chat_id, message, filters[FilterType.ANTI_LINKS.value])
            if violation:
                return violation
        
        # Анти-флуд
        if filters.get(FilterType.ANTI_FLOOD.value, {}).get("enabled", False):
            violation = await self._check_anti_flood(user_id, chat_id, message, filters[FilterType.ANTI_FLOOD.value])
            if violation:
                return violation
        
        # Анти-капс
        if filters.get(FilterType.ANTI_CAPS.value, {}).get("enabled", False) and message.text:
            violation = await self._check_anti_caps(user_id, chat_id, message, filters[FilterType.ANTI_CAPS.value])
            if violation:
                return violation
        
        # Анти-стикеры
        if filters.get(FilterType.ANTI_STICKERS.value, {}).get("enabled", False) and message.sticker:
            violation = await self._check_anti_stickers(user_id, chat_id, message, filters[FilterType.ANTI_STICKERS.value])
            if violation:
                return violation
        
        # Анти-голосовые
        if filters.get(FilterType.ANTI_VOICE.value, {}).get("enabled", False) and message.voice:
            violation = await self._check_anti_voice(user_id, chat_id, message, filters[FilterType.ANTI_VOICE.value])
            if violation:
                return violation
        
        return None
    
    async def _check_anti_spam(self, user_id: int, chat_id: int, message: Message, settings: Dict) -> Optional[Violation]:
        """Проверка на спам"""
        violations = []
        
        # Проверка длины сообщения
        max_length = settings.get("max_message_length", 2000)
        if message.text and len(message.text) > max_length:
            violations.append(f"Сообщение слишком длинное ({len(message.text)} > {max_length} символов)")
        
        # Проверка количества сообщений в минуту
        max_per_minute = settings.get("max_messages_per_minute", 10)
        cache_key = (user_id, chat_id)
        
        now = datetime.now()
        minute_ago = now - timedelta(minutes=1)
        
        if cache_key not in self._flood_cache:
            self._flood_cache[cache_key] = []
        
        # Очистка старых записей
        self._flood_cache[cache_key] = [
            ts for ts in self._flood_cache[cache_key]
            if ts > minute_ago
        ]
        
        # Добавление текущего сообщения
        self._flood_cache[cache_key].append(now)
        
        # Проверка лимита
        if len(self._flood_cache[cache_key]) > max_per_minute:
            violations.append(f"Слишком много сообщений ({len(self._flood_cache[cache_key])} > {max_per_minute} в минуту)")
        
        if violations:
            return Violation(
                user_id=user_id,
                chat_id=chat_id,
                filter_type=FilterType.ANTI_SPAM,
                message=message,
                details={"violations": violations}
            )
        
        return None
    
    async def _check_anti_mat(self, user_id: int, chat_id: int, message: Message, settings: Dict) -> Optional[Violation]:
        """Проверка на мат"""
        text = message.text.lower()
        word_list = settings.get("word_list", [])
        partial_match = settings.get("partial_match", True)
        
        found_words = []
        
        for bad_word in word_list:
            if partial_match:
                if bad_word in text:
                    found_words.append(bad_word)
            else:
                # Точное совпадение слова
                words = text.split()
                if bad_word in words:
                    found_words.append(bad_word)
        
        if found_words:
            return Violation(
                user_id=user_id,
                chat_id=chat_id,
                filter_type=FilterType.ANTI_MAT,
                message=message,
                details={"found_words": found_words}
            )
        
        return None
    
    async def _check_anti_links(self, user_id: int, chat_id: int, message: Message, settings: Dict) -> Optional[Violation]:
        """Проверка на ссылки"""
        if not message.text:
            return None
        
        # Поиск ссылок в тексте
        url_pattern = r'(https?://[^\s]+|www\.[^\s]+)'
        urls = re.findall(url_pattern, message.text)
        
        if not urls:
            return None
        
        # Если разрешены все ссылки
        if settings.get("allow_all", False):
            return None
        
        allowed_domains = settings.get("allowed_domains", [])
        blocked_domains = settings.get("blocked_domains", [])
        
        for url in urls:
            # Извлечение домена
            domain = self._extract_domain(url)
            
            # Проверка белого списка
            if domain in allowed_domains:
                continue
            
            # Проверка черного списка
            if domain in blocked_domains:
                return Violation(
                    user_id=user_id,
                    chat_id=chat_id,
                    filter_type=FilterType.ANTI_LINKS,
                    message=message,
                    details={"blocked_domain": domain, "url": url}
                )
            
            # Если есть белый список, но домена в нем нет - нарушение
            if allowed_domains and domain not in allowed_domains:
                return Violation(
                    user_id=user_id,
                    chat_id=chat_id,
                    filter_type=FilterType.ANTI_LINKS,
                    message=message,
                    details={"unauthorized_domain": domain, "url": url}
                )
        
        return None
    
    async def _check_anti_flood(self, user_id: int, chat_id: int, message: Message, settings: Dict) -> Optional[Violation]:
        """Проверка на флуд"""
        # Эта проверка уже частично выполнена в anti-spam
        # Здесь можно добавить дополнительные проверки
        return None
    
    async def _check_anti_caps(self, user_id: int, chat_id: int, message: Message, settings: Dict) -> Optional[Violation]:
        """Проверка на капс (заглавные буквы)"""
        if not message.text:
            return None
        
        text = message.text
        min_length = settings.get("min_message_length", 5)
        
        if len(text) < min_length:
            return None
        
        # Подсчет заглавных букв
        caps_count = sum(1 for c in text if c.isupper())
        total_letters = sum(1 for c in text if c.isalpha())
        
        if total_letters == 0:
            return None
        
        caps_percentage = (caps_count / total_letters) * 100
        max_percentage = settings.get("max_caps_percentage", 70)
        
        if caps_percentage > max_percentage:
            return Violation(
                user_id=user_id,
                chat_id=chat_id,
                filter_type=FilterType.ANTI_CAPS,
                message=message,
                details={
                    "caps_percentage": caps_percentage,
                    "max_allowed": max_percentage,
                    "caps_count": caps_count,
                    "total_letters": total_letters
                }
            )
        
        return None
    
    async def _check_anti_stickers(self, user_id: int, chat_id: int, message: Message, settings: Dict) -> Optional[Violation]:
        """Проверка на стикеры"""
        if not message.sticker:
            return None
        
        max_per_minute = settings.get("max_per_minute", 5)
        cache_key = (user_id, chat_id, "stickers")
        
        now = datetime.now()
        minute_ago = now - timedelta(minutes=1)
        
        if cache_key not in self._flood_cache:
            self._flood_cache[cache_key] = []
        
        # Очистка старых записей
        self._flood_cache[cache_key] = [
            ts for ts in self._flood_cache[cache_key]
            if ts > minute_ago
        ]
        
        # Добавление текущего стикера
        self._flood_cache[cache_key].append(now)
        
        # Проверка лимита
        if len(self._flood_cache[cache_key]) > max_per_minute:
            return Violation(
                user_id=user_id,
                chat_id=chat_id,
                filter_type=FilterType.ANTI_STICKERS,
                message=message,
                details={
                    "stickers_count": len(self._flood_cache[cache_key]),
                    "max_allowed": max_per_minute
                }
            )
        
        return None
    
    async def _check_anti_voice(self, user_id: int, chat_id: int, message: Message, settings: Dict) -> Optional[Violation]:
        """Проверка на голосовые сообщения"""
        if not message.voice:
            return None
        
        max_per_minute = settings.get("max_per_minute", 3)
        cache_key = (user_id, chat_id, "voice")
        
        now = datetime.now()
        minute_ago = now - timedelta(minutes=1)
        
        if cache_key not in self._flood_cache:
            self._flood_cache[cache_key] = []
        
        # Очистка старых записей
        self._flood_cache[cache_key] = [
            ts for ts in self._flood_cache[cache_key]
            if ts > minute_ago
        ]
        
        # Добавление текущего голосового
        self._flood_cache[cache_key].append(now)
        
        # Проверка лимита
        if len(self._flood_cache[cache_key]) > max_per_minute:
            return Violation(
                user_id=user_id,
                chat_id=chat_id,
                filter_type=FilterType.ANTI_VOICE,
                message=message,
                details={
                    "voice_count": len(self._flood_cache[cache_key]),
                    "max_allowed": max_per_minute
                }
            )
        
        return None
    
    def _extract_domain(self, url: str) -> str:
        """Извлечение домена из URL"""
        # Удаление протокола
        if '://' in url:
            url = url.split('://')[1]
        
        # Удаление пути
        if '/' in url:
            url = url.split('/')[0]
        
        # Удаление www
        if url.startswith('www.'):
            url = url[4:]
        
        return url.lower()
    
    async def _is_whitelisted(self, user_id: int, chat_id: int, message: Message, settings: Dict) -> bool:
        """Проверка белого списка"""
        whitelist = settings.get("whitelist", {})
        
        # Проверка пользователя
        if user_id in whitelist.get("users", []):
            return True
        
        # Проверка админов чата
        try:
            chat_member = await self.bot.get_chat_member(chat_id, user_id)
            if chat_member.status in ["administrator", "creator"]:
                return True
        except:
            pass
        
        # Проверка слов в белом списке
        if message.text:
            text_lower = message.text.lower()
            whitelist_words = whitelist.get("words", [])
            for word in whitelist_words:
                if word in text_lower:
                    return True
        
        # Проверка ссылок в белом списке
        if message.text:
            url_pattern = r'(https?://[^\s]+|www\.[^\s]+)'
            urls = re.findall(url_pattern, message.text)
            whitelist_links = whitelist.get("links", [])
            
            for url in urls:
                domain = self._extract_domain(url)
                if domain in whitelist_links:
                    return True
        
        return False
    
    async def handle_violation(self, violation: Violation):
        """Обработка нарушения"""
        chat_id = violation.chat_id
        user_id = violation.user_id
        
        # Получение настроек чата
        settings = await self.get_chat_settings(chat_id)
        filter_settings = settings.get("filters", {}).get(violation.filter_type.value, {})
        actions = filter_settings.get("actions", [])
        
        # Выполнение действий
        for action in actions:
            if action == ActionType.DELETE.value:
                await self._delete_message(violation)
            
            elif action == ActionType.WARN.value:
                await self._warn_user(violation)
            
            elif action == ActionType.MUTE.value:
                await self._mute_user(violation, settings)
            
            elif action == ActionType.BAN.value:
                await self._ban_user(violation)
            
            elif action == ActionType.NOTIFY.value:
                await self._notify_admins(violation, settings)
    
    async def _delete_message(self, violation: Violation):
        """Удаление сообщения"""
        try:
            if violation.message:
                await violation.message.delete()
                
                # Логирование
                await self.admin_system.security.log_action(
                    user_id=violation.user_id,
                    action_type=7,  # MESSAGE_DELETED
                    action_data={
                        "chat_id": violation.chat_id,
                        "filter_type": violation.filter_type.value,
                        "details": violation.details
                    },
                    chat_id=violation.chat_id
                )
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")
    
    async def _warn_user(self, violation: Violation):
        """Выдать предупреждение пользователю"""
        db = self.admin_system.database
        
        # Получение пользователя
        user = await db.get_user(violation.user_id)
        if not user:
            return
        
        # Увеличение счетчика варнов
        user.warnings += 1
        
        # Проверка на бан
        settings = await self.get_chat_settings(violation.chat_id)
        warnings_to_ban = settings.get("warnings_to_ban", 3)
        
        if user.warnings >= warnings_to_ban:
            await self._ban_user(violation)
        
        await db.update_user(user)
        
        # Отправка уведомления пользователю
        try:
            warning_text = f"⚠️ Вы получили предупреждение!\n"
            warning_text += f"Причина: {violation.filter_type.value}\n"
            warning_text += f"Всего предупреждений: {user.warnings}/{warnings_to_ban}\n"
            warning_text += f"При достижении {warnings_to_ban} последует бан."
            
            await self.bot.send_message(
                chat_id=violation.user_id,
                text=warning_text
            )
        except:
            pass  # Пользователь может быть недоступен в ЛС
    
    async def _mute_user(self, violation: Violation, settings: Dict):
        """Замутить пользователя"""
        mute_duration = settings.get("mute_duration", 300)  # 5 минут
        
        try:
            until_date = datetime.now() + timedelta(seconds=mute_duration)
            
            await self.bot.restrict_chat_member(
                chat_id=violation.chat_id,
                user_id=violation.user_id,
                permissions=ChatPermissions(
                    can_send_messages=False,
                    can_send_media_messages=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False
                ),
                until_date=until_date
            )
            
            # Уведомление в чат
            notification = f"👤 Пользователь был замучен на {mute_duration // 60} минут.\n"
            notification += f"Причина: {violation.filter_type.value}"
            
            await self.bot.send_message(
                chat_id=violation.chat_id,
                text=notification
            )
            
        except Exception as e:
            logger.error(f"Ошибка при муте пользователя: {e}")
    
    async def _ban_user(self, violation: Violation):
        """Забанить пользователя"""
        try:
            await self.bot.ban_chat_member(
                chat_id=violation.chat_id,
                user_id=violation.user_id
            )
            
            # Уведомление в чат
            notification = f"🚫 Пользователь был забанен.\n"
            notification += f"Причина: {violation.filter_type.value}"
            
            await self.bot.send_message(
                chat_id=violation.chat_id,
                text=notification
            )
            
        except Exception as e:
            logger.error(f"Ошибка при бане пользователя: {e}")
    
    async def _notify_admins(self, violation: Violation, settings: Dict):
        """Уведомить админов о нарушении"""
        if not settings.get("notify_admins", True):
            return
        
        chat_id = violation.chat_id
        
        try:
            # Получение списка админов
            admins = await self.bot.get_chat_administrators(chat_id)
            
            notification = f"🚨 Нарушение правил в чате\n\n"
            notification += f"👤 Пользователь: {violation.user_id}\n"
            notification += f"🔍 Тип нарушения: {violation.filter_type.value}\n"
            notification += f"⏰ Время: {violation.timestamp.strftime('%H:%M:%S')}\n"
            
            if violation.details:
                notification += f"📋 Детали: {violation.details}\n"
            
            # Отправка уведомления каждому админу
            for admin in admins:
                try:
                    await self.bot.send_message(
                        chat_id=admin.user.id,
                        text=notification
                    )
                except:
                    continue  # Админ может быть недоступен
            
        except Exception as e:
            logger.error(f"Ошибка при уведомлении админов: {e}")
    
    async def get_chat_settings(self, chat_id: int) -> Dict:
        """Получить настройки автомодерации для чата"""
        db = self.admin_system.database
        
        chat = await db.get_chat(chat_id)
        if not chat:
            return self.default_settings
        
        settings = chat.settings.get("automoderation", {})
        
        # Объединение с настройками по умолчанию
        result = self.default_settings.copy()
        
        # Рекурсивное обновление настроек
        def update_dict(d, u):
            for k, v in u.items():
                if isinstance(v, dict) and k in d and isinstance(d[k], dict):
                    update_dict(d[k], v)
                else:
                    d[k] = v
        
        if settings:
            update_dict(result, settings)
        
        return result
    
    async def update_chat_settings(self, chat_id: int, new_settings: Dict):
        """Обновить настройки автомодерации для чата"""
        db = self.admin_system.database
        
        chat = await db.get_chat(chat_id)
        if not chat:
            return False
        
        # Обновление настроек
        if "automoderation" not in chat.settings:
            chat.settings["automoderation"] = {}
        
        # Рекурсивное обновление
        def update_dict(d, u):
            for k, v in u.items():
                if isinstance(v, dict) and k in d and isinstance(d[k], dict):
                    update_dict(d[k], v)
                else:
                    d[k] = v
        
        update_dict(chat.settings["automoderation"], new_settings)
        
        await db.update_chat(chat)
        return True
    
    async def show_settings(self, callback: CallbackQuery, chat_id: Optional[int] = None):
        """Показать настройки автомодерации"""
        if not chat_id:
            if callback.message.chat.type == "private":
                await callback.message.edit_text("❌ Эта команда работает только в чатах.")
                return
            chat_id = callback.message.chat.id
        
        # Проверка прав
        security = self.admin_system.security
        user_id = callback.from_user.id
        
        if not await security.has_permission(user_id, "moderation.automod"):
            await callback.message.edit_text("❌ У вас нет прав для управления автомодерацией.")
            return
        
        settings = await self.get_chat_settings(chat_id)
        
        text = "🤖 Настройки автомодерации\n\n"
        text += f"Чат ID: {chat_id}\n"
        text += f"Статус: {'✅ Включена' if settings.get('enabled') else '❌ Выключена'}\n\n"
        
        text += "Фильтры:\n"
        filters = settings.get("filters", {})
        
        for filter_type, filter_settings in filters.items():
            enabled = "✅" if filter_settings.get("enabled", False) else "❌"
            text += f"{enabled} {filter_type.replace('_', ' ').title()}\n"
        
        text += "\nДействия при нарушении:\n"
        for filter_type, filter_settings in filters.items():
            if filter_settings.get("enabled", False):
                actions = filter_settings.get("actions", [])
                if actions:
                    text += f"• {filter_type}: {', '.join(actions)}\n"
        
        buttons = [
            ("⚙️ Изменить настройки", f"automod_edit:{chat_id}"),
            ("📊 Статистика нарушений", f"automod_stats:{chat_id}"),
            ("📝 Белый список", f"automod_whitelist:{chat_id}"),
            ("◀️ Назад", "admin_moderation")
        ]
        
        keyboard = create_keyboard(buttons, columns=2)
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def get_violation_stats(self, chat_id: int, days: int = 7) -> Dict[str, Any]:
        """Получить статистику нарушений"""
        db = self.admin_system.database
        
        start_date = datetime.now() - timedelta(days=days)
        
        # Получение логов действий
        logs, total = await db.get_action_logs(
            chat_id=chat_id,
            start_date=start_date,
            limit=1000
        )
        
        stats = {
            "total": 0,
            "by_filter": {},
            "by_user": {},
            "by_day": {},
            "top_violators": []
        }
        
        # Фильтрация логов по нарушениям
        for log in logs:
            if log.action_type == 7:  # MESSAGE_DELETED (предполагаем, что это нарушение)
                action_data = log.action_data
                filter_type = action_data.get("filter_type")
                
                if filter_type:
                    stats["total"] += 1
                    
                    # По фильтрам
                    stats["by_filter"][filter_type] = stats["by_filter"].get(filter_type, 0) + 1
                    
                    # По пользователям
                    stats["by_user"][log.user_id] = stats["by_user"].get(log.user_id, 0) + 1
                    
                    # По дням
                    day_str = log.timestamp.strftime("%Y-%m-%d")
                    stats["by_day"][day_str] = stats["by_day"].get(day_str, 0) + 1
        
        # Топ нарушителей
        top_violators = sorted(stats["by_user"].items(), key=lambda x: x[1], reverse=True)[:10]
        stats["top_violators"] = top_violators
        
        return stats