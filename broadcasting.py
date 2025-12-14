import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum

from aiogram import Bot
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from .models import Broadcast, User
from .ui import create_keyboard, create_pagination_keyboard, create_confirmation_keyboard
from .security import require_admin

logger = logging.getLogger(__name__)

class BroadcastTarget(Enum):
    """Цели рассылки"""
    ALL_USERS = "all_users"
    ALL_CHATS = "all_chats"
    ALL = "all"
    FILTERED = "filtered"

class BroadcastMessageType(Enum):
    """Типы сообщений для рассылки"""
    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "document"
    POLL = "poll"
    QUIZ = "quiz"

class BroadcastStates(StatesGroup):
    """Состояния для создания рассылки"""
    waiting_for_target = State()
    waiting_for_filters = State()
    waiting_for_message_type = State()
    waiting_for_message_content = State()
    waiting_for_confirmation = State()
    waiting_for_schedule = State()

class BroadcastingManager:
    """Менеджер системы рассылок"""
    
    def __init__(self, admin_system):
        self.admin_system = admin_system
        self.bot = admin_system.bot
        self.active_broadcasts: Dict[int, asyncio.Task] = {}
        
    @require_admin(2)  # Только старшие админы и выше
    async def start_new_broadcast(self, callback: CallbackQuery, state: FSMContext = None):
        """Начать создание новой рассылки"""
        text = "📢 Создание новой рассылки\n\n"
        text += "Выберите цель рассылки:"
        
        buttons = [
            ("👤 Всем пользователям", "broadcast_target_all_users"),
            ("💬 Во все чаты", "broadcast_target_all_chats"),
            ("🌐 Всем (пользователи + чаты)", "broadcast_target_all"),
            ("🎯 По фильтрам", "broadcast_target_filtered"),
            ("◀️ Назад", "admin_broadcast")
        ]
        
        keyboard = create_keyboard(buttons, columns=2)
        
        if state:
            await state.set_state(BroadcastStates.waiting_for_target)
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def handle_broadcast_target(self, callback: CallbackQuery, state: FSMContext):
        """Обработка выбора цели рассылки"""
        target = callback.data.replace("broadcast_target_", "")
        
        await state.update_data(target=target)
        
        if target == "filtered":
            # Переход к выбору фильтров
            await self.show_filter_options(callback, state)
        else:
            # Переход к выбору типа сообщения
            await self.show_message_type_options(callback, state)
    
    async def show_filter_options(self, callback: CallbackQuery, state: FSMContext):
        """Показать опции фильтрации"""
        text = "🎯 Фильтрация аудитории\n\n"
        text += "Выберите критерии отбора:"
        
        buttons = [
            ("✅ Только активные", "filter_active"),
            ("❌ Только заблокированные", "filter_blocked"),
            ("⭐ С рейтингом выше...", "filter_min_rating"),
            ("📅 Зарегистрированы с...", "filter_registration_date"),
            ("🔞 Только премиум", "filter_premium"),
            ("📧 С email", "filter_with_email"),
            ("📱 С телефоном", "filter_with_phone"),
            ("➡️ Далее", "broadcast_next_step"),
            ("◀️ Назад", "admin_broadcast_new")
        ]
        
        keyboard = create_keyboard(buttons, columns=2)
        
        await state.set_state(BroadcastStates.waiting_for_filters)
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def show_message_type_options(self, callback: CallbackQuery, state: FSMContext):
        """Показать опции типа сообщения"""
        text = "📝 Тип сообщения\n\n"
        text += "Выберите тип сообщения для рассылки:"
        
        buttons = [
            ("📝 Текст", "broadcast_type_text"),
            ("🖼️ Фото + текст", "broadcast_type_photo"),
            ("🎥 Видео + текст", "broadcast_type_video"),
            ("📎 Документ + текст", "broadcast_type_document"),
            ("📊 Опрос", "broadcast_type_poll"),
            ("❓ Викторина", "broadcast_type_quiz"),
            ("◀️ Назад", "broadcast_target_selection")
        ]
        
        keyboard = create_keyboard(buttons, columns=2)
        
        await state.set_state(BroadcastStates.waiting_for_message_type)
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def handle_message_type(self, callback: CallbackQuery, state: FSMContext):
        """Обработка выбора типа сообщения"""
        message_type = callback.data.replace("broadcast_type_", "")
        await state.update_data(message_type=message_type)
        
        # Запрос контента в зависимости от типа
        if message_type == "text":
            text = "📝 Введите текст сообщения:\n\n"
            text += "Поддерживается HTML-разметка:\n"
            text += "• <b>жирный</b>\n"
            text += "• <i>курсив</i>\n"
            text += "• <u>подчеркнутый</u>\n"
            text += "• <code>моноширинный</code>\n"
            text += "• <a href='url'>ссылка</a>\n\n"
            text += "Для отмены отправьте /cancel"
            
            await state.set_state(BroadcastStates.waiting_for_message_content)
            await callback.message.edit_text(text)
        
        elif message_type in ["photo", "video", "document"]:
            media_type = {"photo": "фото", "video": "видео", "document": "документ"}[message_type]
            text = f"🖼️ Отправьте {media_type} для рассылки\n\n"
            text += "Пришлите файл как обычно (фото, видео или документ)\n"
            text += "Затем введите подпись к нему\n\n"
            text += "Для отмены отправьте /cancel"
            
            await state.set_state(BroadcastStates.waiting_for_message_content)
            await callback.message.edit_text(text)
        
        elif message_type in ["poll", "quiz"]:
            poll_type = "опрос" if message_type == "poll" else "викторину"
            text = f"📊 Создание {poll_type}\n\n"
            text += "Введите вопрос и варианты ответов в формате:\n"
            text += "Вопрос?\n"
            text += "Вариант 1\n"
            text += "Вариант 2\n"
            text += "Вариант 3\n\n"
            text += "Для викторины укажите правильный вариант первым\n"
            text += "Для отмены отправьте /cancel"
            
            await state.set_state(BroadcastStates.waiting_for_message_content)
            await callback.message.edit_text(text)
    
    async def handle_message_content(self, message: Message, state: FSMContext):
        """Обработка контента сообщения"""
        data = await state.get_data()
        message_type = data.get("message_type")
        
        if message_type == "text":
            if not message.text:
                await message.answer("❌ Пожалуйста, отправьте текст.")
                return
            
            await state.update_data(content=message.text)
            await self.show_confirmation(message, state)
        
        elif message_type in ["photo", "video", "document"]:
            # Сохраняем медиа файл
            if message_type == "photo" and message.photo:
                file_id = message.photo[-1].file_id
                caption = message.caption or ""
            elif message_type == "video" and message.video:
                file_id = message.video.file_id
                caption = message.caption or ""
            elif message_type == "document" and message.document:
                file_id = message.document.file_id
                caption = message.caption or ""
            else:
                await message.answer(f"❌ Пожалуйста, отправьте {message_type}.")
                return
            
            await state.update_data(
                file_id=file_id,
                caption=caption
            )
            
            # Запрос кнопок, если нужно
            text = "✅ Медиафайл получен\n\n"
            text += f"Подпись: {caption[:100]}{'...' if len(caption) > 100 else ''}\n\n"
            text += "Добавить кнопку под сообщением?\n"
            text += "Формат: Текст кнопки - URL\n"
            text += "Например: Открыть сайт - https://example.com\n\n"
            text += "Если кнопки не нужны, отправьте /skip"
            
            await message.answer(text)
            # Здесь нужно перейти в состояние ожидания кнопок
        
        elif message_type in ["poll", "quiz"]:
            if not message.text:
                await message.answer("❌ Пожалуйста, отправьте текст опроса.")
                return
            
            lines = message.text.split('\n')
            if len(lines) < 3:
                await message.answer("❌ Нужно как минимум вопрос и 2 варианта ответа.")
                return
            
            question = lines[0].strip()
            options = [line.strip() for line in lines[1:] if line.strip()]
            
            await state.update_data(
                question=question,
                options=options,
                is_anonymous=True,
                allows_multiple_answers=False
            )
            await self.show_confirmation(message, state)
    
    async def show_confirmation(self, message: Message, state: FSMContext):
        """Показать подтверждение рассылки"""
        data = await state.get_data()
        
        text = "✅ Подтверждение рассылки\n\n"
        
        # Информация о цели
        target = data.get("target", "all_users")
        target_text = {
            "all_users": "👤 Всем пользователям",
            "all_chats": "💬 Во все чаты",
            "all": "🌐 Всем (пользователи + чаты)",
            "filtered": "🎯 По фильтрам"
        }.get(target, target)
        
        text += f"Цель: {target_text}\n"
        
        # Информация о сообщении
        message_type = data.get("message_type")
        if message_type == "text":
            content_preview = data.get("content", "")[:100]
            if len(data.get("content", "")) > 100:
                content_preview += "..."
            text += f"Тип: Текст\n"
            text += f"Содержимое: {content_preview}\n"
        
        elif message_type in ["photo", "video", "document"]:
            media_type = {"photo": "Фото", "video": "Видео", "document": "Документ"}[message_type]
            caption_preview = data.get("caption", "")[:100]
            if len(data.get("caption", "")) > 100:
                caption_preview += "..."
            text += f"Тип: {media_type}\n"
            text += f"Подпись: {caption_preview}\n"
        
        elif message_type in ["poll", "quiz"]:
            poll_type = "Опрос" if message_type == "poll" else "Викторина"
            text += f"Тип: {poll_type}\n"
            text += f"Вопрос: {data.get('question', '')}\n"
            text += f"Вариантов: {len(data.get('options', []))}\n"
        
        # Предполагаемое количество получателей
        estimated_count = await self.estimate_recipients(data)
        text += f"\n👥 Примерное количество получателей: {estimated_count:,}\n"
        
        text += "\nВыберите действие:"
        
        buttons = [
            ("🚀 Отправить сейчас", "broadcast_confirm_send"),
            ("⏰ Запланировать", "broadcast_confirm_schedule"),
            ("✏️ Редактировать", "broadcast_edit"),
            ("❌ Отменить", "admin_broadcast")
        ]
        
        keyboard = create_keyboard(buttons, columns=2)
        
        await state.set_state(BroadcastStates.waiting_for_confirmation)
        
        if isinstance(message, Message):
            await message.answer(text, reply_markup=keyboard)
        elif isinstance(message, CallbackQuery):
            await message.message.edit_text(text, reply_markup=keyboard)
    
    async def estimate_recipients(self, data: Dict) -> int:
        """Оценить количество получателей"""
        db = self.admin_system.database
        target = data.get("target")
        
        if target == "all_users":
            users, total = await db.get_users(limit=1)
            return total
        elif target == "all_chats":
            chats, total = await db.get_chats(limit=1)
            return total
        elif target == "all":
            users, user_total = await db.get_users(limit=1)
            chats, chat_total = await db.get_chats(limit=1)
            return user_total + chat_total
        elif target == "filtered":
            # Здесь нужно применить фильтры
            # Для простоты возвращаем 100
            return 100
        
        return 0
    
    async def confirm_broadcast(self, callback: CallbackQuery, state: FSMContext, send_now: bool = True):
        """Подтверждение и запуск рассылки"""
        data = await state.get_data()
        user_id = callback.from_user.id
        
        # Создание записи в БД
        broadcast = Broadcast(
            created_by=user_id,
            target_type=data.get("target", "all_users"),
            target_filter=data.get("filters", {}),
            message_type=data.get("message_type", "text"),
            message_data=self._prepare_message_data(data),
            status="pending",
            bot_id=self.admin_system.config.bot_id
        )
        
        if not send_now:
            # Запланированная рассылка
            await self.show_schedule_options(callback, state, broadcast)
            return
        
        # Немедленная отправка
        db = self.admin_system.database
        broadcast_id = await db.add_broadcast(broadcast)
        broadcast.id = broadcast_id
        
        # Запуск рассылки в фоне
        task = asyncio.create_task(self.send_broadcast(broadcast))
        self.active_broadcasts[broadcast_id] = task
        
        await state.clear()
        
        text = "✅ Рассылка запущена!\n\n"
        text += f"ID рассылки: {broadcast_id}\n"
        text += "Отслеживать прогресс можно в истории рассылок."
        
        buttons = [
            ("📋 История рассылок", "admin_broadcast_history"),
            ("🛠️ В меню", "admin_menu")
        ]
        
        keyboard = create_keyboard(buttons, columns=2)
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    def _prepare_message_data(self, data: Dict) -> Dict:
        """Подготовка данных сообщения для хранения"""
        message_type = data.get("message_type")
        
        if message_type == "text":
            return {
                "text": data.get("content", ""),
                "parse_mode": "HTML",
                "buttons": data.get("buttons", [])
            }
        
        elif message_type in ["photo", "video", "document"]:
            return {
                "file_id": data.get("file_id"),
                "caption": data.get("caption", ""),
                "parse_mode": "HTML",
                "buttons": data.get("buttons", [])
            }
        
        elif message_type in ["poll", "quiz"]:
            return {
                "question": data.get("question", ""),
                "options": data.get("options", []),
                "is_anonymous": data.get("is_anonymous", True),
                "allows_multiple_answers": data.get("allows_multiple_answers", False),
                "type": "quiz" if message_type == "quiz" else "regular"
            }
        
        return {}
    
    async def send_broadcast(self, broadcast: Broadcast):
        """Отправка рассылки"""
        db = self.admin_system.database
        
        # Обновление статуса
        broadcast.status = "sending"
        broadcast.started_at = datetime.now()
        await db.update_broadcast(broadcast)
        
        try:
            # Получение списка получателей
            recipients = await self.get_recipients(broadcast)
            
            total_recipients = len(recipients)
            successful = 0
            failed = 0
            
            # Отправка с задержкой
            config = self.admin_system.config.broadcasting
            delay = config.delay_between_messages_ms / 1000  # в секундах
            
            for i, recipient_id in enumerate(recipients):
                try:
                    await self.send_to_recipient(broadcast, recipient_id)
                    successful += 1
                    
                    # Обновление прогресса каждые 10 сообщений
                    if i % 10 == 0:
                        broadcast.sent_count = successful
                        broadcast.failed_count = failed
                        await db.update_broadcast(broadcast)
                    
                    # Задержка между сообщениями
                    if i < total_recipients - 1:
                        await asyncio.sleep(delay)
                        
                except Exception as e:
                    logger.error(f"Ошибка отправки пользователю {recipient_id}: {e}")
                    failed += 1
            
            # Завершение рассылки
            broadcast.status = "completed"
            broadcast.sent_count = successful
            broadcast.failed_count = failed
            broadcast.completed_at = datetime.now()
            await db.update_broadcast(broadcast)
            
            logger.info(f"Рассылка {broadcast.id} завершена: {successful} успешно, {failed} с ошибками")
            
        except Exception as e:
            logger.error(f"Критическая ошибка в рассылке {broadcast.id}: {e}")
            broadcast.status = "failed"
            await db.update_broadcast(broadcast)
        
        finally:
            # Удаление задачи из активных
            if broadcast.id in self.active_broadcasts:
                del self.active_broadcasts[broadcast.id]
    
    async def get_recipients(self, broadcast: Broadcast) -> List[int]:
        """Получить список ID получателей"""
        db = self.admin_system.database
        recipients = []
        
        if broadcast.target_type == "all_users":
            # Получаем всех пользователей пачками
            batch_size = 100
            offset = 0
            
            while True:
                users, _ = await db.get_users(offset=offset, limit=batch_size)
                if not users:
                    break
                
                recipients.extend([user.user_id for user in users])
                offset += batch_size
        
        elif broadcast.target_type == "all_chats":
            # Получаем все чаты
            chats, _ = await db.get_chats()
            recipients.extend([chat.chat_id for chat in chats])
        
        elif broadcast.target_type == "all":
            # Пользователи + чаты
            users, _ = await db.get_users()
            chats, _ = await db.get_chats()
            recipients.extend([user.user_id for user in users])
            recipients.extend([chat.chat_id for chat in chats])
        
        elif broadcast.target_type == "filtered":
            # Применяем фильтры
            filters = broadcast.target_filter
            users, _ = await db.get_users(filters=filters)
            recipients.extend([user.user_id for user in users])
        
        return recipients
    
    async def send_to_recipient(self, broadcast: Broadcast, recipient_id: int):
        """Отправить сообщение конкретному получателю"""
        message_data = broadcast.message_data
        
        if broadcast.message_type == "text":
            text = message_data.get("text", "")
            buttons = message_data.get("buttons", [])
            
            reply_markup = None
            if buttons:
                keyboard_buttons = []
                for button in buttons:
                    if ' - ' in button:
                        text_part, url = button.split(' - ', 1)
                        keyboard_buttons.append([InlineKeyboardButton(text=text_part, url=url)])
                
                if keyboard_buttons:
                    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
            
            await self.bot.send_message(
                chat_id=recipient_id,
                text=text,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        
        elif broadcast.message_type == "photo":
            await self.bot.send_photo(
                chat_id=recipient_id,
                photo=message_data.get("file_id"),
                caption=message_data.get("caption", ""),
                parse_mode="HTML"
            )
        
        elif broadcast.message_type == "video":
            await self.bot.send_video(
                chat_id=recipient_id,
                video=message_data.get("file_id"),
                caption=message_data.get("caption", ""),
                parse_mode="HTML"
            )
        
        elif broadcast.message_type == "document":
            await self.bot.send_document(
                chat_id=recipient_id,
                document=message_data.get("file_id"),
                caption=message_data.get("caption", ""),
                parse_mode="HTML"
            )
        
        elif broadcast.message_type in ["poll", "quiz"]:
            is_anonymous = message_data.get("is_anonymous", True)
            allows_multiple_answers = message_data.get("allows_multiple_answers", False)
            poll_type = "quiz" if broadcast.message_type == "quiz" else "regular"
            
            await self.bot.send_poll(
                chat_id=recipient_id,
                question=message_data.get("question", ""),
                options=message_data.get("options", []),
                is_anonymous=is_anonymous,
                type=poll_type,
                allows_multiple_answers=allows_multiple_answers
            )
    
    async def show_broadcast_history(self, callback: CallbackQuery, page: int = 0):
        """Показать историю рассылок"""
        user_id = callback.from_user.id
        
        security = self.admin_system.security
        if not await security.has_permission(user_id, "broadcast.view"):
            await callback.message.edit_text("❌ У вас нет прав для просмотра истории рассылок.")
            return
        
        db = self.admin_system.database
        offset = page * 10
        
        broadcasts, total = await db.get_broadcasts(offset=offset, limit=10)
        
        text = f"📋 История рассылок\n\n"
        text += f"📊 Всего: {total:,}\n"
        text += f"📄 Страница {page + 1}/{(total + 9) // 10}\n\n"
        
        for i, broadcast in enumerate(broadcasts, start=1):
            status_icons = {
                "pending": "⏳",
                "sending": "🔄",
                "completed": "✅",
                "cancelled": "❌",
                "failed": "⚠️"
            }
            
            status_icon = status_icons.get(broadcast.status, "❓")
            
            text += f"{i}. {status_icon} Рассылка #{broadcast.id}\n"
            text += f"   👤 От: {broadcast.created_by}\n"
            text += f"   📅: {broadcast.created_at.strftime('%d.%m.%Y %H:%M')}\n"
            
            if broadcast.scheduled_for:
                text += f"   ⏰ Запланирована на: {broadcast.scheduled_for.strftime('%d.%m.%Y %H:%M')}\n"
            
            if broadcast.status == "completed":
                success_rate = (broadcast.sent_count / (broadcast.sent_count + broadcast.failed_count) * 100) if (broadcast.sent_count + broadcast.failed_count) > 0 else 0
                text += f"   📨 Отправлено: {broadcast.sent_count}/{broadcast.sent_count + broadcast.failed_count} ({success_rate:.1f}%)\n"
            
            text += "\n"
        
        buttons = [
            ("📊 Статистика", "broadcast_stats"),
            ("⏰ Запланированные", "admin_broadcast_scheduled")
        ]
        
        keyboard = create_pagination_keyboard(
            current_page=page,
            total_pages=(total + 9) // 10,
            prefix="admin_broadcast_history",
            additional_buttons=buttons
        )
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def show_scheduled_broadcasts(self, callback: CallbackQuery):
        """Показать запланированные рассылки"""
        user_id = callback.from_user.id
        
        security = self.admin_system.security
        if not await security.has_permission(user_id, "broadcast.schedule"):
            await callback.message.edit_text("❌ У вас нет прав для просмотра запланированных рассылок.")
            return
        
        db = self.admin_system.database
        
        # Получаем запланированные рассылки
        broadcasts, _ = await db.get_broadcasts(status="pending")
        scheduled = [b for b in broadcasts if b.scheduled_for and b.scheduled_for > datetime.now()]
        
        text = "⏰ Запланированные рассылки\n\n"
        
        if not scheduled:
            text += "Нет запланированных рассылок."
        else:
            for i, broadcast in enumerate(scheduled, start=1):
                time_left = broadcast.scheduled_for - datetime.now()
                hours = time_left.total_seconds() // 3600
                minutes = (time_left.total_seconds() % 3600) // 60
                
                text += f"{i}. Рассылка #{broadcast.id}\n"
                text += f"   📅: {broadcast.scheduled_for.strftime('%d.%m.%Y %H:%M')}\n"
                text += f"   ⏳ Осталось: {int(hours)}ч {int(minutes)}м\n"
                text += f"   👤 Создал: {broadcast.created_by}\n\n"
        
        buttons = [
            ("➕ Новая рассылка", "admin_broadcast_new"),
            ("📋 История", "admin_broadcast_history"),
            ("◀️ Назад", "admin_broadcast")
        ]
        
        keyboard = create_keyboard(buttons, columns=2)
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def cancel_broadcast(self, callback: CallbackQuery, broadcast_id: int):
        """Отменить рассылку"""
        user_id = callback.from_user.id
        
        security = self.admin_system.security
        if not await security.has_permission(user_id, "broadcast.send"):
            await callback.answer("❌ У вас нет прав для отмены рассылок.")
            return
        
        db = self.admin_system.database
        
        # Получаем рассылку
        broadcasts, _ = await db.get_broadcasts()
        broadcast = next((b for b in broadcasts if b.id == broadcast_id), None)
        
        if not broadcast:
            await callback.answer("❌ Рассылка не найдена.")
            return
        
        if broadcast.status != "pending":
            await callback.answer("❌ Можно отменить только ожидающие рассылки.")
            return
        
        # Отмена
        broadcast.status = "cancelled"
        await db.update_broadcast(broadcast)
        
        # Отмена задачи, если она активна
        if broadcast_id in self.active_broadcasts:
            self.active_broadcasts[broadcast_id].cancel()
            del self.active_broadcasts[broadcast_id]
        
        await callback.answer("✅ Рассылка отменена.")
        await self.show_broadcast_history(callback)
    
    async def show_schedule_options(self, callback: CallbackQuery, state: FSMContext, broadcast: Broadcast):
        """Показать опции планирования"""
        text = "⏰ Планирование рассылки\n\n"
        text += "Выберите время отправки:"
        
        buttons = [
            ("⏰ Через 1 час", "schedule_1h"),
            ("⏰ Через 3 часа", "schedule_3h"),
            ("⏰ Через 6 часов", "schedule_6h"),
            ("📅 Завтра в это же время", "schedule_tomorrow"),
            ("📅 Выбрать дату и время", "schedule_custom"),
            ("◀️ Назад", "broadcast_confirmation")
        ]
        
        keyboard = create_keyboard(buttons, columns=2)
        
        await state.update_data(broadcast_data=broadcast.to_dict())
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def schedule_broadcast(self, callback: CallbackQuery, state: FSMContext, schedule_time: datetime):
        """Запланировать рассылку"""
        data = await state.get_data()
        broadcast_data = data.get("broadcast_data")
        
        if not broadcast_data:
            await callback.answer("❌ Ошибка: данные рассылки не найдены.")
            return
        
        # Создание запланированной рассылки
        broadcast = Broadcast.from_dict(broadcast_data)
        broadcast.scheduled_for = schedule_time
        broadcast.status = "pending"
        
        db = self.admin_system.database
        broadcast_id = await db.add_broadcast(broadcast)
        
        # Запуск задачи для отслеживания времени
        asyncio.create_task(self._schedule_broadcast_task(broadcast_id, schedule_time))
        
        await state.clear()
        
        text = "✅ Рассылка запланирована!\n\n"
        text += f"ID рассылки: {broadcast_id}\n"
        text += f"⏰ Время отправки: {schedule_time.strftime('%d.%m.%Y %H:%M')}\n\n"
        text += "Рассылка начнется автоматически в указанное время."
        
        buttons = [
            ("⏰ Запланированные", "admin_broadcast_scheduled"),
            ("🛠️ В меню", "admin_menu")
        ]
        
        keyboard = create_keyboard(buttons, columns=2)
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def _schedule_broadcast_task(self, broadcast_id: int, schedule_time: datetime):
        """Фоновая задача для запланированной рассылки"""
        # Ожидание времени отправки
        wait_time = (schedule_time - datetime.now()).total_seconds()
        
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        
        # Проверка, не отменена ли рассылка
        db = self.admin_system.database
        broadcasts, _ = await db.get_broadcasts()
        broadcast = next((b for b in broadcasts if b.id == broadcast_id), None)
        
        if broadcast and broadcast.status == "pending":
            # Запуск рассылки
            task = asyncio.create_task(self.send_broadcast(broadcast))
            self.active_broadcasts[broadcast_id] = task
    
    async def get_broadcast_stats(self) -> Dict[str, Any]:
        """Получить статистику по рассылкам"""
        db = self.admin_system.database
        
        broadcasts, total = await db.get_broadcasts()
        
        stats = {
            "total": total,
            "by_status": {},
            "by_month": {},
            "success_rate": 0,
            "total_recipients": 0,
            "avg_recipients": 0
        }
        
        total_sent = 0
        total_failed = 0
        
        for broadcast in broadcasts:
            # По статусам
            stats["by_status"][broadcast.status] = stats["by_status"].get(broadcast.status, 0) + 1
            
            # По месяцам
            month_key = broadcast.created_at.strftime("%Y-%m")
            stats["by_month"][month_key] = stats["by_month"].get(month_key, 0) + 1
            
            # Статистика успешности
            if broadcast.status == "completed":
                total_sent += broadcast.sent_count
                total_failed += broadcast.failed_count
        
        # Расчет процента успешности
        total_attempts = total_sent + total_failed
        if total_attempts > 0:
            stats["success_rate"] = (total_sent / total_attempts) * 100
        
        # Среднее количество получателей
        completed_broadcasts = [b for b in broadcasts if b.status == "completed"]
        if completed_broadcasts:
            total_recipients = sum(b.sent_count + b.failed_count for b in completed_broadcasts)
            stats["total_recipients"] = total_recipients
            stats["avg_recipients"] = total_recipients / len(completed_broadcasts)
        
        return stats