from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from typing import Dict, Any, Optional, List
import asyncio
import logging

from .security import require_admin, throttle_command, AdminLevel
from .models import User, Chat, BotAdmin
from .ui import (
    create_keyboard,
    create_pagination_keyboard,
    create_confirmation_keyboard,
    format_user_info,
    format_chat_info,
    create_admin_menu
)

logger = logging.getLogger(__name__)

class AdminStates(StatesGroup):
    """Состояния админ-панели"""
    waiting_for_user_search = State()
    waiting_for_broadcast_message = State()
    waiting_for_command_name = State()
    waiting_for_command_response = State()
    waiting_for_chat_settings = State()
    waiting_for_user_block_reason = State()

class AdminPanel:
    """Класс админ-панели"""
    
    def __init__(self, admin_system):
        self.admin_system = admin_system
        self.router = Router()
        self.setup_handlers()
        
        # Кэш для пагинации
        self._pagination_cache: Dict[str, Any] = {}
        
    def setup_handlers(self):
        """Настройка обработчиков"""
        
        # Команда /apanel - главное меню
        @self.router.message(Command("apanel"))
        @require_admin(AdminLevel.JUNIOR)
        @throttle_command()
        async def admin_panel(message: Message):
            """Главное меню админ-панели"""
            user_id = message.from_user.id
            
            # Проверка прав
            security = self.admin_system.security
            admin = await security.check_bot_admin(user_id)
            
            if not admin:
                await message.answer("❌ У вас нет доступа к админ-панели.")
                return
            
            # Создание меню
            menu_text = "🛠️ ПАНЕЛЬ АДМИНИСТРАТОРА\n\n"
            menu_text += f"👤 Ваш уровень: {self._get_admin_level_text(admin.level)}\n"
            menu_text += f"📊 Бот: {self.admin_system.config.bot_name or 'Не указан'}\n\n"
            menu_text += "Выберите раздел:"
            
            keyboard = create_admin_menu(admin.level)
            
            await message.answer(menu_text, reply_markup=keyboard)
        
        # Обработка callback-запросов от меню
        @self.router.callback_query(F.data.startswith("admin_"))
        @require_admin(AdminLevel.JUNIOR)
        @throttle_command()
        async def handle_admin_callback(callback: CallbackQuery):
            """Обработка нажатий на кнопки админ-панели"""
            data = callback.data
            
            if data == "admin_menu":
                await self.show_admin_menu(callback)
            elif data.startswith("admin_users"):
                await self.handle_users_callback(callback, data)
            elif data.startswith("admin_chats"):
                await self.handle_chats_callback(callback, data)
            elif data.startswith("admin_stats"):
                await self.handle_stats_callback(callback, data)
            elif data.startswith("admin_settings"):
                await self.handle_settings_callback(callback, data)
            elif data.startswith("admin_broadcast"):
                await self.handle_broadcast_callback(callback, data)
            elif data.startswith("admin_moderation"):
                await self.handle_moderation_callback(callback, data)
            elif data.startswith("admin_extras"):
                await self.handle_extras_callback(callback, data)
            elif data.startswith("admin_user_action"):
                await self.handle_user_action_callback(callback, data)
            elif data.startswith("admin_chat_action"):
                await self.handle_chat_action_callback(callback, data)
            
            await callback.answer()
        
        # Команда /cancel для отмены действий
        @self.router.message(Command("cancel"))
        async def cancel_action(message: Message, state: FSMContext):
            """Отмена текущего действия"""
            current_state = await state.get_state()
            if current_state is None:
                await message.answer("❌ Нет активных действий для отмены.")
                return
            
            await state.clear()
            await message.answer("✅ Действие отменено.")
    
    def _get_admin_level_text(self, level: int) -> str:
        """Получить текстовое представление уровня админа"""
        levels = {
            1: "👶 Младший админ",
            2: "👨‍💼 Старший админ", 
            3: "👑 Главный админ"
        }
        return levels.get(level, f"Уровень {level}")
    
    async def show_admin_menu(self, callback: CallbackQuery):
        """Показать главное меню"""
        user_id = callback.from_user.id
        security = self.admin_system.security
        admin = await security.check_bot_admin(user_id)
        
        if not admin:
            await callback.message.edit_text("❌ У вас нет доступа к админ-панели.")
            return
        
        menu_text = "🛠️ ПАНЕЛЬ АДМИНИСТРАТОРА\n\n"
        menu_text += f"👤 Ваш уровень: {self._get_admin_level_text(admin.level)}\n"
        menu_text += "Выберите раздел:"
        
        keyboard = create_admin_menu(admin.level)
        await callback.message.edit_text(menu_text, reply_markup=keyboard)
    
    async def handle_users_callback(self, callback: CallbackQuery, data: str):
        """Обработка callback-ов раздела пользователей"""
        action = data.replace("admin_users_", "")
        
        if action == "list":
            await self.show_users_list(callback)
        elif action == "search":
            await self.start_user_search(callback)
        elif action == "blocked":
            await self.show_blocked_users(callback)
        elif action == "stats":
            await self.show_users_stats(callback)
    
    async def show_users_list(self, callback: CallbackQuery, page: int = 0, page_size: int = 10):
        """Показать список пользователей"""
        user_id = callback.from_user.id
        
        # Проверка прав
        security = self.admin_system.security
        if not await security.has_permission(user_id, "users.view"):
            await callback.message.edit_text("❌ У вас нет прав для просмотра пользователей.")
            return
        
        # Получение пользователей с пагинацией
        db = self.admin_system.database
        offset = page * page_size
        
        users, total = await db.get_users(
            offset=offset,
            limit=page_size,
            order_by="last_activity DESC"
        )
        
        if not users:
            await callback.message.edit_text("📭 Пользователи не найдены.")
            return
        
        # Формирование текста
        text = f"👥 Список пользователей\n\n"
        text += f"📊 Всего: {total}\n"
        text += f"📄 Страница {page + 1}/{(total + page_size - 1) // page_size}\n\n"
        
        for i, user in enumerate(users, start=1):
            status_icon = "✅" if user.status.value == 1 else "❌" if user.status.value in [2, 3] else "⏸️"
            text += f"{i}. {status_icon} {user.full_name}"
            if user.username:
                text += f" (@{user.username})"
            text += f"\n   🆔: {user.user_id} | ⭐: {user.rating} | ⚠️: {user.warnings}\n"
            text += f"   📅: {user.last_activity.strftime('%d.%m.%Y %H:%M')}\n\n"
        
        # Создание клавиатуры
        keyboard = create_pagination_keyboard(
            current_page=page,
            total_pages=(total + page_size - 1) // page_size,
            prefix="admin_users_list",
            additional_buttons=[
                ("🔍 Поиск", "admin_users_search"),
                ("📊 Статистика", "admin_users_stats")
            ]
        )
        
        # Кэширование данных для быстрых действий
        cache_key = f"users_list_{user_id}_{page}"
        self._pagination_cache[cache_key] = {
            "users": [u.user_id for u in users],
            "page": page,
            "total": total
        }
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def start_user_search(self, callback: CallbackQuery):
        """Начать поиск пользователя"""
        user_id = callback.from_user.id
        
        security = self.admin_system.security
        if not await security.has_permission(user_id, "users.search"):
            await callback.message.edit_text("❌ У вас нет прав для поиска пользователей.")
            return
        
        text = "🔍 Поиск пользователей\n\n"
        text += "Отправьте один из вариантов:\n"
        text += "• ID пользователя (например: 123456789)\n"
        text += "• Username (например: @username)\n"
        text += "• Часть имени (например: Иван)\n"
        text += "• Email или телефон (если указаны)\n\n"
        text += "Для отмены отправьте /cancel"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_users")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard)
        # Здесь должен быть переход в состояние поиска
    
    async def handle_user_action_callback(self, callback: CallbackQuery, data: str):
        """Обработка действий с пользователем"""
        parts = data.split(":")
        if len(parts) < 3:
            await callback.answer("❌ Ошибка обработки действия")
            return
        
        action = parts[1]
        target_user_id = int(parts[2])
        
        user_id = callback.from_user.id
        security = self.admin_system.security
        
        # Получение информации о пользователе
        db = self.admin_system.database
        target_user = await db.get_user(target_user_id)
        
        if not target_user:
            await callback.answer("❌ Пользователь не найден")
            return
        
        if action == "view":
            await self.show_user_details(callback, target_user)
        elif action == "block":
            if not await security.has_permission(user_id, "users.block"):
                await callback.answer("❌ У вас нет прав для блокировки пользователей")
                return
            await self.block_user_dialog(callback, target_user)
        elif action == "unblock":
            if not await security.has_permission(user_id, "users.unblock"):
                await callback.answer("❌ У вас нет прав для разблокировки пользователей")
                return
            await self.unblock_user(callback, target_user)
        elif action == "edit":
            if not await security.has_permission(user_id, "users.edit"):
                await callback.answer("❌ У вас нет прав для редактирования пользователей")
                return
            await self.edit_user_dialog(callback, target_user)
        elif action == "stats":
            await self.show_user_stats(callback, target_user)
    
    async def show_user_details(self, callback: CallbackQuery, user: User):
        """Показать детальную информацию о пользователе"""
        text = format_user_info(user)
        
        # Создание кнопок действий
        buttons = []
        
        security = self.admin_system.security
        user_id = callback.from_user.id
        
        if await security.has_permission(user_id, "users.block"):
            if user.status.value == 1:  # Активен
                buttons.append(("🔒 Заблокировать", f"admin_user_action:block:{user.user_id}"))
            else:
                buttons.append(("🔓 Разблокировать", f"admin_user_action:unblock:{user.user_id}"))
        
        if await security.has_permission(user_id, "users.edit"):
            buttons.append(("✏️ Редактировать", f"admin_user_action:edit:{user.user_id}"))
        
        buttons.append(("📊 Статистика", f"admin_user_action:stats:{user.user_id}"))
        buttons.append(("◀️ Назад", "admin_users_list_0"))
        
        keyboard = create_keyboard(buttons, columns=2)
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def block_user_dialog(self, callback: CallbackQuery, user: User):
        """Диалог блокировки пользователя"""
        text = f"🔒 Блокировка пользователя\n\n"
        text += f"Пользователь: {user.full_name}\n"
        text += f"ID: {user.user_id}\n\n"
        text += "Выберите тип блокировки:"
        
        buttons = [
            ("⏰ Временная (1 час)", f"admin_block_temp:1h:{user.user_id}"),
            ("⏰ Временная (1 день)", f"admin_block_temp:1d:{user.user_id}"),
            ("⏰ Временная (7 дней)", f"admin_block_temp:7d:{user.user_id}"),
            ("⛔ Постоянная", f"admin_block_perm:{user.user_id}"),
            ("◀️ Назад", f"admin_user_action:view:{user.user_id}")
        ]
        
        keyboard = create_keyboard(buttons, columns=2)
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def unblock_user(self, callback: CallbackQuery, user: User):
        """Разблокировка пользователя"""
        from .models import UserStatus
        
        user.status = UserStatus.ACTIVE
        
        db = self.admin_system.database
        await db.update_user(user)
        
        # Логирование действия
        security = self.admin_system.security
        await security.log_action(
            user_id=callback.from_user.id,
            action_type=3,  # USER_UNBLOCKED
            action_data={"target_user_id": user.user_id}
        )
        
        await callback.answer("✅ Пользователь разблокирован")
        await self.show_user_details(callback, user)
    
    async def handle_chats_callback(self, callback: CallbackQuery, data: str):
        """Обработка callback-ов раздела чатов"""
        action = data.replace("admin_chats_", "")
        
        if action == "list":
            await self.show_chats_list(callback)
        elif action == "stats":
            await self.show_chats_stats(callback)
        elif action == "manage":
            await self.show_chat_management(callback)
    
    async def show_chats_list(self, callback: CallbackQuery, page: int = 0, page_size: int = 10):
        """Показать список чатов"""
        user_id = callback.from_user.id
        
        security = self.admin_system.security
        if not await security.has_permission(user_id, "chats.view"):
            await callback.message.edit_text("❌ У вас нет прав для просмотра чатов.")
            return
        
        db = self.admin_system.database
        offset = page * page_size
        
        chats, total = await db.get_chats(
            offset=offset,
            limit=page_size,
            chat_type=None,  # Все типы
            order_by="last_activity DESC"
        )
        
        if not chats:
            await callback.message.edit_text("📭 Чаты не найдены.")
            return
        
        text = f"💬 Список чатов\n\n"
        text += f"📊 Всего: {total}\n"
        text += f"📄 Страница {page + 1}/{(total + page_size - 1) // page_size}\n\n"
        
        for i, chat in enumerate(chats, start=1):
            type_icon = "👥" if chat.chat_type in ["group", "supergroup"] else "🔒"
            text += f"{i}. {type_icon} {chat.title}\n"
            text += f"   🆔: {chat.chat_id} | 👥: {chat.members_count}\n"
            if chat.username:
                text += f"   @{chat.username}\n"
            text += f"   📅: {chat.last_activity.strftime('%d.%m.%Y %H:%M')}\n\n"
        
        keyboard = create_pagination_keyboard(
            current_page=page,
            total_pages=(total + page_size - 1) // page_size,
            prefix="admin_chats_list",
            additional_buttons=[
                ("📊 Статистика", "admin_chats_stats"),
                ("⚙️ Управление", "admin_chats_manage")
            ]
        )
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def handle_stats_callback(self, callback: CallbackQuery, data: str):
        """Обработка callback-ов раздела статистики"""
        from .statistics import StatisticsManager
        
        action = data.replace("admin_stats_", "")
        
        stats_manager = StatisticsManager(self.admin_system)
        
        if action == "overview":
            await stats_manager.show_overview(callback)
        elif action == "users":
            await stats_manager.show_users_stats(callback)
        elif action == "chats":
            await stats_manager.show_chats_stats(callback)
        elif action == "charts":
            await stats_manager.show_charts_menu(callback)
    
    async def handle_settings_callback(self, callback: CallbackQuery, data: str):
        """Обработка callback-ов раздела настроек"""
        action = data.replace("admin_settings_", "")
        
        if action == "main":
            await self.show_bot_settings(callback)
        elif action == "status":
            await self.show_bot_status_settings(callback)
        elif action == "admins":
            await self.show_bot_admins_list(callback)
        elif action == "backups":
            await self.show_backups_menu(callback)
    
    async def show_bot_settings(self, callback: CallbackQuery):
        """Показать настройки бота"""
        user_id = callback.from_user.id
        
        security = self.admin_system.security
        if not await security.has_permission(user_id, "settings.view"):
            await callback.message.edit_text("❌ У вас нет прав для просмотра настроек.")
            return
        
        config = self.admin_system.config
        
        text = "⚙️ Настройки бота\n\n"
        text += f"🤖 Имя бота: {config.bot_name or 'Не указано'}\n"
        text += f"🌐 Язык по умолчанию: {config.default_language}\n"
        text += f"🕐 Часовой пояс: {config.timezone}\n"
        text += f"💾 Путь к БД: {config.database.path}\n"
        text += f"🔐 Троттлинг: {'✅ Включен' if config.security.throttling_enabled else '❌ Выключен'}\n\n"
        text += "Выберите раздел настроек:"
        
        buttons = [
            ("📝 Статус бота", "admin_settings_status"),
            ("👑 Админы бота", "admin_settings_admins"),
            ("💾 Бэкапы", "admin_settings_backups"),
            ("◀️ Назад", "admin_menu")
        ]
        
        keyboard = create_keyboard(buttons, columns=2)
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def show_bot_admins_list(self, callback: CallbackQuery, page: int = 0):
        """Показать список админов бота"""
        user_id = callback.from_user.id
        
        security = self.admin_system.security
        if not await security.has_permission(user_id, "admins.view"):
            await callback.message.edit_text("❌ У вас нет прав для просмотра админов.")
            return
        
        db = self.admin_system.database
        admins = await db.get_bot_admins()
        
        text = "👑 Админы бота\n\n"
        
        for admin in admins:
            # Получение информации о пользователе
            user = await db.get_user(admin.user_id)
            user_name = user.full_name if user else f"ID: {admin.user_id}"
            level_text = self._get_admin_level_text(admin.level)
            
            text += f"• {level_text}: {user_name}\n"
            if user and user.username:
                text += f"  @{user.username}\n"
            text += f"  🆔: {admin.user_id} | 📅: {admin.added_date.strftime('%d.%m.%Y')}\n\n"
        
        # Проверка, может ли текущий пользователь добавлять админов
        can_add = await security.has_permission(user_id, "admins.add")
        
        buttons = []
        if can_add:
            buttons.append(("➕ Добавить админа", "admin_add_admin"))
        buttons.append(("◀️ Назад", "admin_settings"))
        
        keyboard = create_keyboard(buttons, columns=1)
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def handle_broadcast_callback(self, callback: CallbackQuery, data: str):
        """Обработка callback-ов раздела рассылок"""
        from .broadcasting import BroadcastingManager
        
        action = data.replace("admin_broadcast_", "")
        
        broadcast_manager = BroadcastingManager(self.admin_system)
        
        if action == "new":
            await broadcast_manager.start_new_broadcast(callback)
        elif action == "history":
            await broadcast_manager.show_broadcast_history(callback)
        elif action == "scheduled":
            await broadcast_manager.show_scheduled_broadcasts(callback)
    
    async def handle_moderation_callback(self, callback: CallbackQuery, data: str):
        """Обработка callback-ов раздела модерации"""
        from .reports import ReportsManager
        from .automoderation import AutoModerationManager
        
        action = data.replace("admin_moderation_", "")
        
        if action == "reports":
            reports_manager = ReportsManager(self.admin_system)
            await reports_manager.show_reports_list(callback)
        elif action == "automod":
            automod_manager = AutoModerationManager(self.admin_system)
            await automod_manager.show_settings(callback)
        elif action == "violators":
            await self.show_violators_list(callback)
    
    async def handle_extras_callback(self, callback: CallbackQuery, data: str):
        """Обработка callback-ов дополнительных функций"""
        from .polls import PollsManager
        from .giveaways import GiveawaysManager
        from .custom_commands import CustomCommandsManager
        
        action = data.replace("admin_extras_", "")
        
        if action == "polls":
            polls_manager = PollsManager(self.admin_system)
            await polls_manager.show_polls_menu(callback)
        elif action == "giveaways":
            giveaways_manager = GiveawaysManager(self.admin_system)
            await giveaways_manager.show_giveaways_menu(callback)
        elif action == "commands":
            commands_manager = CustomCommandsManager(self.admin_system)
            await commands_manager.show_commands_list(callback)
        elif action == "logs":
            await self.show_logs_menu(callback)
    
    async def show_logs_menu(self, callback: CallbackQuery):
        """Показать меню логов"""
        user_id = callback.from_user.id
        
        security = self.admin_system.security
        if not await security.has_permission(user_id, "system.logs"):
            await callback.message.edit_text("❌ У вас нет прав для просмотра логов.")
            return
        
        text = "📋 Системные логи\n\n"
        text += "Выберите тип логов для просмотра:"
        
        buttons = [
            ("👤 Действия пользователей", "admin_logs_user_actions"),
            ("🛡️ Действия админов", "admin_logs_admin_actions"),
            ("⚠️ Ошибки системы", "admin_logs_errors"),
            ("◀️ Назад", "admin_extras")
        ]
        
        keyboard = create_keyboard(buttons, columns=2)
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def show_violators_list(self, callback: CallbackQuery, page: int = 0):
        """Показать список нарушителей"""
        user_id = callback.from_user.id
        
        security = self.admin_system.security
        if not await security.has_permission(user_id, "moderation.reports"):
            await callback.message.edit_text("❌ У вас нет прав для просмотра нарушителей.")
            return
        
        db = self.admin_system.database
        offset = page * 10
        
        # Получение пользователей с варнами
        users, total = await db.get_users(
            offset=offset,
            limit=10,
            filters={"min_warnings": 1},
            order_by="warnings DESC"
        )
        
        text = f"⚠️ Нарушители (с варнами)\n\n"
        text += f"📊 Всего: {total}\n"
        text += f"📄 Страница {page + 1}/{(total + 9) // 10}\n\n"
        
        for i, user in enumerate(users, start=1):
            text += f"{i}. {user.full_name}\n"
            text += f"   🆔: {user.user_id} | ⚠️: {user.warnings}\n"
            if user.username:
                text += f"   @{user.username}\n"
            text += "\n"
        
        keyboard = create_pagination_keyboard(
            current_page=page,
            total_pages=(total + 9) // 10,
            prefix="admin_violators",
            additional_buttons=[
                ("◀️ Назад", "admin_moderation")
            ]
        )
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    def get_router(self) -> Router:
        """Получить роутер админ-панели"""
        return self.router