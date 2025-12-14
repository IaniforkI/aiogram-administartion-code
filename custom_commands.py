import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum
import json

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from .models import CustomCommand, User, Chat
from .ui import create_keyboard, create_pagination_keyboard, create_confirmation_keyboard
from .security import require_admin
from .database import DatabaseManager

logger = logging.getLogger(__name__)

class CommandWorksIn(Enum):
    """Где работает команда"""
    EVERYWHERE = "everywhere"
    PRIVATE_ONLY = "private_only"
    CHATS_ONLY = "chats_only"

class CommandResponseType(Enum):
    """Типы ответов команд"""
    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "document"
    AUDIO = "audio"
    STICKER = "sticker"
    VOICE = "voice"
    ANIMATION = "animation"
    POLL = "poll"
    QUIZ = "quiz"

class CommandStates(StatesGroup):
    """Состояния для создания команд"""
    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_response_type = State()
    waiting_for_response_content = State()
    waiting_for_buttons = State()
    waiting_for_settings = State()

class CustomCommandsManager:
    """Менеджер кастомных команд"""
    
    def __init__(self, admin_system):
        self.admin_system = admin_system
        self.bot = admin_system.bot
        self.router = Router()
        
        # Кэш команд для быстрого доступа
        self._commands_cache: Dict[str, CustomCommand] = {}
        self._commands_list_cache: List[CustomCommand] = []
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl = 60  # 1 минута
        
        self.setup_handlers()
        
    def setup_handlers(self):
        """Настройка обработчиков"""
        
        # Динамическая обработка кастомных команд
        @self.router.message(F.text)
        async def handle_custom_command(message: Message):
            """Обработка кастомных команд"""
            await self.process_custom_command(message)
        
        # Команда для списка команд
        @self.router.message(Command("commands"))
        async def show_commands_list(message: Message):
            """Показать список доступных команд"""
            await self.handle_commands_list_command(message)
    
    async def process_custom_command(self, message: Message):
        """Обработка кастомной команды"""
        if not message.text or not message.text.startswith('/'):
            return
        
        # Извлечение имени команды
        command_text = message.text.split()[0][1:].lower()  # Убираем "/"
        if not command_text:
            return
        
        # Получение команды из кэша или БД
        command = await self.get_command(command_text)
        if not command:
            return
        
        # Проверка валидности команды
        if not command.is_valid:
            return
        
        # Проверка, где работает команда
        if not self._check_command_works_in(command, message):
            return
        
        # Проверка прав доступа
        if not await self._check_command_access(command, message):
            return
        
        # Отправка ответа
        await self.send_command_response(command, message)
        
        # Увеличение счетчика использования
        await self.increment_command_usage(command.id)
        
        # Логирование
        security = self.admin_system.security
        await security.log_action(
            user_id=message.from_user.id,
            action_type=8,  # COMMAND_USED
            action_data={
                "command_name": command.name,
                "command_id": command.id,
                "chat_id": message.chat.id
            },
            chat_id=message.chat.id
        )
    
    async def get_command(self, name: str) -> Optional[CustomCommand]:
        """Получить команду по имени"""
        # Проверка кэша
        cache_key = name.lower()
        if cache_key in self._commands_cache:
            command = self._commands_cache[cache_key]
            if command.is_valid:
                return command
        
        # Обновление кэша при необходимости
        if (not self._cache_timestamp or 
            (datetime.now() - self._cache_timestamp).total_seconds() > self._cache_ttl):
            await self._update_commands_cache()
        
        # Повторная проверка после обновления кэша
        if cache_key in self._commands_cache:
            command = self._commands_cache[cache_key]
            if command.is_valid:
                return command
        
        # Запрос из БД
        db = DatabaseManager.get_instance()
        command = await db.get_custom_command(name)
        
        if command and command.is_valid:
            self._commands_cache[cache_key] = command
            return command
        
        return None
    
    async def _update_commands_cache(self):
        """Обновление кэша команд"""
        db = DatabaseManager.get_instance()
        commands, _ = await db.get_custom_commands(valid_only=True, limit=1000)
        
        self._commands_cache.clear()
        for command in commands:
            if command.is_valid:
                self._commands_cache[command.name.lower()] = command
        
        self._commands_list_cache = commands
        self._cache_timestamp = datetime.now()
        
        logger.info(f"Кэш команд обновлен: {len(self._commands_cache)} команд")
    
    def _check_command_works_in(self, command: CustomCommand, message: Message) -> bool:
        """Проверить, где работает команда"""
        chat_type = message.chat.type
        
        if command.works_in == CommandWorksIn.EVERYWHERE.value:
            return True
        elif command.works_in == CommandWorksIn.PRIVATE_ONLY.value:
            return chat_type == "private"
        elif command.works_in == CommandWorksIn.CHATS_ONLY.value:
            return chat_type in ["group", "supergroup", "channel"]
        
        return False
    
    async def _check_command_access(self, command: CustomCommand, message: Message) -> bool:
        """Проверить права доступа к команде"""
        user_id = message.from_user.id
        
        # Если уровень доступа 0 - команда доступна всем
        if command.access_level == 0:
            return True
        
        # Проверка прав админа
        security = self.admin_system.security
        
        # Проверка админа бота
        admin = await security.check_bot_admin(user_id)
        if admin and admin.level >= command.access_level:
            return True
        
        # Проверка админа чата (если команда в чате)
        if message.chat.type != "private":
            chat_admin = await security.check_chat_admin(user_id, message.chat.id)
            if chat_admin and chat_admin.level >= command.access_level:
                return True
        
        return False
    
    async def send_command_response(self, command: CustomCommand, message: Message):
        """Отправить ответ команды"""
        try:
            response_type = command.response_type
            response_data = command.response_data
            buttons = command.buttons
            
            # Подготовка клавиатуры
            reply_markup = None
            if buttons:
                keyboard_buttons = []
                for button in buttons:
                    if isinstance(button, dict):
                        if button.get('type') == 'url':
                            keyboard_buttons.append([
                                InlineKeyboardButton(
                                    text=button.get('text', 'Кнопка'),
                                    url=button.get('url')
                                )
                            ])
                        elif button.get('type') == 'callback':
                            keyboard_buttons.append([
                                InlineKeyboardButton(
                                    text=button.get('text', 'Кнопка'),
                                    callback_data=button.get('data')
                                )
                            ])
                
                if keyboard_buttons:
                    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
            
            # Отправка в зависимости от типа
            if response_type == CommandResponseType.TEXT.value:
                text = response_data.get('text', '')
                parse_mode = response_data.get('parse_mode', None)
                
                await message.answer(
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup
                )
            
            elif response_type == CommandResponseType.PHOTO.value:
                file_id = response_data.get('file_id')
                caption = response_data.get('caption', '')
                parse_mode = response_data.get('parse_mode', None)
                
                await message.answer_photo(
                    photo=file_id,
                    caption=caption,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup
                )
            
            elif response_type == CommandResponseType.VIDEO.value:
                file_id = response_data.get('file_id')
                caption = response_data.get('caption', '')
                parse_mode = response_data.get('parse_mode', None)
                
                await message.answer_video(
                    video=file_id,
                    caption=caption,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup
                )
            
            elif response_type == CommandResponseType.DOCUMENT.value:
                file_id = response_data.get('file_id')
                caption = response_data.get('caption', '')
                parse_mode = response_data.get('parse_mode', None)
                
                await message.answer_document(
                    document=file_id,
                    caption=caption,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup
                )
            
            elif response_type == CommandResponseType.AUDIO.value:
                file_id = response_data.get('file_id')
                caption = response_data.get('caption', '')
                parse_mode = response_data.get('parse_mode', None)
                
                await message.answer_audio(
                    audio=file_id,
                    caption=caption,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup
                )
            
            elif response_type == CommandResponseType.STICKER.value:
                file_id = response_data.get('file_id')
                
                await message.answer_sticker(
                    sticker=file_id,
                    reply_markup=reply_markup
                )
            
            elif response_type == CommandResponseType.VOICE.value:
                file_id = response_data.get('file_id')
                
                await message.answer_voice(
                    voice=file_id,
                    reply_markup=reply_markup
                )
            
            elif response_type == CommandResponseType.ANIMATION.value:
                file_id = response_data.get('file_id')
                caption = response_data.get('caption', '')
                parse_mode = response_data.get('parse_mode', None)
                
                await message.answer_animation(
                    animation=file_id,
                    caption=caption,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup
                )
            
            elif response_type in [CommandResponseType.POLL.value, CommandResponseType.QUIZ.value]:
                question = response_data.get('question', '')
                options = response_data.get('options', [])
                is_anonymous = response_data.get('is_anonymous', True)
                allows_multiple_answers = response_data.get('allows_multiple_answers', False)
                poll_type = "quiz" if response_type == CommandResponseType.QUIZ.value else "regular"
                
                await message.answer_poll(
                    question=question,
                    options=options,
                    is_anonymous=is_anonymous,
                    type=poll_type,
                    allows_multiple_answers=allows_multiple_answers,
                    reply_markup=reply_markup
                )
            
        except Exception as e:
            logger.error(f"Ошибка при отправке ответа команды {command.name}: {e}")
    
    async def increment_command_usage(self, command_id: int):
        """Увеличить счетчик использования команды"""
        db = DatabaseManager.get_instance()
        await db.increment_command_usage(command_id)
        
        # Обновление кэша
        for command in self._commands_list_cache:
            if command.id == command_id:
                command.usage_count += 1
                break
    
    async def handle_commands_list_command(self, message: Message):
        """Обработка команды /commands"""
        user_id = message.from_user.id
        chat_type = message.chat.type
        
        # Получение доступных команд
        available_commands = []
        
        for command in self._commands_list_cache:
            if not command.is_valid:
                continue
            
            # Проверка, где работает команда
            if not self._check_command_works_in(command, message):
                continue
            
            # Проверка прав доступа
            if not await self._check_command_access(command, message):
                continue
            
            available_commands.append(command)
        
        if not available_commands:
            await message.answer("📭 Нет доступных команд.")
            return
        
        # Формирование списка
        text = "📋 Доступные команды:\n\n"
        
        for command in available_commands[:20]:  # Ограничиваем 20 командами
            text += f"• /{command.name}"
            if command.description:
                text += f" - {command.description}"
            text += "\n"
        
        if len(available_commands) > 20:
            text += f"\n... и еще {len(available_commands) - 20} команд"
        
        await message.answer(text)
    
    @require_admin(2)  # Только старшие админы и выше
    async def show_commands_list(self, callback: CallbackQuery, page: int = 0):
        """Показать список всех кастомных команд"""
        user_id = callback.from_user.id
        
        # Обновление кэша
        await self._update_commands_cache()
        
        # Пагинация
        page_size = 10
        start_idx = page * page_size
        end_idx = start_idx + page_size
        
        commands = self._commands_list_cache[start_idx:end_idx]
        total = len(self._commands_list_cache)
        
        text = f"💬 Кастомные команды\n\n"
        text += f"📊 Всего: {total:,}\n"
        text += f"📄 Страница {page + 1}/{(total + page_size - 1) // page_size}\n\n"
        
        if not commands:
            text += "Команды не найдены."
        else:
            for i, command in enumerate(commands, start=1):
                status = "✅" if command.is_valid else "❌"
                works_in = {
                    "everywhere": "🌐",
                    "private_only": "🔒",
                    "chats_only": "👥"
                }.get(command.works_in, "❓")
                
                text += f"{i}. {status} {works_in} /{command.name}\n"
                if command.description:
                    text += f"   {command.description[:50]}"
                    if len(command.description) > 50:
                        text += "..."
                text += f"\n   👤 Уровень: {command.access_level} | 📊 Использований: {command.usage_count}\n\n"
        
        # Кнопки
        buttons = [
            ("➕ Создать команду", "command_create"),
            ("📊 Статистика", "command_stats"),
            ("📥 Экспорт", "command_export"),
            ("📤 Импорт", "command_import")
        ]
        
        keyboard = create_pagination_keyboard(
            current_page=page,
            total_pages=(total + page_size - 1) // page_size,
            prefix="admin_commands_list",
            additional_buttons=buttons
        )
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    @require_admin(2)
    async def create_command_dialog(self, callback: CallbackQuery, state: FSMContext):
        """Начать создание новой команды"""
        text = "➕ Создание новой команды\n\n"
        text += "Введите название команды (без /):"
        
        await state.set_state(CommandStates.waiting_for_name)
        await callback.message.edit_text(text)
    
    async def handle_command_name(self, message: Message, state: FSMContext):
        """Обработка имени команды"""
        name = message.text.strip()
        
        if not name:
            await message.answer("❌ Имя команды не может быть пустым.")
            return
        
        # Проверка на существование команды
        existing_command = await self.get_command(name)
        if existing_command:
            await message.answer(f"❌ Команда /{name} уже существует.")
            return
        
        # Проверка на длину
        if len(name) > 32:
            await message.answer("❌ Имя команды не может быть длиннее 32 символов.")
            return
        
        # Проверка на разрешенные символы
        if not name.replace('_', '').isalnum():
            await message.answer("❌ Имя команды может содержать только буквы, цифры и подчеркивания.")
            return
        
        await state.update_data(name=name.lower())
        await state.set_state(CommandStates.waiting_for_description)
        
        await message.answer(
            "📝 Введите описание команды (опционально):\n\n"
            "Этот текст будет показан в списке команд.\n"
            "Максимум 200 символов.\n\n"
            "Для пропуска отправьте /skip"
        )
    
    async def handle_command_description(self, message: Message, state: FSMContext):
        """Обработка описания команды"""
        if message.text == '/skip':
            description = ""
        else:
            description = message.text.strip()[:200]
        
        await state.update_data(description=description)
        await state.set_state(CommandStates.waiting_for_response_type)
        
        text = "📝 Выберите тип ответа:\n\n"
        text += "• 📝 Текст - простой текстовый ответ\n"
        text += "• 🖼️ Фото - изображение с текстом\n"
        text += "• 🎥 Видео - видео с текстом\n"
        text += "• 📎 Документ - файл с текстом\n"
        text += "• 🎵 Аудио - аудиофайл\n"
        text += "• 😀 Стикер\n"
        text += "• 🎤 Голосовое сообщение\n"
        text += "• 🎞️ GIF/анимация\n"
        text += "• 📊 Опрос/викторина\n"
        
        buttons = [
            ("📝 Текст", "command_type_text"),
            ("🖼️ Фото", "command_type_photo"),
            ("🎥 Видео", "command_type_video"),
            ("📎 Документ", "command_type_document"),
            ("🎵 Аудио", "command_type_audio"),
            ("😀 Стикер", "command_type_sticker"),
            ("🎤 Голосовое", "command_type_voice"),
            ("🎞️ Анимация", "command_type_animation"),
            ("📊 Опрос", "command_type_poll"),
            ("❓ Викторина", "command_type_quiz"),
            ("❌ Отмена", "command_cancel")
        ]
        
        keyboard = create_keyboard(buttons, columns=2)
        
        await message.answer(text, reply_markup=keyboard)
    
    async def handle_response_type(self, callback: CallbackQuery, state: FSMContext):
        """Обработка выбора типа ответа"""
        if callback.data == "command_cancel":
            await state.clear()
            await callback.message.edit_text("❌ Создание команды отменено.")
            return
        
        response_type = callback.data.replace("command_type_", "")
        
        await state.update_data(response_type=response_type)
        await state.set_state(CommandStates.waiting_for_response_content)
        
        # Запрос контента в зависимости от типа
        if response_type == "text":
            text = "📝 Введите текст ответа:\n\n"
            text += "Поддерживается HTML-разметка:\n"
            text += "<b>жирный</b>, <i>курсив</i>, <u>подчеркнутый</u>\n"
            text += "<code>моноширинный</code>, <a href='url'>ссылка</a>\n\n"
            text += "Максимум 4000 символов."
            
            await callback.message.edit_text(text)
        
        elif response_type in ["photo", "video", "document", "audio", "voice", "animation"]:
            media_type = {
                "photo": "фото",
                "video": "видео", 
                "document": "документ",
                "audio": "аудио",
                "voice": "голосовое сообщение",
                "animation": "GIF/анимацию"
            }[response_type]
            
            text = f"🖼️ Отправьте {media_type}:\n\n"
            text += f"Пришлите {media_type} как обычное сообщение."
            
            await callback.message.edit_text(text)
        
        elif response_type == "sticker":
            text = "😀 Отправьте стикер:\n\n"
            text += "Пришлите стикер как обычное сообщение."
            
            await callback.message.edit_text(text)
        
        elif response_type in ["poll", "quiz"]:
            poll_type = "опрос" if response_type == "poll" else "викторину"
            
            text = f"📊 Создание {poll_type}\n\n"
            text += "Введите вопрос и варианты ответов в формате:\n"
            text += "Вопрос?\n"
            text += "Вариант 1\n"
            text += "Вариант 2\n"
            text += "Вариант 3\n\n"
            text += "Для викторины укажите правильный вариант первым.\n"
            text += "Максимум 10 вариантов."
            
            await callback.message.edit_text(text)
    
    async def handle_response_content(self, message: Message, state: FSMContext):
        """Обработка контента ответа"""
        data = await state.get_data()
        response_type = data.get("response_type")
        
        response_data = {}
        
        if response_type == "text":
            if not message.text:
                await message.answer("❌ Пожалуйста, отправьте текст.")
                return
            
            response_data = {
                "text": message.text[:4000],
                "parse_mode": "HTML"
            }
            
            await state.update_data(response_data=response_data)
            await self.show_button_options(message, state)
        
        elif response_type in ["photo", "video", "document", "audio", "voice", "animation", "sticker"]:
            # Сохранение файла
            file_id = None
            caption = ""
            
            if response_type == "photo" and message.photo:
                file_id = message.photo[-1].file_id
                caption = message.caption or ""
            elif response_type == "video" and message.video:
                file_id = message.video.file_id
                caption = message.caption or ""
            elif response_type == "document" and message.document:
                file_id = message.document.file_id
                caption = message.caption or ""
            elif response_type == "audio" and message.audio:
                file_id = message.audio.file_id
                caption = message.caption or ""
            elif response_type == "voice" and message.voice:
                file_id = message.voice.file_id
            elif response_type == "animation" and message.animation:
                file_id = message.animation.file_id
                caption = message.caption or ""
            elif response_type == "sticker" and message.sticker:
                file_id = message.sticker.file_id
            else:
                await message.answer(f"❌ Пожалуйста, отправьте {response_type}.")
                return
            
            response_data = {"file_id": file_id}
            if caption:
                response_data["caption"] = caption
                response_data["parse_mode"] = "HTML"
            
            await state.update_data(response_data=response_data)
            await self.show_button_options(message, state)
        
        elif response_type in ["poll", "quiz"]:
            if not message.text:
                await message.answer("❌ Пожалуйста, отправьте текст опроса.")
                return
            
            lines = message.text.strip().split('\n')
            if len(lines) < 3:
                await message.answer("❌ Нужно как минимум вопрос и 2 варианта ответа.")
                return
            
            question = lines[0].strip()
            options = [line.strip() for line in lines[1:] if line.strip()]
            
            if len(options) > 10:
                options = options[:10]
            
            response_data = {
                "question": question,
                "options": options,
                "is_anonymous": True,
                "allows_multiple_answers": False
            }
            
            await state.update_data(response_data=response_data)
            await self.show_button_options(message, state)
    
    async def show_button_options(self, message: Message, state: FSMContext):
        """Показать опции кнопок"""
        text = "🔘 Добавить кнопки к сообщению?\n\n"
        text += "Кнопки будут отображаться под сообщением.\n"
        text += "Вы можете добавить:\n"
        text += "• Кнопку-ссылку (открывает URL)\n"
        text += "• Кнопку с callback (для интерактивных действий)\n\n"
        text += "Выберите тип кнопки:"
        
        buttons = [
            ("🔗 Кнопка-ссылка", "button_type_url"),
            ("🔄 Callback кнопка", "button_type_callback"),
            ("➡️ Пропустить", "button_skip"),
            ("❌ Отмена", "command_cancel")
        ]
        
        keyboard = create_keyboard(buttons, columns=2)
        
        await state.set_state(CommandStates.waiting_for_buttons)
        await message.answer(text, reply_markup=keyboard)
    
    async def handle_button_type(self, callback: CallbackQuery, state: FSMContext):
        """Обработка выбора типа кнопки"""
        if callback.data == "button_skip":
            await state.update_data(buttons=[])
            await self.show_settings_options(callback, state)
            return
        
        if callback.data == "command_cancel":
            await state.clear()
            await callback.message.edit_text("❌ Создание команды отменено.")
            return
        
        button_type = callback.data.replace("button_type_", "")
        
        await state.update_data(current_button_type=button_type)
        
        if button_type == "url":
            text = "🔗 Создание кнопки-ссылки\n\n"
            text += "Введите данные в формате:\n"
            text += "Текст кнопки - URL\n\n"
            text += "Пример:\n"
            text += "Открыть сайт - https://example.com"
            
            await callback.message.edit_text(text)
        
        elif button_type == "callback":
            text = "🔄 Создание callback кнопки\n\n"
            text += "Введите данные в формате:\n"
            text += "Текст кнопки - callback_data\n\n"
            text += "Пример:\n"
            text += "Подтвердить - confirm_action"
            
            await callback.message.edit_text(text)
    
    async def handle_button_data(self, message: Message, state: FSMContext):
        """Обработка данных кнопки"""
        data = await state.get_data()
        button_type = data.get("current_button_type")
        
        if ' - ' not in message.text:
            await message.answer("❌ Неверный формат. Используйте: Текст - данные")
            return
        
        text_part, data_part = message.text.split(' - ', 1)
        text_part = text_part.strip()
        data_part = data_part.strip()
        
        if not text_part or not data_part:
            await message.answer("❌ Текст и данные кнопки не могут быть пустыми.")
            return
        
        # Сохранение кнопки
        button = {
            "type": button_type,
            "text": text_part,
            "url" if button_type == "url" else "data": data_part
        }
        
        # Получение существующих кнопок
        buttons = data.get("buttons", [])
        buttons.append(button)
        
        await state.update_data(buttons=buttons)
        
        # Предложение добавить еще кнопку
        text = f"✅ Кнопка добавлена: {text_part}\n\n"
        text += "Добавить еще одну кнопку?"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Еще кнопку", callback_data="button_add_more"),
                InlineKeyboardButton(text="➡️ Далее", callback_data="button_next")
            ]
        ])
        
        await message.answer(text, reply_markup=keyboard)
    
    async def show_settings_options(self, callback: CallbackQuery, state: FSMContext):
        """Показать опции настроек"""
        data = await state.get_data()
        
        text = "⚙️ Настройки команды\n\n"
        text += f"Название: /{data.get('name')}\n"
        text += f"Описание: {data.get('description', 'нет')}\n"
        text += f"Тип ответа: {data.get('response_type')}\n"
        text += f"Кнопок: {len(data.get('buttons', []))}\n\n"
        text += "Выберите, где работает команда:"
        
        buttons = [
            ("🌐 Везде", "command_works_everywhere"),
            ("🔒 Только в ЛС", "command_works_private"),
            ("👥 Только в чатах", "command_works_chats"),
            ("➡️ Далее", "command_next_settings")
        ]
        
        keyboard = create_keyboard(buttons, columns=2)
        
        await state.set_state(CommandStates.waiting_for_settings)
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def handle_settings(self, callback: CallbackQuery, state: FSMContext):
        """Обработка настроек"""
        if callback.data == "command_next_settings":
            await self.show_access_level_options(callback, state)
            return
        
        if callback.data.startswith("command_works_"):
            works_in = callback.data.replace("command_works_", "")
            await state.update_data(works_in=works_in)
            await self.show_access_level_options(callback, state)
            return
    
    async def show_access_level_options(self, callback: CallbackQuery, state: FSMContext):
        """Показать опции уровня доступа"""
        text = "🔐 Уровень доступа\n\n"
        text += "Выберите, кто может использовать команду:\n\n"
        text += "• 0 - Все пользователи\n"
        text += "• 1 - Наблюдатели и выше\n"
        text += "• 2 - Помощники модераторов и выше\n"
        text += "• 3 - Модераторы и выше\n"
        text += "• 4 - Старшие модераторы и выше\n"
        text += "• 5 - Владельцы и админы бота\n"
        
        buttons = []
        for i in range(6):
            buttons.append((f"Уровень {i}", f"command_access_{i}"))
        
        buttons.append(("❌ Отмена", "command_cancel"))
        
        keyboard = create_keyboard(buttons, columns=3)
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def handle_access_level(self, callback: CallbackQuery, state: FSMContext):
        """Обработка уровня доступа"""
        if callback.data == "command_cancel":
            await state.clear()
            await callback.message.edit_text("❌ Создание команды отменено.")
            return
        
        access_level = int(callback.data.replace("command_access_", ""))
        
        await state.update_data(access_level=access_level)
        await self.show_time_limits_options(callback, state)
    
    async def show_time_limits_options(self, callback: CallbackQuery, state: FSMContext):
        """Показать опции временных ограничений"""
        text = "⏰ Временные ограничения\n\n"
        text += "Вы можете ограничить время работы команды:\n\n"
        text += "• Без ограничений\n"
        text += "• С даты по дату\n"
        text += "• Только в определенные дни/часы\n\n"
        text += "Выберите вариант:"
        
        buttons = [
            ("🔄 Без ограничений", "command_time_none"),
            ("📅 С даты по дату", "command_time_range"),
            ("⏱️ В определенное время", "command_time_specific"),
            ("➡️ Пропустить", "command_time_skip")
        ]
        
        keyboard = create_keyboard(buttons, columns=2)
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def handle_time_limits(self, callback: CallbackQuery, state: FSMContext):
        """Обработка временных ограничений"""
        if callback.data == "command_time_none":
            await state.update_data(valid_from=None, valid_until=None)
            await self.show_confirmation(callback, state)
            return
        
        elif callback.data == "command_time_skip":
            await state.update_data(valid_from=None, valid_until=None)
            await self.show_confirmation(callback, state)
            return
        
        elif callback.data == "command_time_range":
            text = "📅 Укажите период работы команды:\n\n"
            text += "Формат: ДД.ММ.ГГГГ ЧЧ:ММ - ДД.ММ.ГГГГ ЧЧ:ММ\n\n"
            text += "Пример:\n"
            text += "01.01.2024 00:00 - 31.01.2024 23:59"
            
            await callback.message.edit_text(text)
            # Здесь нужно перейти в состояние ожидания ввода дат
        
        elif callback.data == "command_time_specific":
            text = "⏱️ Укажите время работы команды:\n\n"
            text += "Формат: ДНИ ЧАСЫ-ЧАСЫ\n\n"
            text += "Примеры:\n"
            text += "пн-пт 09:00-18:00 - по будням с 9 до 18\n"
            text += "вс 00:00-23:59 - только по воскресеньям\n"
            text += "ежедневно 20:00-22:00 - каждый день с 20 до 22"
            
            await callback.message.edit_text(text)
            # Здесь нужно перейти в состояние ожидания ввода времени
    
    async def show_confirmation(self, callback: CallbackQuery, state: FSMContext):
        """Показать подтверждение создания команды"""
        data = await state.get_data()
        
        text = "✅ Подтверждение создания команды\n\n"
        text += f"📛 Название: /{data.get('name')}\n"
        text += f"📝 Описание: {data.get('description', 'нет')}\n"
        text += f"📤 Тип ответа: {data.get('response_type')}\n"
        
        works_in = data.get('works_in', 'everywhere')
        works_in_text = {
            'everywhere': '🌐 Везде',
            'private': '🔒 Только в ЛС',
            'chats': '👥 Только в чатах'
        }.get(works_in, works_in)
        
        text += f"📍 Работает: {works_in_text}\n"
        text += f"🔐 Уровень доступа: {data.get('access_level', 0)}\n"
        text += f"🔘 Кнопок: {len(data.get('buttons', []))}\n\n"
        
        if data.get('valid_from') or data.get('valid_until'):
            text += "⏰ Ограничения по времени:\n"
            if data.get('valid_from'):
                text += f"С: {data['valid_from']}\n"
            if data.get('valid_until'):
                text += f"По: {data['valid_until']}\n"
            text += "\n"
        
        text += "Создать команду?"
        
        buttons = [
            ("✅ Создать", "command_confirm_create"),
            ("✏️ Редактировать", "command_edit"),
            ("❌ Отменить", "command_cancel")
        ]
        
        keyboard = create_keyboard(buttons, columns=2)
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def create_command(self, callback: CallbackQuery, state: FSMContext):
        """Создать команду"""
        data = await state.get_data()
        user_id = callback.from_user.id
        
        # Создание объекта команды
        command = CustomCommand(
            name=data['name'],
            description=data.get('description', ''),
            command_text=f"/{data['name']}",
            response_type=data['response_type'],
            response_data=data.get('response_data', {}),
            buttons=data.get('buttons', []),
            works_in=data.get('works_in', 'everywhere'),
            access_level=data.get('access_level', 0),
            created_by=user_id,
            valid_from=data.get('valid_from'),
            valid_until=data.get('valid_until'),
            bot_id=self.admin_system.config.bot_id
        )
        
        # Сохранение в БД
        db = DatabaseManager.get_instance()
        command_id = await db.add_custom_command(command)
        
        if command_id == -1:
            await callback.answer("❌ Ошибка при создании команды")
            return
        
        command.id = command_id
        
        # Обновление кэша
        self._commands_cache[command.name.lower()] = command
        self._commands_list_cache.append(command)
        
        # Логирование
        security = self.admin_system.security
        await security.log_action(
            user_id=user_id,
            action_type=9,  # SETTINGS_CHANGED
            action_data={
                "action": "command_created",
                "command_name": command.name,
                "command_id": command_id
            }
        )
        
        await state.clear()
        
        text = f"✅ Команда /{command.name} создана!\n\n"
        text += f"🆔 ID: {command_id}\n"
        text += "Теперь команда доступна для использования."
        
        buttons = [
            ("📋 Список команд", "admin_commands_list_0"),
            ("➕ Еще команда", "command_create")
        ]
        
        keyboard = create_keyboard(buttons, columns=2)
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def get_command_stats(self) -> Dict[str, Any]:
        """Получить статистику по командам"""
        stats = {
            "total": len(self._commands_list_cache),
            "by_type": {},
            "by_access_level": {},
            "by_works_in": {},
            "active": 0,
            "inactive": 0,
            "total_usage": 0,
            "top_commands": []
        }
        
        for command in self._commands_list_cache:
            # По типу
            stats["by_type"][command.response_type] = stats["by_type"].get(command.response_type, 0) + 1
            
            # По уровню доступа
            stats["by_access_level"][command.access_level] = stats["by_access_level"].get(command.access_level, 0) + 1
            
            # По месту работы
            stats["by_works_in"][command.works_in] = stats["by_works_in"].get(command.works_in, 0) + 1
            
            # Активные/неактивные
            if command.is_valid:
                stats["active"] += 1
            else:
                stats["inactive"] += 1
            
            # Общее использование
            stats["total_usage"] += command.usage_count
        
        # Топ команд по использованию
        top_commands = sorted(
            [c for c in self._commands_list_cache if c.usage_count > 0],
            key=lambda x: x.usage_count,
            reverse=True
        )[:10]
        
        stats["top_commands"] = [
            {"name": c.name, "usage": c.usage_count}
            for c in top_commands
        ]
        
        return stats
    
    async def export_commands(self, format_type: str = "json") -> bytes:
        """Экспорт команд"""
        commands_data = []
        
        for command in self._commands_list_cache:
            command_dict = command.to_dict()
            # Убираем служебные поля
            command_dict.pop('id', None)
            command_dict.pop('bot_id', None)
            command_dict.pop('usage_count', None)
            command_dict.pop('created_by', None)
            
            commands_data.append(command_dict)
        
        if format_type == "json":
            return json.dumps(commands_data, ensure_ascii=False, indent=2).encode('utf-8')
        
        elif format_type == "csv":
            import csv
            import io
            
            # Создание CSV
            output = io.StringIO()
            writer = csv.writer(output)
            
            # Заголовки
            writer.writerow([
                'name', 'description', 'response_type', 'works_in',
                'access_level', 'valid_from', 'valid_until'
            ])
            
            # Данные
            for command in commands_data:
                writer.writerow([
                    command.get('name', ''),
                    command.get('description', ''),
                    command.get('response_type', ''),
                    command.get('works_in', ''),
                    command.get('access_level', 0),
                    command.get('valid_from', ''),
                    command.get('valid_until', '')
                ])
            
            return output.getvalue().encode('utf-8')
        
        else:
            raise ValueError(f"Неподдерживаемый формат: {format_type}")
    
    async def import_commands(self, data: bytes, format_type: str = "json") -> Tuple[int, int]:
        """Импорт команд"""
        imported = 0
        skipped = 0
        
        if format_type == "json":
            commands_data = json.loads(data.decode('utf-8'))
        elif format_type == "csv":
            import csv
            import io
            
            reader = csv.DictReader(io.StringIO(data.decode('utf-8')))
            commands_data = list(reader)
        else:
            raise ValueError(f"Неподдерживаемый формат: {format_type}")
        
        db = DatabaseManager.get_instance()
        
        for command_data in commands_data:
            # Проверка существования команды
            existing = await self.get_command(command_data.get('name', ''))
            if existing:
                skipped += 1
                continue
            
            # Создание команды
            command = CustomCommand.from_dict(command_data)
            command.bot_id = self.admin_system.config.bot_id
            command.created_by = 0  # Системный импорт
            command.usage_count = 0
            
            # Сохранение
            command_id = await db.add_custom_command(command)
            if command_id != -1:
                imported += 1
                
                # Обновление кэша
                command.id = command_id
                self._commands_cache[command.name.lower()] = command
                self._commands_list_cache.append(command)
        
        # Обновление кэша
        self._cache_timestamp = None
        
        return imported, skipped
    
    def get_router(self) -> Router:
        """Получить роутер команд"""
        return self.router