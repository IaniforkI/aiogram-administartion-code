import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum

from aiogram import Bot, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from .models import User, Chat, ActionType, ReportType
from .ui import create_keyboard, create_pagination_keyboard
from .security import require_admin, require_chat_admin

logger = logging.getLogger(__name__)

class ReportStatus(Enum):
    """Статусы жалоб"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"

class ReportAction(Enum):
    """Действия с жалобами"""
    DELETE_MESSAGE = "delete_message"
    WARN_USER = "warn_user"
    MUTE_USER = "mute_user"
    BAN_USER = "ban_user"
    IGNORE = "ignore"
    MARK_RESOLVED = "mark_resolved"

class ReportStates(StatesGroup):
    """Состояния для отправки жалобы"""
    waiting_for_report_type = State()
    waiting_for_report_reason = State()

class ReportsManager:
    """Менеджер системы жалоб"""
    
    def __init__(self, admin_system):
        self.admin_system = admin_system
        self.bot = admin_system.bot
        
        # Кэш для частых операций
        self._reports_cache: Dict[int, Dict] = {}
        self._user_reports_cache: Dict[int, List] = {}
        
    async def setup_handlers(self, router):
        """Настройка обработчиков"""
        
        # Команда /report для пользователей
        @router.message(Command("report"))
        async def report_command(message: Message, state: FSMContext):
            """Обработка команды /report"""
            await self.handle_report_command(message, state)
        
        # Ответ на сообщение с жалобой
        @router.message(F.reply_to_message)
        async def handle_report_reply(message: Message, state: FSMContext):
            """Обработка ответа на сообщение с жалобой"""
            replied_message = message.reply_to_message
            
            # Проверяем, является ли replied_message жалобой от бота
            if replied_message.from_user.id == self.bot.id and "жалоб" in replied_message.text:
                await self.handle_admin_report_response(message, replied_message)
    
    async def handle_report_command(self, message: Message, state: FSMContext):
        """Обработка команды /report"""
        # Проверка, что команда используется в ответ на сообщение
        if not message.reply_to_message:
            await message.answer(
                "❌ Пожалуйста, используйте команду /report в ответ на сообщение, "
                "на которое хотите пожаловаться."
            )
            return
        
        # Проверка, что это не приватный чат
        if message.chat.type == "private":
            await message.answer("❌ Жалобы можно отправлять только в чатах.")
            return
        
        # Проверка, что пользователь не жалуется на себя
        if message.reply_to_message.from_user.id == message.from_user.id:
            await message.answer("❌ Нельзя жаловаться на самого себя.")
            return
        
        # Проверка, что пользователь не админ (чтобы не спамили)
        try:
            chat_member = await self.bot.get_chat_member(
                chat_id=message.chat.id,
                user_id=message.from_user.id
            )
            if chat_member.status in ["administrator", "creator"]:
                await message.answer("👑 Админы могут использовать модерацию напрямую.")
                return
        except:
            pass
        
        # Сохранение данных для жалобы
        await state.update_data(
            reported_message_id=message.reply_to_message.message_id,
            reported_user_id=message.reply_to_message.from_user.id,
            chat_id=message.chat.id,
            reporter_id=message.from_user.id
        )
        
        # Показать типы жалоб
        await self.show_report_types(message, state)
    
    async def show_report_types(self, message: Message, state: FSMContext):
        """Показать типы жалоб"""
        text = "⚠️ Отправка жалобы\n\n"
        text += "Выберите тип нарушения:"
        
        buttons = [
            ("📨 Спам", "report_type_spam"),
            ("😠 Оскорбление", "report_type_abuse"),
            ("🎭 Мошенничество", "report_type_scam"),
            ("🔞 Непристойный контент", "report_type_pornography"),
            ("⚡ Насилие/угрозы", "report_type_violence"),
            ("❓ Другое", "report_type_other"),
            ("❌ Отмена", "report_cancel")
        ]
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data)]
            for text, data in buttons
        ])
        
        await state.set_state(ReportStates.waiting_for_report_type)
        await message.answer(text, reply_markup=keyboard)
    
    async def handle_report_type(self, callback: CallbackQuery, state: FSMContext):
        """Обработка выбора типа жалобы"""
        if callback.data == "report_cancel":
            await state.clear()
            await callback.message.delete()
            await callback.answer("❌ Жалоба отменена.")
            return
        
        report_type = callback.data.replace("report_type_", "")
        report_type_enum = {
            "spam": ReportType.SPAM,
            "abuse": ReportType.ABUSE,
            "scam": ReportType.SCAM,
            "pornography": ReportType.PORNOGRAPHY,
            "violence": ReportType.VIOLENCE,
            "other": ReportType.OTHER
        }.get(report_type, ReportType.OTHER)
        
        await state.update_data(report_type=report_type_enum.value)
        
        # Запрос причины
        text = "📝 Укажите причину жалобы:\n\n"
        text += "Опишите подробнее, что именно нарушает правила.\n"
        text += "Максимум 500 символов.\n\n"
        text += "Для отмены отправьте /cancel"
        
        await state.set_state(ReportStates.waiting_for_report_reason)
        await callback.message.edit_text(text)
    
    async def handle_report_reason(self, message: Message, state: FSMContext):
        """Обработка причины жалобы"""
        reason = message.text
        
        if not reason or len(reason.strip()) < 5:
            await message.answer("❌ Пожалуйста, укажите причину жалобы (минимум 5 символов).")
            return
        
        if len(reason) > 500:
            await message.answer("❌ Слишком длинная причина. Максимум 500 символов.")
            return
        
        data = await state.get_data()
        
        # Создание жалобы в БД
        report_id = await self.create_report(
            reporter_id=data["reporter_id"],
            reported_user_id=data["reported_user_id"],
            chat_id=data["chat_id"],
            message_id=data["reported_message_id"],
            report_type=data["report_type"],
            reason=reason[:500]
        )
        
        # Отправка уведомления админам
        await self.notify_admins_about_report(report_id, data)
        
        # Ответ пользователю
        await message.answer(
            "✅ Жалоба отправлена!\n\n"
            "Администраторы рассмотрят её в ближайшее время."
        )
        
        await state.clear()
    
    async def create_report(self, reporter_id: int, reported_user_id: int, chat_id: int,
                           message_id: int, report_type: int, reason: str) -> int:
        """Создание жалобы в БД"""
        from .database import DatabaseManager
        
        db = DatabaseManager.get_instance()
        
        # Проверка на дубликаты (такая же жалоба за последние 5 минут)
        recent_reports = await self.get_recent_reports(
            reporter_id=reporter_id,
            reported_user_id=reported_user_id,
            chat_id=chat_id,
            minutes=5
        )
        
        if recent_reports:
            # Помечаем как дубликат
            report_data = {
                "reporter_id": reporter_id,
                "reported_user_id": reported_user_id,
                "chat_id": chat_id,
                "message_id": message_id,
                "report_type": report_type,
                "reason": reason,
                "status": ReportStatus.DUPLICATE.value,
                "created_at": datetime.now().isoformat(),
                "bot_id": self.admin_system.config.bot_id
            }
            
            # Логирование
            security = self.admin_system.security
            await security.log_action(
                user_id=reporter_id,
                action_type=13,  # REPORT_SUBMITTED
                action_data={
                    "report_type": report_type,
                    "status": "duplicate",
                    "reported_user_id": reported_user_id,
                    "chat_id": chat_id
                },
                chat_id=chat_id
            )
            
            return -1  # ID дубликата
        
        # Создание новой жалобы
        try:
            # Вставка в БД
            cursor = await db.connection.execute(
                f"""
                INSERT INTO {db.get_table_name('reports')}
                (reporter_id, reported_user_id, chat_id, message_id, 
                 report_type, reason, status, created_at, bot_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reporter_id, reported_user_id, chat_id, message_id,
                    report_type, reason, ReportStatus.PENDING.value,
                    datetime.now().isoformat(), self.admin_system.config.bot_id
                )
            )
            
            await db.connection.commit()
            report_id = cursor.lastrowid
            
            # Логирование
            security = self.admin_system.security
            await security.log_action(
                user_id=reporter_id,
                action_type=13,  # REPORT_SUBMITTED
                action_data={
                    "report_id": report_id,
                    "report_type": report_type,
                    "reported_user_id": reported_user_id,
                    "chat_id": chat_id
                },
                chat_id=chat_id
            )
            
            # Кэширование
            self._add_to_cache(report_id, {
                "id": report_id,
                "reporter_id": reporter_id,
                "reported_user_id": reported_user_id,
                "chat_id": chat_id,
                "status": ReportStatus.PENDING.value
            })
            
            return report_id
            
        except Exception as e:
            logger.error(f"Ошибка при создании жалобы: {e}")
            return -1
    
    async def get_recent_reports(self, reporter_id: int, reported_user_id: int,
                                chat_id: int, minutes: int = 5) -> List[Dict]:
        """Получить недавние жалобы"""
        from .database import DatabaseManager
        
        db = DatabaseManager.get_instance()
        
        cutoff_time = datetime.now() - timedelta(minutes=minutes)
        
        cursor = await db.connection.execute(
            f"""
            SELECT * FROM {db.get_table_name('reports')}
            WHERE reporter_id = ? AND reported_user_id = ? AND chat_id = ?
            AND created_at >= ? AND status != ?
            AND bot_id = ?
            """,
            (
                reporter_id, reported_user_id, chat_id,
                cutoff_time.isoformat(), ReportStatus.DUPLICATE.value,
                self.admin_system.config.bot_id
            )
        )
        
        reports = []
        async for row in cursor:
            reports.append(dict(row))
        
        await cursor.close()
        return reports
    
    async def notify_admins_about_report(self, report_id: int, report_data: Dict):
        """Уведомить админов о новой жалобе"""
        if report_id == -1:  # Дубликат
            return
        
        chat_id = report_data["chat_id"]
        
        try:
            # Получение информации о чате
            chat = await self.bot.get_chat(chat_id)
            chat_title = chat.title or "Чат"
            
            # Получение информации о пользователях
            reporter = await self.bot.get_chat_member(chat_id, report_data["reporter_id"])
            reported = await self.bot.get_chat_member(chat_id, report_data["reported_user_id"])
            
            reporter_name = reporter.user.full_name
            reported_name = reported.user.full_name
            
            # Получение сообщения
            message = None
            try:
                message = await self.bot.copy_message(
                    chat_id=self.bot.id,  # Отправляем боту
                    from_chat_id=chat_id,
                    message_id=report_data["reported_message_id"]
                )
            except:
                pass
            
            # Формирование уведомления
            text = f"🚨 Новая жалоба в чате: {chat_title}\n\n"
            text += f"👤 От: {reporter_name}\n"
            text += f"👥 На: {reported_name}\n"
            text += f"📋 Тип: {self._get_report_type_text(report_data['report_type'])}\n"
            text += f"💬 Причина: {report_data.get('reason', 'не указана')}\n\n"
            text += f"🆔 ID жалобы: {report_id}\n"
            text += f"💬 ID чата: {chat_id}\n"
            text += f"📝 ID сообщения: {report_data['reported_message_id']}\n\n"
            text += "Выберите действие:"
            
            # Кнопки действий
            buttons = [
                [
                    InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"report_action_delete:{report_id}"),
                    InlineKeyboardButton(text="⚠️ Предупредить", callback_data=f"report_action_warn:{report_id}")
                ],
                [
                    InlineKeyboardButton(text="🔇 Мут", callback_data=f"report_action_mute:{report_id}"),
                    InlineKeyboardButton(text="🚫 Бан", callback_data=f"report_action_ban:{report_id}")
                ],
                [
                    InlineKeyboardButton(text="✅ Решено", callback_data=f"report_action_resolved:{report_id}"),
                    InlineKeyboardButton(text="❌ Игнорировать", callback_data=f"report_action_ignore:{report_id}")
                ],
                [
                    InlineKeyboardButton(text="📋 Подробнее", callback_data=f"report_details:{report_id}")
                ]
            ]
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            
            # Отправка уведомления всем админам бота
            security = self.admin_system.security
            admins = await security.get_all_bot_admins()
            
            for admin in admins:
                try:
                    # Отправляем текст
                    await self.bot.send_message(
                        chat_id=admin.user_id,
                        text=text,
                        reply_markup=keyboard
                    )
                    
                    # Если есть сообщение, пересылаем его
                    if message:
                        await message.copy(chat_id=admin.user_id)
                        
                except Exception as e:
                    logger.error(f"Ошибка при уведомлении админа {admin.user_id}: {e}")
                    continue
            
        except Exception as e:
            logger.error(f"Ошибка при уведомлении админов: {e}")
    
    def _get_report_type_text(self, report_type: int) -> str:
        """Получить текстовое представление типа жалобы"""
        types = {
            ReportType.SPAM.value: "📨 Спам",
            ReportType.ABUSE.value: "😠 Оскорбление",
            ReportType.SCAM.value: "🎭 Мошенничество",
            ReportType.PORNOGRAPHY.value: "🔞 Непристойный контент",
            ReportType.VIOLENCE.value: "⚡ Насилие/угрозы",
            ReportType.OTHER.value: "❓ Другое"
        }
        return types.get(report_type, "❓ Неизвестно")
    
    async def handle_admin_report_response(self, message: Message, report_message: Message):
        """Обработка ответа админа на жалобу"""
        # Извлекаем ID жалобы из текста сообщения
        import re
        
        report_id_match = re.search(r'ID жалобы:\s*(\d+)', report_message.text)
        if not report_id_match:
            return
        
        report_id = int(report_id_match.group(1))
        
        # Проверка прав
        security = self.admin_system.security
        user_id = message.from_user.id
        
        if not await security.has_permission(user_id, "moderation.reports"):
            await message.answer("❌ У вас нет прав для обработки жалоб.")
            return
        
        # Обновление статуса жалобы
        await self.update_report_status(
            report_id=report_id,
            status=ReportStatus.RESOLVED.value,
            handled_by=user_id,
            admin_comment=message.text
        )
        
        # Ответ админу
        await message.answer(f"✅ Жалоба #{report_id} отмечена как решенная.")
        
        # Уведомление отправителя жалобы
        await self.notify_reporter_about_resolution(report_id, user_id)
    
    async def update_report_status(self, report_id: int, status: str, 
                                 handled_by: int, admin_comment: str = ""):
        """Обновление статуса жалобы"""
        from .database import DatabaseManager
        
        db = DatabaseManager.get_instance()
        
        try:
            await db.connection.execute(
                f"""
                UPDATE {db.get_table_name('reports')}
                SET status = ?, handled_by = ?, handled_at = ?, admin_comment = ?
                WHERE id = ? AND bot_id = ?
                """,
                (
                    status, handled_by, datetime.now().isoformat(),
                    admin_comment[:500], report_id,
                    self.admin_system.config.bot_id
                )
            )
            
            await db.connection.commit()
            
            # Обновление кэша
            if report_id in self._reports_cache:
                self._reports_cache[report_id]["status"] = status
                self._reports_cache[report_id]["handled_by"] = handled_by
                self._reports_cache[report_id]["handled_at"] = datetime.now()
            
            # Логирование
            security = self.admin_system.security
            await security.log_action(
                user_id=handled_by,
                action_type=14,  # REPORT_HANDLED
                action_data={
                    "report_id": report_id,
                    "status": status,
                    "admin_comment": admin_comment
                }
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Ошибка при обновлении статуса жалобы: {e}")
            return False
    
    async def notify_reporter_about_resolution(self, report_id: int, admin_id: int):
        """Уведомить отправителя о решении жалобы"""
        from .database import DatabaseManager
        
        db = DatabaseManager.get_instance()
        
        # Получение информации о жалобе
        cursor = await db.connection.execute(
            f"SELECT * FROM {db.get_table_name('reports')} WHERE id = ? AND bot_id = ?",
            (report_id, self.admin_system.config.bot_id)
        )
        
        row = await cursor.fetchone()
        await cursor.close()
        
        if not row:
            return
        
        report = dict(row)
        
        # Формирование уведомления
        text = "📢 Ваша жалоба рассмотрена\n\n"
        text += f"🆔 Жалоба: #{report_id}\n"
        text += f"✅ Статус: Решена\n"
        text += f"👮‍♂️ Рассмотрена: Администратором\n"
        text += f"⏰ Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
        text += "Спасибо за помощь в поддержании порядка!"
        
        # Отправка уведомления
        try:
            await self.bot.send_message(
                chat_id=report["reporter_id"],
                text=text
            )
        except:
            pass  # Пользователь может быть недоступен
    
    async def show_reports_list(self, callback: CallbackQuery, status: str = "pending", page: int = 0):
        """Показать список жалоб"""
        user_id = callback.from_user.id
        
        security = self.admin_system.security
        if not await security.has_permission(user_id, "moderation.reports"):
            await callback.message.edit_text("❌ У вас нет прав для просмотра жалоб.")
            return
        
        from .database import DatabaseManager
        
        db = DatabaseManager.get_instance()
        
        # Получение жалоб
        offset = page * 10
        
        cursor = await db.connection.execute(
            f"""
            SELECT r.*, u1.first_name as reporter_name, u2.first_name as reported_name
            FROM {db.get_table_name('reports')} r
            LEFT JOIN {db.get_table_name('users')} u1 ON r.reporter_id = u1.user_id
            LEFT JOIN {db.get_table_name('users')} u2 ON r.reported_user_id = u2.user_id
            WHERE r.status = ? AND r.bot_id = ?
            ORDER BY r.created_at DESC
            LIMIT ? OFFSET ?
            """,
            (status, self.admin_system.config.bot_id, 10, offset)
        )
        
        reports = []
        async for row in cursor:
            reports.append(dict(row))
        
        await cursor.close()
        
        # Получение общего количества
        count_cursor = await db.connection.execute(
            f"SELECT COUNT(*) FROM {db.get_table_name('reports')} WHERE status = ? AND bot_id = ?",
            (status, self.admin_system.config.bot_id)
        )
        
        total = (await count_cursor.fetchone())[0]
        await count_cursor.close()
        
        # Формирование текста
        status_text = {
            "pending": "⏳ Ожидающие",
            "in_progress": "🔄 В работе",
            "resolved": "✅ Решенные",
            "rejected": "❌ Отклоненные",
            "duplicate": "📋 Дубликаты"
        }.get(status, status)
        
        text = f"📋 Список жалоб: {status_text}\n\n"
        text += f"📊 Всего: {total:,}\n"
        text += f"📄 Страница {page + 1}/{(total + 9) // 10}\n\n"
        
        if not reports:
            text += "Жалобы не найдены."
        else:
            for i, report in enumerate(reports, start=1):
                report_type = self._get_report_type_text(report["report_type"])
                created_at = datetime.fromisoformat(report["created_at"])
                
                text += f"{i}. #{report['id']} - {report_type}\n"
                text += f"   👤 От: {report.get('reporter_name', 'Неизвестно')}\n"
                text += f"   👥 На: {report.get('reported_name', 'Неизвестно')}\n"
                text += f"   💬 Чат: {report['chat_id']}\n"
                text += f"   📅: {created_at.strftime('%d.%m %H:%M')}\n\n"
        
        # Кнопки фильтрации
        status_buttons = [
            ("⏳ Ожидающие", "reports_status_pending"),
            ("🔄 В работе", "reports_status_in_progress"),
            ("✅ Решенные", "reports_status_resolved"),
            ("❌ Отклоненные", "reports_status_rejected"),
            ("📋 Дубликаты", "reports_status_duplicate")
        ]
        
        # Создание клавиатуры
        keyboard_buttons = []
        
        # Кнопки статусов в два ряда
        for i in range(0, len(status_buttons), 2):
            row = status_buttons[i:i+2]
            keyboard_buttons.append([
                InlineKeyboardButton(text=text, callback_data=data)
                for text, data in row
            ])
        
        # Кнопки навигации
        nav_buttons = []
        
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(
                text="◀️ Назад", 
                callback_data=f"reports_page_{status}_{page-1}"
            ))
        
        nav_buttons.append(InlineKeyboardButton(
            text=f"{page+1}/{(total+9)//10}", 
            callback_data="reports_stats"
        ))
        
        if page < (total + 9) // 10 - 1:
            nav_buttons.append(InlineKeyboardButton(
                text="Вперед ▶️", 
                callback_data=f"reports_page_{status}_{page+1}"
            ))
        
        if nav_buttons:
            keyboard_buttons.append(nav_buttons)
        
        # Кнопка возврата
        keyboard_buttons.append([
            InlineKeyboardButton(text="◀️ В меню", callback_data="admin_moderation")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def handle_report_action(self, callback: CallbackQuery, action: str, report_id: int):
        """Обработка действий с жалобой"""
        user_id = callback.from_user.id
        
        security = self.admin_system.security
        if not await security.has_permission(user_id, "moderation.reports"):
            await callback.answer("❌ У вас нет прав для обработки жалоб.")
            return
        
        from .database import DatabaseManager
        
        db = DatabaseManager.get_instance()
        
        # Получение информации о жалобе
        cursor = await db.connection.execute(
            f"SELECT * FROM {db.get_table_name('reports')} WHERE id = ? AND bot_id = ?",
            (report_id, self.admin_system.config.bot_id)
        )
        
        row = await cursor.fetchone()
        await cursor.close()
        
        if not row:
            await callback.answer("❌ Жалоба не найдена.")
            return
        
        report = dict(row)
        
        # Выполнение действия
        if action == "delete":
            await self._delete_reported_message(report, user_id)
            await self.update_report_status(
                report_id=report_id,
                status=ReportStatus.RESOLVED.value,
                handled_by=user_id,
                admin_comment="Сообщение удалено"
            )
            await callback.answer("✅ Сообщение удалено.")
            
        elif action == "warn":
            await self._warn_reported_user(report, user_id)
            await self.update_report_status(
                report_id=report_id,
                status=ReportStatus.RESOLVED.value,
                handled_by=user_id,
                admin_comment="Пользователь предупрежден"
            )
            await callback.answer("✅ Пользователь предупрежден.")
            
        elif action == "mute":
            await self._mute_reported_user(report, user_id)
            await self.update_report_status(
                report_id=report_id,
                status=ReportStatus.RESOLVED.value,
                handled_by=user_id,
                admin_comment="Пользователь замучен"
            )
            await callback.answer("✅ Пользователь замучен.")
            
        elif action == "ban":
            await self._ban_reported_user(report, user_id)
            await self.update_report_status(
                report_id=report_id,
                status=ReportStatus.RESOLVED.value,
                handled_by=user_id,
                admin_comment="Пользователь забанен"
            )
            await callback.answer("✅ Пользователь забанен.")
            
        elif action == "resolved":
            await self.update_report_status(
                report_id=report_id,
                status=ReportStatus.RESOLVED.value,
                handled_by=user_id,
                admin_comment="Отмечено как решенное"
            )
            await callback.answer("✅ Жалоба отмечена как решенная.")
            
        elif action == "ignore":
            await self.update_report_status(
                report_id=report_id,
                status=ReportStatus.REJECTED.value,
                handled_by=user_id,
                admin_comment="Игнорировано"
            )
            await callback.answer("✅ Жалоба отклонена.")
        
        # Обновление сообщения
        await self.show_reports_list(callback)
    
    async def _delete_reported_message(self, report: Dict, admin_id: int):
        """Удаление сообщения из жалобы"""
        try:
            await self.bot.delete_message(
                chat_id=report["chat_id"],
                message_id=report["message_id"]
            )
            
            # Логирование
            security = self.admin_system.security
            await security.log_action(
                user_id=admin_id,
                action_type=7,  # MESSAGE_DELETED
                action_data={
                    "chat_id": report["chat_id"],
                    "message_id": report["message_id"],
                    "reason": "report",
                    "report_id": report["id"]
                },
                chat_id=report["chat_id"]
            )
            
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")
    
    async def _warn_reported_user(self, report: Dict, admin_id: int):
        """Выдать предупреждение пользователю из жалобы"""
        from .database import DatabaseManager
        
        db = DatabaseManager.get_instance()
        
        # Получение пользователя
        user = await db.get_user(report["reported_user_id"])
        if not user:
            return
        
        # Увеличение счетчика варнов
        user.warnings += 1
        await db.update_user(user)
        
        # Отправка уведомления пользователю
        try:
            warning_text = f"⚠️ Вы получили предупреждение!\n\n"
            warning_text += f"Причина: Жалоба от пользователя\n"
            warning_text += f"Тип нарушения: {self._get_report_type_text(report['report_type'])}\n"
            warning_text += f"Всего предупреждений: {user.warnings}\n\n"
            warning_text += "Пожалуйста, соблюдайте правила чата."
            
            await self.bot.send_message(
                chat_id=report["reported_user_id"],
                text=warning_text
            )
        except:
            pass  # Пользователь может быть недоступен
        
        # Логирование
        security = self.admin_system.security
        await security.log_action(
            user_id=admin_id,
            action_type=4,  # USER_WARNED
            action_data={
                "target_user_id": report["reported_user_id"],
                "reason": "report",
                "report_id": report["id"],
                "warnings_count": user.warnings
            },
            chat_id=report["chat_id"]
        )
    
    async def _mute_reported_user(self, report: Dict, admin_id: int):
        """Замутить пользователя из жалобы"""
        from aiogram.types import ChatPermissions
        
        try:
            until_date = datetime.now() + timedelta(hours=1)
            
            await self.bot.restrict_chat_member(
                chat_id=report["chat_id"],
                user_id=report["reported_user_id"],
                permissions=ChatPermissions(
                    can_send_messages=False,
                    can_send_media_messages=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False
                ),
                until_date=until_date
            )
            
            # Уведомление в чат
            notification = f"👤 Пользователь был замучен на 1 час.\n"
            notification += f"Причина: Жалоба от участника чата"
            
            await self.bot.send_message(
                chat_id=report["chat_id"],
                text=notification
            )
            
            # Логирование
            security = self.admin_system.security
            await security.log_action(
                user_id=admin_id,
                action_type=15,  # USER_MUTED
                action_data={
                    "target_user_id": report["reported_user_id"],
                    "duration": 3600,
                    "reason": "report",
                    "report_id": report["id"]
                },
                chat_id=report["chat_id"]
            )
            
        except Exception as e:
            logger.error(f"Ошибка при муте пользователя: {e}")
    
    async def _ban_reported_user(self, report: Dict, admin_id: int):
        """Забанить пользователя из жалобы"""
        try:
            await self.bot.ban_chat_member(
                chat_id=report["chat_id"],
                user_id=report["reported_user_id"]
            )
            
            # Уведомление в чат
            notification = f"🚫 Пользователь был забанен.\n"
            notification += f"Причина: Жалоба от участника чата"
            
            await self.bot.send_message(
                chat_id=report["chat_id"],
                text=notification
            )
            
            # Логирование
            security = self.admin_system.security
            await security.log_action(
                user_id=admin_id,
                action_type=2,  # USER_BLOCKED
                action_data={
                    "target_user_id": report["reported_user_id"],
                    "reason": "report",
                    "report_id": report["id"]
                },
                chat_id=report["chat_id"]
            )
            
        except Exception as e:
            logger.error(f"Ошибка при бане пользователя: {e}")
    
    async def get_report_stats(self, days: int = 7) -> Dict[str, Any]:
        """Получить статистику по жалобам"""
        from .database import DatabaseManager
        
        db = DatabaseManager.get_instance()
        
        start_date = datetime.now() - timedelta(days=days)
        
        # Общая статистика
        cursor = await db.connection.execute(
            f"""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status = 'resolved' THEN 1 ELSE 0 END) as resolved,
                SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) as rejected,
                SUM(CASE WHEN status = 'duplicate' THEN 1 ELSE 0 END) as duplicate
            FROM {db.get_table_name('reports')}
            WHERE created_at >= ? AND bot_id = ?
            """,
            (start_date.isoformat(), self.admin_system.config.bot_id)
        )
        
        row = await cursor.fetchone()
        await cursor.close()
        
        stats = dict(row) if row else {}
        
        # Статистика по типам
        type_cursor = await db.connection.execute(
            f"""
            SELECT report_type, COUNT(*) as count
            FROM {db.get_table_name('reports')}
            WHERE created_at >= ? AND bot_id = ?
            GROUP BY report_type
            ORDER BY count DESC
            """,
            (start_date.isoformat(), self.admin_system.config.bot_id)
        )
        
        stats["by_type"] = {}
        async for row in type_cursor:
            stats["by_type"][row["report_type"]] = row["count"]
        
        await type_cursor.close()
        
        # Топ пользователей по жалобам
        user_cursor = await db.connection.execute(
            f"""
            SELECT reported_user_id, COUNT(*) as report_count
            FROM {db.get_table_name('reports')}
            WHERE created_at >= ? AND bot_id = ?
            GROUP BY reported_user_id
            ORDER BY report_count DESC
            LIMIT 10
            """,
            (start_date.isoformat(), self.admin_system.config.bot_id)
        )
        
        stats["top_reported"] = []
        async for row in user_cursor:
            stats["top_reported"].append({
                "user_id": row["reported_user_id"],
                "count": row["report_count"]
            })
        
        await user_cursor.close()
        
        # Среднее время обработки
        time_cursor = await db.connection.execute(
            f"""
            SELECT 
                AVG(julianday(handled_at) - julianday(created_at)) * 24 * 60 as avg_minutes
            FROM {db.get_table_name('reports')}
            WHERE status = 'resolved' AND handled_at IS NOT NULL 
            AND created_at >= ? AND bot_id = ?
            """,
            (start_date.isoformat(), self.admin_system.config.bot_id)
        )
        
        row = await time_cursor.fetchone()
        await time_cursor.close()
        
        stats["avg_resolution_time"] = row["avg_minutes"] if row and row["avg_minutes"] else 0
        
        return stats
    
    def _add_to_cache(self, report_id: int, report_data: Dict):
        """Добавить жалобу в кэш"""
        self._reports_cache[report_id] = report_data
        
        # Добавление в кэш пользователя
        reporter_id = report_data.get("reporter_id")
        reported_user_id = report_data.get("reported_user_id")
        
        if reporter_id:
            if reporter_id not in self._user_reports_cache:
                self._user_reports_cache[reporter_id] = []
            self._user_reports_cache[reporter_id].append(report_id)
        
        if reported_user_id:
            if reported_user_id not in self._user_reports_cache:
                self._user_reports_cache[reported_user_id] = []
            self._user_reports_cache[reported_user_id].append(report_id)
    
    async def cleanup_old_reports(self, days_to_keep: int = 30):
        """Очистка старых жалоб"""
        from .database import DatabaseManager
        
        db = DatabaseManager.get_instance()
        
        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        
        try:
            # Удаление старых жалоб
            await db.connection.execute(
                f"""
                DELETE FROM {db.get_table_name('reports')}
                WHERE created_at < ? AND status IN ('resolved', 'rejected', 'duplicate')
                AND bot_id = ?
                """,
                (cutoff_date.isoformat(), self.admin_system.config.bot_id)
            )
            
            await db.connection.commit()
            
            # Очистка кэша
            self._reports_cache = {}
            self._user_reports_cache = {}
            
            logger.info(f"Очищены старые жалобы (старше {days_to_keep} дней)")
            
        except Exception as e:
            logger.error(f"Ошибка при очистке старых жалоб: {e}")