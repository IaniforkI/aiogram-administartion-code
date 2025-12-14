import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum

from aiogram import Bot, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from .models import Chat, ChatAdmin, User, ActionType
from .ui import create_keyboard, create_pagination_keyboard, format_chat_info
from .security import require_chat_admin, require_admin, ChatAdminLevel
from .database import DatabaseManager

logger = logging.getLogger(__name__)

class ChatCommandType(Enum):
    """Типы команд для чата"""
    DELETE = "delete"
    BAN = "ban"
    UNBAN = "unban"
    MUTE = "mute"
    UNMUTE = "unmute"
    WARN = "warn"
    UNWARN = "unwarn"
    PIN = "pin"
    UNPIN = "unpin"
    RULES = "rules"
    INFO = "info"
    STATS = "stats"
    ADMINS = "admins"

class ChatSettingsStates(StatesGroup):
    """Состояния для настройки чата"""
    waiting_for_rules = State()
    waiting_for_welcome = State()
    waiting_for_farewell = State()
    waiting_for_max_warnings = State()

class ChatManagementManager:
    """Менеджер управления чатами"""
    
    def __init__(self, admin_system):
        self.admin_system = admin_system
        self.bot = admin_system.bot
        
        # Кэш для быстрого доступа
        self._chat_cache: Dict[int, Chat] = {}
        self._chat_admins_cache: Dict[int, List[ChatAdmin]] = {}
        
    async def setup_handlers(self, router):
        """Настройка обработчиков команд"""
        
        # Команды для админов чата
        @router.message(Command("del"))
        @require_chat_admin(ChatAdminLevel.ASSISTANT)
        async def delete_message(message: Message, command: CommandObject):
            """Удаление сообщения"""
            await self.handle_delete_command(message, command)
        
        @router.message(Command("purge"))
        @require_chat_admin(ChatAdminLevel.MODERATOR)
        async def purge_messages(message: Message, command: CommandObject):
            """Очистка сообщений"""
            await self.handle_purge_command(message, command)
        
        @router.message(Command("ban"))
        @require_chat_admin(ChatAdminLevel.MODERATOR)
        async def ban_user(message: Message, command: CommandObject):
            """Бан пользователя"""
            await self.handle_ban_command(message, command)
        
        @router.message(Command("tban"))
        @require_chat_admin(ChatAdminLevel.MODERATOR)
        async def temp_ban_user(message: Message, command: CommandObject):
            """Временный бан пользователя"""
            await self.handle_temp_ban_command(message, command)
        
        @router.message(Command("unban"))
        @require_chat_admin(ChatAdminLevel.MODERATOR)
        async def unban_user(message: Message, command: CommandObject):
            """Разбан пользователя"""
            await self.handle_unban_command(message, command)
        
        @router.message(Command("mute"))
        @require_chat_admin(ChatAdminLevel.MODERATOR)
        async def mute_user(message: Message, command: CommandObject):
            """Мут пользователя"""
            await self.handle_mute_command(message, command)
        
        @router.message(Command("unmute"))
        @require_chat_admin(ChatAdminLevel.MODERATOR)
        async def unmute_user(message: Message, command: CommandObject):
            """Размут пользователя"""
            await self.handle_unmute_command(message, command)
        
        @router.message(Command("warn"))
        @require_chat_admin(ChatAdminLevel.ASSISTANT)
        async def warn_user(message: Message, command: CommandObject):
            """Предупреждение пользователя"""
            await self.handle_warn_command(message, command)
        
        @router.message(Command("unwarn"))
        @require_chat_admin(ChatAdminLevel.MODERATOR)
        async def unwarn_user(message: Message, command: CommandObject):
            """Снятие предупреждения"""
            await self.handle_unwarn_command(message, command)
        
        @router.message(Command("warns"))
        @require_chat_admin(ChatAdminLevel.OBSERVER)
        async def show_warns(message: Message, command: CommandObject):
            """Показать предупреждения пользователя"""
            await self.handle_warns_command(message, command)
        
        @router.message(Command("pin"))
        @require_chat_admin(ChatAdminLevel.ASSISTANT)
        async def pin_message(message: Message):
            """Закрепление сообщения"""
            await self.handle_pin_command(message)
        
        @router.message(Command("unpin"))
        @require_chat_admin(ChatAdminLevel.ASSISTANT)
        async def unpin_message(message: Message):
            """Открепление сообщения"""
            await self.handle_unpin_command(message)
        
        @router.message(Command("rules"))
        async def show_rules(message: Message):
            """Показать правила чата"""
            await self.handle_rules_command(message)
        
        @router.message(Command("info"))
        @require_chat_admin(ChatAdminLevel.OBSERVER)
        async def user_info(message: Message, command: CommandObject):
            """Информация о пользователе"""
            await self.handle_info_command(message, command)
        
        @router.message(Command("chatstats"))
        @require_chat_admin(ChatAdminLevel.OBSERVER)
        async def chat_stats(message: Message):
            """Статистика чата"""
            await self.handle_chat_stats_command(message)
        
        @router.message(Command("admins"))
        async def show_admins(message: Message):
            """Показать админов чата"""
            await self.handle_admins_command(message)
        
        # Команда для пользователей
        @router.message(Command("profile"))
        async def user_profile(message: Message):
            """Профиль пользователя"""
            await self.handle_profile_command(message)
        
        @router.message(Command("mystats"))
        async def my_stats(message: Message):
            """Моя статистика"""
            await self.handle_my_stats_command(message)
        
        @router.message(Command("top"))
        async def top_users(message: Message):
            """Топ пользователей"""
            await self.handle_top_command(message)
    
    async def handle_delete_command(self, message: Message, command: CommandObject):
        """Обработка команды /del"""
        chat_id = message.chat.id
        
        if message.reply_to_message:
            # Удаление ответного сообщения
            try:
                await message.reply_to_message.delete()
                await message.delete()
                
                # Логирование
                security = self.admin_system.security
                await security.log_action(
                    user_id=message.from_user.id,
                    action_type=7,  # MESSAGE_DELETED
                    action_data={
                        "chat_id": chat_id,
                        "message_id": message.reply_to_message.message_id,
                        "command": "del"
                    },
                    chat_id=chat_id
                )
                
                # Краткое подтверждение
                confirmation = await message.answer("✅ Сообщение удалено")
                await asyncio.sleep(3)
                await confirmation.delete()
                
            except Exception as e:
                await message.answer(f"❌ Не удалось удалить сообщение: {e}")
        
        elif command.args:
            # Удаление по ID сообщения
            try:
                message_id = int(command.args)
                await self.bot.delete_message(
                    chat_id=chat_id,
                    message_id=message_id
                )
                await message.delete()
                
                # Логирование
                security = self.admin_system.security
                await security.log_action(
                    user_id=message.from_user.id,
                    action_type=7,  # MESSAGE_DELETED
                    action_data={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "command": "del"
                    },
                    chat_id=chat_id
                )
                
                # Краткое подтверждение
                confirmation = await message.answer(f"✅ Сообщение {message_id} удалено")
                await asyncio.sleep(3)
                await confirmation.delete()
                
            except ValueError:
                await message.answer("❌ Неверный формат ID сообщения")
            except Exception as e:
                await message.answer(f"❌ Не удалось удалить сообщение: {e}")
        
        else:
            await message.answer(
                "Использование:\n"
                "• /del - в ответ на сообщение\n"
                "• /del <ID сообщения>"
            )
    
    async def handle_purge_command(self, message: Message, command: CommandObject):
        """Обработка команды /purge"""
        if not command.args:
            await message.answer(
                "Использование:\n"
                "• /purge <кол-во> - удалить N последних сообщений\n"
                "• /purge @username - удалить сообщения пользователя\n"
                "• /purge from:время to:время - удалить за период"
            )
            return
        
        chat_id = message.chat.id
        user_id = message.from_user.id
        
        try:
            if command.args.isdigit():
                # Удаление N сообщений
                count = int(command.args)
                if count < 1 or count > 100:
                    await message.answer("❌ Можно удалить от 1 до 100 сообщений")
                    return
                
                deleted = await self._purge_messages(chat_id, count, user_id)
                await message.answer(f"✅ Удалено {deleted} сообщений")
            
            elif command.args.startswith('@'):
                # Удаление сообщений пользователя
                username = command.args[1:]
                deleted = await self._purge_user_messages(chat_id, username, user_id)
                await message.answer(f"✅ Удалено {deleted} сообщений от @{username}")
            
            elif 'from:' in command.args and 'to:' in command.args:
                # Удаление за период
                await message.answer("⚠️ Эта функция в разработке")
            
            else:
                await message.answer("❌ Неверный формат команды")
                
        except Exception as e:
            logger.error(f"Ошибка при очистке сообщений: {e}")
            await message.answer(f"❌ Ошибка: {e}")
    
    async def _purge_messages(self, chat_id: int, count: int, admin_id: int) -> int:
        """Удаление последних сообщений"""
        deleted = 0
        
        try:
            # Получение последних сообщений
            messages = []
            async for msg in self.bot.client.iter_messages(chat_id, limit=count + 10):
                messages.append(msg)
            
            # Удаление
            for msg in messages[:count]:
                try:
                    await msg.delete()
                    deleted += 1
                    await asyncio.sleep(0.1)  # Задержка для избежания лимитов
                except:
                    continue
            
            # Логирование
            security = self.admin_system.security
            await security.log_action(
                user_id=admin_id,
                action_type=7,  # MESSAGE_DELETED
                action_data={
                    "chat_id": chat_id,
                    "count": deleted,
                    "type": "purge"
                },
                chat_id=chat_id
            )
            
        except Exception as e:
            logger.error(f"Ошибка при массовом удалении: {e}")
        
        return deleted
    
    async def _purge_user_messages(self, chat_id: int, username: str, admin_id: int) -> int:
        """Удаление сообщений пользователя"""
        deleted = 0
        
        try:
            # Поиск пользователя
            user = None
            
            # Поиск по username в участниках чата
            async for member in self.bot.get_chat_members(chat_id):
                if member.user.username and member.user.username.lower() == username.lower():
                    user = member.user
                    break
            
            if not user:
                return 0
            
            # Удаление сообщений
            async for msg in self.bot.client.iter_messages(chat_id, from_user=user.id, limit=100):
                try:
                    await msg.delete()
                    deleted += 1
                    await asyncio.sleep(0.1)
                except:
                    continue
            
            # Логирование
            security = self.admin_system.security
            await security.log_action(
                user_id=admin_id,
                action_type=7,  # MESSAGE_DELETED
                action_data={
                    "chat_id": chat_id,
                    "target_user_id": user.id,
                    "username": username,
                    "count": deleted,
                    "type": "purge_user"
                },
                chat_id=chat_id
            )
            
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщений пользователя: {e}")
        
        return deleted
    
    async def handle_ban_command(self, message: Message, command: CommandObject):
        """Обработка команды /ban"""
        if not command.args:
            await message.answer(
                "Использование:\n"
                "• /ban @username [причина]\n"
                "• /ban <ID пользователя> [причина]"
            )
            return
        
        chat_id = message.chat.id
        admin_id = message.from_user.id
        
        # Разбор аргументов
        args = command.args.split(' ', 1)
        target = args[0]
        reason = args[1] if len(args) > 1 else "Не указана"
        
        try:
            # Определение пользователя
            user_id = await self._resolve_user_identifier(chat_id, target)
            if not user_id:
                await message.answer("❌ Пользователь не найден")
                return
            
            # Проверка, что не пытаемся забанить админа или себя
            if user_id == admin_id:
                await message.answer("❌ Нельзя забанить самого себя")
                return
            
            try:
                chat_member = await self.bot.get_chat_member(chat_id, user_id)
                if chat_member.status in ["administrator", "creator"]:
                    await message.answer("❌ Нельзя забанить администратора чата")
                    return
            except:
                pass
            
            # Бан
            await self.bot.ban_chat_member(
                chat_id=chat_id,
                user_id=user_id
            )
            
            # Уведомление в чат
            admin_name = message.from_user.full_name
            target_name = await self._get_user_name(user_id)
            
            notification = f"🚫 Пользователь {target_name} был забанен.\n"
            notification += f"👮‍♂️ Админ: {admin_name}\n"
            notification += f"📋 Причина: {reason}"
            
            await message.answer(notification)
            
            # Логирование
            security = self.admin_system.security
            await security.log_action(
                user_id=admin_id,
                action_type=2,  # USER_BLOCKED
                action_data={
                    "target_user_id": user_id,
                    "chat_id": chat_id,
                    "reason": reason,
                    "command": "ban"
                },
                chat_id=chat_id
            )
            
            # Обновление данных пользователя
            await self._update_user_after_ban(user_id, chat_id, reason)
            
        except Exception as e:
            logger.error(f"Ошибка при бане пользователя: {e}")
            await message.answer(f"❌ Ошибка: {e}")
    
    async def handle_temp_ban_command(self, message: Message, command: CommandObject):
        """Обработка команды /tban (временный бан)"""
        if not command.args:
            await message.answer(
                "Использование:\n"
                "• /tban @username 1h30m [причина]\n"
                "• /tban <ID> 1d [причина]\n\n"
                "Доступные единицы времени:\n"
                "m - минуты, h - часы, d - дни"
            )
            return
        
        chat_id = message.chat.id
        admin_id = message.from_user.id
        
        # Разбор аргументов
        parts = command.args.split(' ', 2)
        
        if len(parts) < 2:
            await message.answer("❌ Неверный формат. Укажите пользователя и время.")
            return
        
        target = parts[0]
        time_str = parts[1]
        reason = parts[2] if len(parts) > 2 else "Не указана"
        
        try:
            # Парсинг времени
            duration = self._parse_duration(time_str)
            if not duration:
                await message.answer("❌ Неверный формат времени")
                return
            
            # Определение пользователя
            user_id = await self._resolve_user_identifier(chat_id, target)
            if not user_id:
                await message.answer("❌ Пользователь не найден")
                return
            
            # Проверка на админа
            if user_id == admin_id:
                await message.answer("❌ Нельзя забанить самого себя")
                return
            
            try:
                chat_member = await self.bot.get_chat_member(chat_id, user_id)
                if chat_member.status in ["administrator", "creator"]:
                    await message.answer("❌ Нельзя забанить администратора чата")
                    return
            except:
                pass
            
            # Временный бан
            until_date = datetime.now() + timedelta(seconds=duration)
            
            await self.bot.ban_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                until_date=until_date
            )
            
            # Уведомление в чат
            admin_name = message.from_user.full_name
            target_name = await self._get_user_name(user_id)
            time_text = self._format_duration(duration)
            
            notification = f"⏰ Пользователь {target_name} забанен на {time_text}.\n"
            notification += f"👮‍♂️ Админ: {admin_name}\n"
            notification += f"📋 Причина: {reason}"
            
            await message.answer(notification)
            
            # Логирование
            security = self.admin_system.security
            await security.log_action(
                user_id=admin_id,
                action_type=2,  # USER_BLOCKED
                action_data={
                    "target_user_id": user_id,
                    "chat_id": chat_id,
                    "reason": reason,
                    "duration": duration,
                    "command": "tban"
                },
                chat_id=chat_id
            )
            
        except Exception as e:
            logger.error(f"Ошибка при временном бане: {e}")
            await message.answer(f"❌ Ошибка: {e}")
    
    async def handle_unban_command(self, message: Message, command: CommandObject):
        """Обработка команды /unban"""
        if not command.args:
            await message.answer(
                "Использование:\n"
                "• /unban @username\n"
                "• /unban <ID пользователя>"
            )
            return
        
        chat_id = message.chat.id
        admin_id = message.from_user.id
        
        target = command.args.strip()
        
        try:
            # Определение пользователя
            user_id = await self._resolve_user_identifier(chat_id, target)
            if not user_id:
                await message.answer("❌ Пользователь не найден")
                return
            
            # Разбан
            await self.bot.unban_chat_member(
                chat_id=chat_id,
                user_id=user_id
            )
            
            # Уведомление в чат
            admin_name = message.from_user.full_name
            target_name = await self._get_user_name(user_id)
            
            notification = f"✅ Пользователь {target_name} разбанен.\n"
            notification += f"👮‍♂️ Админ: {admin_name}"
            
            await message.answer(notification)
            
            # Логирование
            security = self.admin_system.security
            await security.log_action(
                user_id=admin_id,
                action_type=3,  # USER_UNBLOCKED
                action_data={
                    "target_user_id": user_id,
                    "chat_id": chat_id,
                    "command": "unban"
                },
                chat_id=chat_id
            )
            
        except Exception as e:
            logger.error(f"Ошибка при разбане: {e}")
            await message.answer(f"❌ Ошибка: {e}")
    
    async def handle_mute_command(self, message: Message, command: CommandObject):
        """Обработка команды /mute"""
        if not command.args:
            await message.answer(
                "Использование:\n"
                "• /mute @username [время] [причина]\n"
                "• /mute <ID> 1h [причина]\n\n"
                "По умолчанию: 1 час"
            )
            return
        
        chat_id = message.chat.id
        admin_id = message.from_user.id
        
        # Разбор аргументов
        parts = command.args.split(' ', 2)
        
        target = parts[0]
        time_str = parts[1] if len(parts) > 1 else "1h"
        reason = parts[2] if len(parts) > 2 else "Не указана"
        
        try:
            # Парсинг времени
            duration = self._parse_duration(time_str)
            if not duration:
                await message.answer("❌ Неверный формат времени")
                return
            
            # Определение пользователя
            user_id = await self._resolve_user_identifier(chat_id, target)
            if not user_id:
                await message.answer("❌ Пользователь не найден")
                return
            
            # Проверка на админа
            if user_id == admin_id:
                await message.answer("❌ Нельзя замутить самого себя")
                return
            
            try:
                chat_member = await self.bot.get_chat_member(chat_id, user_id)
                if chat_member.status in ["administrator", "creator"]:
                    await message.answer("❌ Нельзя замутить администратора чата")
                    return
            except:
                pass
            
            # Мут
            until_date = datetime.now() + timedelta(seconds=duration)
            
            await self.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=ChatPermissions(
                    can_send_messages=False,
                    can_send_media_messages=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False
                ),
                until_date=until_date
            )
            
            # Уведомление в чат
            admin_name = message.from_user.full_name
            target_name = await self._get_user_name(user_id)
            time_text = self._format_duration(duration)
            
            notification = f"🔇 Пользователь {target_name} замучен на {time_text}.\n"
            notification += f"👮‍♂️ Админ: {admin_name}\n"
            notification += f"📋 Причина: {reason}"
            
            await message.answer(notification)
            
            # Логирование
            security = self.admin_system.security
            await security.log_action(
                user_id=admin_id,
                action_type=15,  # USER_MUTED
                action_data={
                    "target_user_id": user_id,
                    "chat_id": chat_id,
                    "reason": reason,
                    "duration": duration,
                    "command": "mute"
                },
                chat_id=chat_id
            )
            
        except Exception as e:
            logger.error(f"Ошибка при муте: {e}")
            await message.answer(f"❌ Ошибка: {e}")
    
    async def handle_unmute_command(self, message: Message, command: CommandObject):
        """Обработка команды /unmute"""
        if not command.args:
            await message.answer(
                "Использование:\n"
                "• /unmute @username\n"
                "• /unmute <ID пользователя>"
            )
            return
        
        chat_id = message.chat.id
        admin_id = message.from_user.id
        
        target = command.args.strip()
        
        try:
            # Определение пользователя
            user_id = await self._resolve_user_identifier(chat_id, target)
            if not user_id:
                await message.answer("❌ Пользователь не найден")
                return
            
            # Размут
            await self.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=ChatPermissions(
                    can_send_messages=True,
                    can_send_media_messages=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True
                )
            )
            
            # Уведомление в чат
            admin_name = message.from_user.full_name
            target_name = await self._get_user_name(user_id)
            
            notification = f"🔊 Пользователь {target_name} размучен.\n"
            notification += f"👮‍♂️ Админ: {admin_name}"
            
            await message.answer(notification)
            
            # Логирование
            security = self.admin_system.security
            await security.log_action(
                user_id=admin_id,
                action_type=16,  # USER_UNMUTED
                action_data={
                    "target_user_id": user_id,
                    "chat_id": chat_id,
                    "command": "unmute"
                },
                chat_id=chat_id
            )
            
        except Exception as e:
            logger.error(f"Ошибка при размуте: {e}")
            await message.answer(f"❌ Ошибка: {e}")
    
    async def handle_warn_command(self, message: Message, command: CommandObject):
        """Обработка команды /warn"""
        if not command.args:
            await message.answer(
                "Использование:\n"
                "• /warn @username [причина]\n"
                "• /warn <ID пользователя> [причина]"
            )
            return
        
        chat_id = message.chat.id
        admin_id = message.from_user.id
        
        # Разбор аргументов
        args = command.args.split(' ', 1)
        target = args[0]
        reason = args[1] if len(args) > 1 else "Не указана"
        
        try:
            # Определение пользователя
            user_id = await self._resolve_user_identifier(chat_id, target)
            if not user_id:
                await message.answer("❌ Пользователь не найден")
                return
            
            # Проверка на админа
            if user_id == admin_id:
                await message.answer("❌ Нельзя выдать предупреждение самому себе")
                return
            
            try:
                chat_member = await self.bot.get_chat_member(chat_id, user_id)
                if chat_member.status in ["administrator", "creator"]:
                    await message.answer("❌ Нельзя выдать предупреждение администратору чата")
                    return
            except:
                pass
            
            # Получение данных пользователя
            db = DatabaseManager.get_instance()
            user = await db.get_user(user_id)
            
            if not user:
                # Создание записи пользователя
                from .models import User, UserStatus
                user = User(
                    user_id=user_id,
                    first_name=await self._get_user_name(user_id),
                    status=UserStatus.ACTIVE
                )
                await db.add_user(user)
            
            # Добавление предупреждения
            user.warnings += 1
            
            # Получение настроек чата
            chat = await db.get_chat(chat_id)
            max_warnings = chat.settings.get("max_warnings", 3) if chat else 3
            
            # Проверка на бан
            if user.warnings >= max_warnings:
                # Автоматический бан
                await self.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                
                notification = f"🚫 Пользователь {user.full_name} забанен.\n"
                notification += f"Причина: достигнут лимит предупреждений ({user.warnings}/{max_warnings})\n"
                notification += f"👮‍♂️ Админ: {message.from_user.full_name}\n"
                notification += f"📋 Последняя причина: {reason}"
                
                await message.answer(notification)
                
                # Логирование бана
                security = self.admin_system.security
                await security.log_action(
                    user_id=admin_id,
                    action_type=2,  # USER_BLOCKED
                    action_data={
                        "target_user_id": user_id,
                        "chat_id": chat_id,
                        "reason": f"Достигнут лимит предупреждений: {reason}",
                        "warnings_count": user.warnings,
                        "max_warnings": max_warnings,
                        "command": "warn_auto_ban"
                    },
                    chat_id=chat_id
                )
            else:
                # Только предупреждение
                notification = f"⚠️ Пользователь {user.full_name} получил предупреждение.\n"
                notification += f"Всего предупреждений: {user.warnings}/{max_warnings}\n"
                notification += f"👮‍♂️ Админ: {message.from_user.full_name}\n"
                notification += f"📋 Причина: {reason}"
                
                await message.answer(notification)
                
                # Отправка уведомления пользователю
                try:
                    user_notification = f"⚠️ Вы получили предупреждение в чате!\n\n"
                    user_notification += f"Причина: {reason}\n"
                    user_notification += f"Всего предупреждений: {user.warnings}/{max_warnings}\n"
                    user_notification += f"При достижении {max_warnings} последует бан."
                    
                    await self.bot.send_message(
                        chat_id=user_id,
                        text=user_notification
                    )
                except:
                    pass  # Пользователь может быть недоступен в ЛС
            
            # Обновление пользователя
            await db.update_user(user)
            
            # Логирование предупреждения
            security = self.admin_system.security
            await security.log_action(
                user_id=admin_id,
                action_type=4,  # USER_WARNED
                action_data={
                    "target_user_id": user_id,
                    "chat_id": chat_id,
                    "reason": reason,
                    "warnings_count": user.warnings,
                    "command": "warn"
                },
                chat_id=chat_id
            )
            
        except Exception as e:
            logger.error(f"Ошибка при выдаче предупреждения: {e}")
            await message.answer(f"❌ Ошибка: {e}")
    
    async def handle_unwarn_command(self, message: Message, command: CommandObject):
        """Обработка команды /unwarn"""
        if not command.args:
            await message.answer(
                "Использование:\n"
                "• /unwarn @username [номер предупреждения]\n"
                "• /unwarn <ID> [номер]"
            )
            return
        
        chat_id = message.chat.id
        admin_id = message.from_user.id
        
        # Разбор аргументов
        args = command.args.split(' ', 1)
        target = args[0]
        warn_number = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
        
        try:
            # Определение пользователя
            user_id = await self._resolve_user_identifier(chat_id, target)
            if not user_id:
                await message.answer("❌ Пользователь не найден")
                return
            
            # Получение данных пользователя
            db = DatabaseManager.get_instance()
            user = await db.get_user(user_id)
            
            if not user or user.warnings <= 0:
                await message.answer("✅ У пользователя нет предупреждений")
                return
            
            # Снятие предупреждения
            if warn_number and 1 <= warn_number <= user.warnings:
                # Снятие конкретного предупреждения
                # В реальной системе нужно хранить историю предупреждений
                user.warnings -= 1
                await message.answer(f"✅ Снято предупреждение #{warn_number}")
            else:
                # Снятие последнего предупреждения
                user.warnings = max(0, user.warnings - 1)
                await message.answer(f"✅ Снято одно предупреждение. Осталось: {user.warnings}")
            
            # Обновление пользователя
            await db.update_user(user)
            
            # Логирование
            security = self.admin_system.security
            await security.log_action(
                user_id=admin_id,
                action_type=17,  # USER_UNWARNED
                action_data={
                    "target_user_id": user_id,
                    "chat_id": chat_id,
                    "warnings_count": user.warnings,
                    "command": "unwarn"
                },
                chat_id=chat_id
            )
            
        except Exception as e:
            logger.error(f"Ошибка при снятии предупреждения: {e}")
            await message.answer(f"❌ Ошибка: {e}")
    
    async def handle_warns_command(self, message: Message, command: CommandObject):
        """Обработка команды /warns"""
        chat_id = message.chat.id
        
        if command.args:
            target = command.args.strip()
        elif message.reply_to_message:
            target = f"@{message.reply_to_message.from_user.username}" if message.reply_to_message.from_user.username else str(message.reply_to_message.from_user.id)
        else:
            await message.answer(
                "Использование:\n"
                "• /warns @username\n"
                "• /warns <ID пользователя>\n"
                "• /warns - в ответ на сообщение"
            )
            return
        
        try:
            # Определение пользователя
            user_id = await self._resolve_user_identifier(chat_id, target)
            if not user_id:
                await message.answer("❌ Пользователь не найден")
                return
            
            # Получение данных пользователя
            db = DatabaseManager.get_instance()
            user = await db.get_user(user_id)
            
            if not user:
                await message.answer("✅ У пользователя нет предупреждений")
                return
            
            # Формирование ответа
            chat = await db.get_chat(chat_id)
            max_warnings = chat.settings.get("max_warnings", 3) if chat else 3
            
            text = f"⚠️ Предупреждения пользователя\n\n"
            text += f"👤 Пользователь: {user.full_name}\n"
            text += f"🆔 ID: {user.user_id}\n\n"
            text += f"📊 Всего предупреждений: {user.warnings}/{max_warnings}\n"
            
            if user.warnings > 0:
                text += f"🚨 До бана осталось: {max_warnings - user.warnings} предупреждений\n"
            
            if user.warnings >= max_warnings:
                text += "🚫 Лимит предупреждений достигнут!\n"
            
            await message.answer(text)
            
        except Exception as e:
            logger.error(f"Ошибка при получении предупреждений: {e}")
            await message.answer(f"❌ Ошибка: {e}")
    
    async def handle_pin_command(self, message: Message):
        """Обработка команды /pin"""
        if not message.reply_to_message:
            await message.answer("❌ Используйте команду в ответ на сообщение")
            return
        
        chat_id = message.chat.id
        admin_id = message.from_user.id
        
        try:
            # Закрепление сообщения
            await self.bot.pin_chat_message(
                chat_id=chat_id,
                message_id=message.reply_to_message.message_id,
                disable_notification=True
            )
            
            await message.delete()
            
            # Логирование
            security = self.admin_system.security
            await security.log_action(
                user_id=admin_id,
                action_type=18,  # MESSAGE_PINNED
                action_data={
                    "chat_id": chat_id,
                    "message_id": message.reply_to_message.message_id
                },
                chat_id=chat_id
            )
            
        except Exception as e:
            logger.error(f"Ошибка при закреплении сообщения: {e}")
            await message.answer(f"❌ Ошибка: {e}")
    
    async def handle_unpin_command(self, message: Message):
        """Обработка команды /unpin"""
        chat_id = message.chat.id
        admin_id = message.from_user.id
        
        try:
            # Открепление сообщения
            await self.bot.unpin_chat_message(chat_id=chat_id)
            
            await message.answer("✅ Сообщение откреплено")
            
            # Логирование
            security = self.admin_system.security
            await security.log_action(
                user_id=admin_id,
                action_type=19,  # MESSAGE_UNPINNED
                action_data={
                    "chat_id": chat_id
                },
                chat_id=chat_id
            )
            
        except Exception as e:
            logger.error(f"Ошибка при откреплении сообщения: {e}")
            await message.answer(f"❌ Ошибка: {e}")
    
    async def handle_rules_command(self, message: Message):
        """Обработка команды /rules"""
        chat_id = message.chat.id
        
        db = DatabaseManager.get_instance()
        chat = await db.get_chat(chat_id)
        
        if not chat or not chat.settings.get("rules_enabled", False):
            await message.answer("📜 Правила чата не установлены.")
            return
        
        rules = chat.settings.get("rules_text", "")
        if not rules:
            await message.answer("📜 Правила чата не установлены.")
            return
        
        await message.answer(f"📜 Правила чата:\n\n{rules}")
    
    async def handle_info_command(self, message: Message, command: CommandObject):
        """Обработка команды /info"""
        chat_id = message.chat.id
        
        if command.args:
            target = command.args.strip()
        elif message.reply_to_message:
            target = f"@{message.reply_to_message.from_user.username}" if message.reply_to_message.from_user.username else str(message.reply_to_message.from_user.id)
        else:
            await message.answer(
                "Использование:\n"
                "• /info @username\n"
                "• /info <ID пользователя>\n"
                "• /info - в ответ на сообщение"
            )
            return
        
        try:
            # Определение пользователя
            user_id = await self._resolve_user_identifier(chat_id, target)
            if not user_id:
                await message.answer("❌ Пользователь не найден")
                return
            
            # Получение информации
            info = await self._get_user_chat_info(user_id, chat_id)
            await message.answer(info)
            
        except Exception as e:
            logger.error(f"Ошибка при получении информации: {e}")
            await message.answer(f"❌ Ошибка: {e}")
    
    async def handle_chat_stats_command(self, message: Message):
        """Обработка команды /chatstats"""
        chat_id = message.chat.id
        
        try:
            stats = await self._get_chat_stats(chat_id)
            await message.answer(stats)
            
        except Exception as e:
            logger.error(f"Ошибка при получении статистики чата: {e}")
            await message.answer(f"❌ Ошибка: {e}")
    
    async def handle_admins_command(self, message: Message):
        """Обработка команды /admins"""
        chat_id = message.chat.id
        
        try:
            admins_text = await self._get_chat_admins_text(chat_id)
            await message.answer(admins_text)
            
        except Exception as e:
            logger.error(f"Ошибка при получении списка админов: {e}")
            await message.answer(f"❌ Ошибка: {e}")
    
    async def handle_profile_command(self, message: Message):
        """Обработка команды /profile"""
        user_id = message.from_user.id
        chat_id = message.chat.id if message.chat.type != "private" else None
        
        try:
            profile = await self._get_user_profile(user_id, chat_id)
            await message.answer(profile)
            
        except Exception as e:
            logger.error(f"Ошибка при получении профиля: {e}")
            await message.answer(f"❌ Ошибка: {e}")
    
    async def handle_my_stats_command(self, message: Message):
        """Обработка команды /mystats"""
        user_id = message.from_user.id
        
        try:
            stats = await self._get_user_stats(user_id)
            await message.answer(stats)
            
        except Exception as e:
            logger.error(f"Ошибка при получении статистики: {e}")
            await message.answer(f"❌ Ошибка: {e}")
    
    async def handle_top_command(self, message: Message):
        """Обработка команды /top"""
        chat_id = message.chat.id if message.chat.type != "private" else None
        
        try:
            top = await self._get_top_users(chat_id)
            await message.answer(top)
            
        except Exception as e:
            logger.error(f"Ошибка при получении топа: {e}")
            await message.answer(f"❌ Ошибка: {e}")
    
    # Вспомогательные методы
    
    async def _resolve_user_identifier(self, chat_id: int, identifier: str) -> Optional[int]:
        """Определить ID пользователя по идентификатору"""
        # Если это числовой ID
        if identifier.isdigit():
            return int(identifier)
        
        # Если это username
        if identifier.startswith('@'):
            username = identifier[1:]
            
            # Поиск в участниках чата
            async for member in self.bot.get_chat_members(chat_id):
                if member.user.username and member.user.username.lower() == username.lower():
                    return member.user.id
        
        # Если это упоминание
        if identifier.startswith('tg://user?id='):
            try:
                return int(identifier.replace('tg://user?id=', ''))
            except:
                pass
        
        return None
    
    async def _get_user_name(self, user_id: int) -> str:
        """Получить имя пользователя"""
        try:
            user = await self.bot.get_chat(user_id)
            return user.full_name
        except:
            return f"Пользователь {user_id}"
    
    def _parse_duration(self, time_str: str) -> Optional[int]:
        """Парсинг строки времени в секунды"""
        try:
            seconds = 0
            current_num = ""
            
            for char in time_str:
                if char.isdigit():
                    current_num += char
                elif char in ['s', 'm', 'h', 'd', 'w']:
                    if not current_num:
                        return None
                    
                    num = int(current_num)
                    
                    if char == 's':  # секунды
                        seconds += num
                    elif char == 'm':  # минуты
                        seconds += num * 60
                    elif char == 'h':  # часы
                        seconds += num * 3600
                    elif char == 'd':  # дни
                        seconds += num * 86400
                    elif char == 'w':  # недели
                        seconds += num * 604800
                    
                    current_num = ""
                else:
                    return None
            
            return seconds if seconds > 0 else None
            
        except:
            return None
    
    def _format_duration(self, seconds: int) -> str:
        """Форматирование времени"""
        if seconds < 60:
            return f"{seconds} сек"
        elif seconds < 3600:
            return f"{seconds // 60} мин"
        elif seconds < 86400:
            return f"{seconds // 3600} час"
        else:
            return f"{seconds // 86400} дн"
    
    async def _update_user_after_ban(self, user_id: int, chat_id: int, reason: str):
        """Обновление данных пользователя после бана"""
        db = DatabaseManager.get_instance()
        
        user = await db.get_user(user_id)
        if not user:
            # Создание записи
            from .models import User, UserStatus
            user = User(
                user_id=user_id,
                first_name=await self._get_user_name(user_id),
                status=UserStatus.BLOCKED
            )
            await db.add_user(user)
        else:
            # Обновление статуса
            from .models import UserStatus
            user.status = UserStatus.BLOCKED
            await db.update_user(user)
    
    async def _get_user_chat_info(self, user_id: int, chat_id: int) -> str:
        """Получить информацию о пользователе в чате"""
        db = DatabaseManager.get_instance()
        
        # Получение данных пользователя
        user = await db.get_user(user_id)
        
        if not user:
            return f"👤 Пользователь {user_id}\n\n❓ Информация не найдена"
        
        # Получение информации из чата
        try:
            chat_member = await self.bot.get_chat_member(chat_id, user_id)
            
            text = f"👤 Информация о пользователе\n\n"
            text += f"🆔 ID: {user.user_id}\n"
            text += f"📛 Имя: {user.full_name}\n"
            
            if user.username:
                text += f"📱 Username: @{user.username}\n"
            
            text += f"👥 Роль в чате: {self._get_chat_role_text(chat_member.status)}\n"
            text += f"⭐ Рейтинг: {user.rating}\n"
            text += f"⚠️ Предупреждения: {user.warnings}\n"
            text += f"📅 Регистрация: {user.registration_date.strftime('%d.%m.%Y')}\n"
            text += f"⏰ Последняя активность: {user.last_activity.strftime('%d.%m.%Y %H:%M')}\n"
            
            if user.is_premium:
                text += f"👑 Премиум: Да\n"
            
            return text
            
        except Exception as e:
            return f"👤 Информация о пользователе\n\n🆔 ID: {user.user_id}\n📛 Имя: {user.full_name}\n❌ Не удалось получить информацию из чата"
    
    def _get_chat_role_text(self, status: str) -> str:
        """Текстовое представление роли в чате"""
        roles = {
            "creator": "👑 Создатель",
            "administrator": "🛡️ Администратор",
            "member": "👤 Участник",
            "restricted": "⏸️ Ограничен",
            "left": "🚪 Вышел",
            "kicked": "🚫 Исключен"
        }
        return roles.get(status, status)
    
    async def _get_chat_stats(self, chat_id: int) -> str:
        """Получить статистику чата"""
        db = DatabaseManager.get_instance()
        
        # Получение данных чата
        chat = await db.get_chat(chat_id)
        
        if not chat:
            return "❌ Информация о чате не найдена"
        
        # Получение количества участников
        try:
            chat_info = await self.bot.get_chat(chat_id)
            members_count = chat_info.get_members_count()
        except:
            members_count = chat.members_count
        
        # Получение активности за последние 7 дней
        week_ago = datetime.now() - timedelta(days=7)
        logs, activity_count = await db.get_action_logs(
            chat_id=chat_id,
            start_date=week_ago,
            limit=1
        )
        
        text = f"📊 Статистика чата\n\n"
        text += f"💬 Название: {chat.title}\n"
        text += f"🆔 ID: {chat.chat_id}\n"
        text += f"👥 Участников: {members_count:,}\n"
        text += f"📅 Бот добавлен: {chat.join_date.strftime('%d.%m.%Y')}\n"
        text += f"⏰ Последняя активность: {chat.last_activity.strftime('%d.%m.%Y %H:%M')}\n"
        text += f"📈 Активность (7 дней): {activity_count:,} действий\n"
        
        # Статистика по предупреждениям
        users, _ = await db.get_users(
            filters={"min_warnings": 1},
            limit=100
        )
        
        total_warnings = sum(u.warnings for u in users)
        warned_users = len([u for u in users if u.warnings > 0])
        
        text += f"\n⚠️ Предупреждения:\n"
        text += f"• Всего выдано: {total_warnings}\n"
        text += f"• Пользователей с варнами: {warned_users}\n"
        
        return text
    
    async def _get_chat_admins_text(self, chat_id: int) -> str:
        """Получить текст со списком админов"""
        try:
            admins = await self.bot.get_chat_administrators(chat_id)
            
            if not admins:
                return "👑 Администраторы не найдены"
            
            text = "👑 Администраторы чата:\n\n"
            
            for admin in admins:
                role = "👑 Создатель" if admin.status == "creator" else "🛡️ Админ"
                name = admin.user.full_name
                username = f" (@{admin.user.username})" if admin.user.username else ""
                
                text += f"{role}: {name}{username}\n"
            
            return text
            
        except Exception as e:
            return f"❌ Не удалось получить список администраторов: {e}"
    
    async def _get_user_profile(self, user_id: int, chat_id: Optional[int] = None) -> str:
        """Получить профиль пользователя"""
        db = DatabaseManager.get_instance()
        
        user = await db.get_user(user_id)
        
        if not user:
            return "❌ Профиль не найден"
        
        text = f"👤 Ваш профиль\n\n"
        text += f"🆔 ID: {user.user_id}\n"
        text += f"📛 Имя: {user.full_name}\n"
        
        if user.username:
            text += f"📱 Username: @{user.username}\n"
        
        text += f"🌐 Язык: {user.language_code}\n"
        text += f"⭐ Рейтинг: {user.rating}\n"
        text += f"⚠️ Предупреждения: {user.warnings}\n"
        
        if user.is_premium:
            text += f"👑 Премиум: Да\n"
        
        if user.email:
            text += f"📧 Email: {user.email}\n"
        
        if user.phone:
            text += f"📱 Телефон: {user.phone}\n"
        
        text += f"📅 Регистрация: {user.registration_date.strftime('%d.%m.%Y')}\n"
        text += f"⏰ Последняя активность: {user.last_activity.strftime('%d.%m.%Y %H:%M')}\n"
        
        # Статистика по чату, если указан
        if chat_id:
            week_ago = datetime.now() - timedelta(days=7)
            logs, activity_count = await db.get_action_logs(
                user_id=user_id,
                chat_id=chat_id,
                start_date=week_ago,
                limit=1
            )
            
            text += f"\n💬 Активность в этом чате (7 дней): {activity_count:,} действий\n"
        
        return text
    
    async def _get_user_stats(self, user_id: int) -> str:
        """Получить статистику пользователя"""
        from .statistics import StatisticsManager
        
        stats_manager = StatisticsManager(self.admin_system)
        stats = await stats_manager.get_user_statistics(user_id, period_days=30)
        
        if not stats:
            return "❌ Статистика не найдена"
        
        user = stats['user']
        
        text = f"📊 Ваша статистика\n\n"
        text += f"📅 Период: последние {stats['period']['days']} дней\n\n"
        text += f"📈 Общая активность: {stats['total_activity']:,} действий\n"
        text += f"📊 Среднедневная активность: {stats['daily_average']:.1f}\n\n"
        
        # Распределение по типам активности
        if stats['activity_by_type']:
            text += "📋 Распределение по типам:\n"
            for action_type, count in stats['activity_by_type'].items():
                type_name = self._get_action_type_text(action_type)
                percentage = (count / stats['total_activity'] * 100) if stats['total_activity'] > 0 else 0
                text += f"• {type_name}: {count} ({percentage:.1f}%)\n"
        
        # Топ чатов
        if stats['top_chats']:
            text += "\n🏆 Топ чатов по активности:\n"
            for i, (chat_id, chat_data) in enumerate(stats['top_chats'].items(), 1):
                if i > 5:
                    break
                text += f"{i}. {chat_data['title']}: {chat_data['activity']} действий\n"
        
        return text
    
    async def _get_top_users(self, chat_id: Optional[int] = None) -> str:
        """Получить топ пользователей"""
        db = DatabaseManager.get_instance()
        
        # Получение топ пользователей по рейтингу
        users, _ = await db.get_users(
            limit=10,
            order_by="rating DESC"
        )
        
        if not users:
            return "🏆 Топ пользователей пуст"
        
        text = "🏆 Топ пользователей по рейтингу\n\n"
        
        for i, user in enumerate(users, 1):
            text += f"{i}. {user.full_name}"
            if user.username:
                text += f" (@{user.username})"
            text += f" - ⭐ {user.rating}\n"
        
        # Топ по активности, если указан чат
        if chat_id:
            text += "\n⚡ Топ по активности в этом чате (7 дней):\n"
            
            # Здесь нужно реализовать получение топ по активности
            # Для простоты показываем только рейтинг
            
        return text
    
    def _get_action_type_text(self, action_type: int) -> str:
        """Текстовое представление типа действия"""
        from .models import ActionType as AT
        
        types = {
            AT.USER_REGISTERED.value: "📝 Регистрация",
            AT.USER_BLOCKED.value: "🚫 Блокировка",
            AT.USER_UNBLOCKED.value: "✅ Разблокировка",
            AT.USER_WARNED.value: "⚠️ Предупреждение",
            AT.CHAT_JOINED.value: "💬 Вход в чат",
            AT.CHAT_LEFT.value: "🚪 Выход из чата",
            AT.MESSAGE_SENT.value: "📨 Сообщение",
            AT.COMMAND_USED.value: "⌨️ Команда",
            AT.SETTINGS_CHANGED.value: "⚙️ Настройки",
            AT.BROADCAST_SENT.value: "📢 Рассылка",
            AT.POLL_CREATED.value: "📊 Опрос",
            AT.GIVEAWAY_CREATED.value: "🎁 Розыгрыш",
            AT.REPORT_SUBMITTED.value: "⚠️ Жалоба"
        }
        
        return types.get(action_type, f"Действие {action_type}")