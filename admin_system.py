"""
Основной класс системы администрирования для Telegram ботов
"""

import asyncio
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import signal
import sys

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeDefault
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage

from .config import AdminConfig, DatabaseConfig, SecurityConfig
from .database import DatabaseManager
from .security import SecurityManager
from .admin_panel import AdminPanel
from .user_management import UserManagementManager
from .chat_management import ChatManagementManager
from .statistics import StatisticsManager
from .broadcasting import BroadcastingManager
from .automoderation import AutoModerationManager
from .reports import ReportsManager
from .custom_commands import CustomCommandsManager
from .rating import RatingManager
from .polls import PollsManager
from .giveaways import GiveawaysManager
from .logs import LogsManager
from .backup import BackupManager
from .ui import create_keyboard

logger = logging.getLogger(__name__)

class AdminSystem:
    """Основной класс системы администрирования"""
    
    _instance = None
    
    def __init__(self, config: AdminConfig):
        if AdminSystem._instance is not None:
            raise Exception("AdminSystem уже инициализирован. Используйте get_instance()")
        
        self.config = config
        self.bot: Optional[Bot] = None
        self.dispatcher: Optional[Dispatcher] = None
        self._is_initialized = False
        self._background_tasks: List[asyncio.Task] = []
        
        # Инициализация менеджеров
        self.database: Optional[DatabaseManager] = None
        self.security: Optional[SecurityManager] = None
        self.admin_panel: Optional[AdminPanel] = None
        self.user_management: Optional[UserManagementManager] = None
        self.chat_management: Optional[ChatManagementManager] = None
        self.statistics: Optional[StatisticsManager] = None
        self.broadcasting: Optional[BroadcastingManager] = None
        self.automoderation: Optional[AutoModerationManager] = None
        self.reports: Optional[ReportsManager] = None
        self.custom_commands: Optional[CustomCommandsManager] = None
        self.rating: Optional[RatingManager] = None
        self.polls: Optional[PollsManager] = None
        self.giveaways: Optional[GiveawaysManager] = None
        self.logs: Optional[LogsManager] = None
        self.backup: Optional[BackupManager] = None
        
        # Состояние бота
        self.bot_status = "active"
        self.maintenance_message = "🤖 Бот находится на техническом обслуживании. Пожалуйста, зайдите позже."
        self.unavailable_message = "⛔ Бот временно недоступен."
        
        AdminSystem._instance = self
    
    @classmethod
    def get_instance(cls):
        """Получить экземпляр AdminSystem"""
        if cls._instance is None:
            raise Exception("AdminSystem не инициализирован. Сначала вызовите AdminSystem(config)")
        return cls._instance
    
    async def setup(self):
        """Настройка системы"""
        if self._is_initialized:
            logger.warning("Система уже инициализирована")
            return
        
        logger.info("Настройка системы администрирования...")
        
        try:
            # Инициализация бота и диспетчера
            await self._init_bot()
            
            # Инициализация базы данных
            await self._init_database()
            
            # Инициализация менеджеров
            await self._init_managers()
            
            # Настройка команд бота
            await self._setup_bot_commands()
            
            # Настройка обработчиков
            await self._setup_handlers()
            
            self._is_initialized = True
            logger.info("✅ Система администрирования успешно настроена")
            
        except Exception as e:
            logger.error(f"❌ Ошибка при настройке системы: {e}")
            raise
    
    async def _init_bot(self):
        """Инициализация бота и диспетчера"""
        # Создание бота
        self.bot = Bot(token=self.config.bot_token, parse_mode="HTML")
        
        # Настройка хранилища состояний
        if self.config.database.use_redis:
            from aiogram.fsm.storage.redis import RedisStorage
            storage = RedisStorage.from_url(self.config.database.redis_url)
        else:
            from aiogram.fsm.storage.memory import MemoryStorage
            storage = MemoryStorage()
        
        # Создание диспетчера
        self.dispatcher = Dispatcher(storage=storage)
        
        # Настройка данных бота
        try:
            bot_info = await self.bot.get_me()
            self.config.bot_username = bot_info.username
            self.config.bot_name = bot_info.first_name
            logger.info(f"🤖 Бот: @{bot_info.username} ({bot_info.first_name})")
        except Exception as e:
            logger.warning(f"Не удалось получить информацию о боте: {e}")
    
    async def _init_database(self):
        """Инициализация базы данных"""
        self.database = DatabaseManager(
            db_path=self.config.database.path,
            prefix=self.config.database.prefix,
            bot_id=0  # Можно использовать ID бота
        )
        
        await self.database.connect()
        
        # Инициализация главных админов
        for admin_id in self.config.main_admins:
            from .models import BotAdmin
            admin = BotAdmin(
                user_id=admin_id,
                level=3,  # Главный админ
                added_by=0,  # Система
                bot_id=0
            )
            await self.database.add_bot_admin(admin)
        
        logger.info(f"✅ База данных инициализирована: {self.config.database.path}")
    
    async def _init_managers(self):
        """Инициализация менеджеров"""
        # Менеджер безопасности
        self.security = SecurityManager(
            config=self.config.security,
            bot_id=0
        )
        
        # Менеджер админ-панели
        self.admin_panel = AdminPanel(self)
        
        # Менеджер пользователей
        self.user_management = UserManagementManager(self)
        
        # Менеджер чатов
        self.chat_management = ChatManagementManager(self)
        
        # Менеджер статистики
        self.statistics = StatisticsManager(self)
        
        # Менеджер рассылок
        self.broadcasting = BroadcastingManager(self)
        
        # Менеджер автомодерации
        self.automoderation = AutoModerationManager(self)
        
        # Менеджер жалоб
        self.reports = ReportsManager(self)
        
        # Менеджер кастомных команд
        self.custom_commands = CustomCommandsManager(self)
        
        # Менеджер рейтинга
        self.rating = RatingManager(self)
        
        # Менеджер опросов
        self.polls = PollsManager(self)
        
        # Менеджер розыгрышей
        self.giveaways = GiveawaysManager(self)
        
        # Менеджер логов
        self.logs = LogsManager(self)
        
        # Менеджер бэкапов
        self.backup = BackupManager(self)
        
        logger.info("✅ Менеджеры инициализированы")
    
    async def _setup_bot_commands(self):
        """Настройка команд бота"""
        commands = [
            BotCommand(command="start", description="Запустить бота"),
            BotCommand(command="help", description="Помощь"),
            BotCommand(command="apanel", description="Админ-панель"),
            BotCommand(command="profile", description="Мой профиль"),
            BotCommand(command="mystats", description="Моя статистика"),
            BotCommand(command="rating", description="Мой рейтинг"),
            BotCommand(command="top", description="Топ пользователей"),
            BotCommand(command="commands", description="Доступные команды"),
        ]
        
        try:
            await self.bot.set_my_commands(commands, scope=BotCommandScopeDefault())
            logger.info("✅ Команды бота настроены")
        except Exception as e:
            logger.error(f"❌ Ошибка при настройке команд: {e}")
    
    async def _setup_handlers(self):
        """Настройка обработчиков"""
        # Добавление роутеров
        if "admin_panel" in self.config.enabled_modules:
            self.dispatcher.include_router(self.admin_panel.router)
        
        if "user_management" in self.config.enabled_modules:
            self.dispatcher.include_router(self.user_management.router)
        
        if "chat_management" in self.config.enabled_modules:
            self.dispatcher.include_router(self.chat_management.router)
            # Настройка обработчиков команд чата
            await self.chat_management.setup_handlers(self.dispatcher)
        
        if "reports" in self.config.enabled_modules:
            await self.reports.setup_handlers(self.dispatcher)
        
        if "custom_commands" in self.config.enabled_modules:
            self.dispatcher.include_router(self.custom_commands.router)
        
        if "rating" in self.config.enabled_modules:
            self.dispatcher.include_router(self.rating.router)
        
        if "polls" in self.config.enabled_modules:
            self.dispatcher.include_router(self.polls.router)
        
        if "giveaways" in self.config.enabled_modules:
            self.dispatcher.include_router(self.giveaways.router)
        
        # Базовые обработчики
        self.dispatcher.message.register(self._handle_start, F.text == "/start")
        self.dispatcher.message.register(self._handle_help, F.text == "/help")
        self.dispatcher.message.register(self._handle_status_check)
        
        logger.info("✅ Обработчики настроены")
    
    async def _handle_start(self, message):
        """Обработка команды /start"""
        user_id = message.from_user.id
        
        # Проверка статуса бота
        if self.bot_status != "active":
            status_message = self.maintenance_message if self.bot_status == "maintenance" else self.unavailable_message
            await message.answer(status_message)
            return
        
        # Регистрация/обновление пользователя
        from .models import User, UserStatus
        
        user = User(
            user_id=user_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            language_code=message.from_user.language_code,
            is_premium=message.from_user.is_premium or False,
            status=UserStatus.ACTIVE
        )
        
        await self.database.add_user(user)
        
        # Приветственное сообщение
        welcome_text = f"👋 Привет, {message.from_user.first_name}!\n\n"
        welcome_text += "Я - бот с расширенной системой администрирования.\n\n"
        
        if await self.security.check_bot_admin(user_id):
            welcome_text += "🔐 У вас есть доступ к админ-панели.\n"
            welcome_text += "Используйте команду /apanel для управления ботом.\n\n"
        
        welcome_text += "📋 Доступные команды:\n"
        welcome_text += "/profile - Мой профиль\n"
        welcome_text += "/mystats - Моя статистика\n"
        welcome_text += "/rating - Мой рейтинг\n"
        welcome_text += "/top - Топ пользователей\n"
        welcome_text += "/help - Помощь"
        
        await message.answer(welcome_text)
        
        # Логирование
        await self.security.log_action(
            user_id=user_id,
            action_type=1,  # USER_REGISTERED
            action_data={"source": "start_command"}
        )
    
    async def _handle_help(self, message):
        """Обработка команды /help"""
        help_text = "📋 Справка по боту\n\n"
        
        help_text += "👤 Основные команды:\n"
        help_text += "/start - Запустить бота\n"
        help_text += "/profile - Мой профиль\n"
        help_text += "/mystats - Моя статистика\n"
        help_text += "/rating - Мой рейтинг\n"
        help_text += "/top - Топ пользователей\n"
        help_text += "/commands - Доступные команды\n\n"
        
        # Команды для чатов
        if message.chat.type != "private":
            help_text += "💬 Команды для чатов:\n"
            help_text += "/rules - Правила чата\n"
            help_text += "/report - Пожаловаться на сообщение\n"
            help_text += "/info - Информация о пользователе\n"
            help_text += "/chatstats - Статистика чата\n"
            help_text += "/admins - Список админов\n\n"
        
        # Команды для админов
        if await self.security.check_bot_admin(message.from_user.id):
            help_text += "🛠️ Команды для админов:\n"
            help_text += "/apanel - Админ-панель\n\n"
        
        help_text += "❓ По вопросам обращайтесь к администраторам."
        
        await message.answer(help_text)
    
    async def _handle_status_check(self, message):
        """Проверка статуса бота перед обработкой сообщения"""
        if self.bot_status != "active" and message.text not in ["/start", "/help"]:
            status_message = self.maintenance_message if self.bot_status == "maintenance" else self.unavailable_message
            await message.answer(status_message)
            return
    
    async def start_background_tasks(self):
        """Запуск фоновых задач"""
        logger.info("Запуск фоновых задач...")
        
        # Задача сбора статистики
        if "statistics" in self.config.enabled_modules:
            task = asyncio.create_task(self._statistics_task())
            self._background_tasks.append(task)
        
        # Задача очистки старых данных
        task = asyncio.create_task(self._cleanup_task())
        self._background_tasks.append(task)
        
        # Задача проверки запланированных рассылок
        if "broadcasting" in self.config.enabled_modules:
            task = asyncio.create_task(self._broadcast_scheduler_task())
            self._background_tasks.append(task)
        
        # Задача снижения рейтинга за неактивность
        if "rating" in self.config.enabled_modules:
            task = asyncio.create_task(self._rating_decay_task())
            self._background_tasks.append(task)
        
        # Задача создания бэкапов
        if "backup" in self.config.enabled_modules:
            task = asyncio.create_task(self._backup_task())
            self._background_tasks.append(task)
        
        logger.info(f"✅ Запущено {len(self._background_tasks)} фоновых задач")
    
    async def _statistics_task(self):
        """Задача сбора статистики"""
        while True:
            try:
                await self.statistics.collect_statistics()
            except Exception as e:
                logger.error(f"Ошибка при сборе статистики: {e}")
            
            # Ожидание перед следующим сбором
            await asyncio.sleep(self.config.statistics.update_interval_minutes * 60)
    
    async def _cleanup_task(self):
        """Задача очистки старых данных"""
        while True:
            try:
                # Очистка старых логов
                await self.database.cleanup_old_data(days_to_keep=90)
                
                # Очистка старых жалоб
                if "reports" in self.config.enabled_modules:
                    await self.reports.cleanup_old_reports(days_to_keep=30)
                
                logger.info("✅ Очистка старых данных выполнена")
            except Exception as e:
                logger.error(f"Ошибка при очистке старых данных: {e}")
            
            # Ожидание 24 часа
            await asyncio.sleep(24 * 60 * 60)
    
    async def _broadcast_scheduler_task(self):
        """Задача планировщика рассылок"""
        while True:
            try:
                # Проверка запланированных рассылок
                db = self.database
                broadcasts, _ = await db.get_broadcasts(status="pending")
                
                now = datetime.now()
                for broadcast in broadcasts:
                    if broadcast.scheduled_for and broadcast.scheduled_for <= now:
                        # Запуск рассылки
                        task = asyncio.create_task(
                            self.broadcasting.send_broadcast(broadcast)
                        )
                        self.broadcasting.active_broadcasts[broadcast.id] = task
                        
                        # Обновление статуса
                        broadcast.status = "sending"
                        await db.update_broadcast(broadcast)
                
            except Exception as e:
                logger.error(f"Ошибка в планировщике рассылок: {e}")
            
            # Проверка каждую минуту
            await asyncio.sleep(60)
    
    async def _rating_decay_task(self):
        """Задача снижения рейтинга за неактивность"""
        while True:
            try:
                await self.rating.apply_rating_decay()
                
                # Сброс дневных лимитов в полночь
                now = datetime.now()
                if now.hour == 0 and now.minute == 0:
                    await self.rating.reset_daily_limits()
                
                # Начисление недельных бонусов в понедельник
                if now.weekday() == 0 and now.hour == 0 and now.minute == 0:
                    await self.rating.award_weekly_bonuses()
                
                # Начисление месячных бонусов в первый день месяца
                if now.day == 1 and now.hour == 0 and now.minute == 0:
                    await self.rating.award_monthly_bonuses()
                    
            except Exception as e:
                logger.error(f"Ошибка в задаче рейтинга: {e}")
            
            # Проверка каждый час
            await asyncio.sleep(60 * 60)
    
    async def _backup_task(self):
        """Задача создания бэкапов"""
        while True:
            try:
                # Создание бэкапа каждый день в 3:00
                now = datetime.now()
                if now.hour == 3 and now.minute == 0:
                    await self.backup.create_automatic_backup()
                    
            except Exception as e:
                logger.error(f"Ошибка при создании бэкапа: {e}")
            
            # Проверка каждые 30 минут
            await asyncio.sleep(30 * 60)
    
    async def get_routers(self):
        """Получить список роутеров системы"""
        routers = []
        
        if "admin_panel" in self.config.enabled_modules:
            routers.append(self.admin_panel.router)
        
        if "user_management" in self.config.enabled_modules:
            routers.append(self.user_management.router)
        
        if "chat_management" in self.config.enabled_modules:
            routers.append(self.chat_management.router)
        
        if "custom_commands" in self.config.enabled_modules:
            routers.append(self.custom_commands.router)
        
        if "rating" in self.config.enabled_modules:
            routers.append(self.rating.router)
        
        if "polls" in self.config.enabled_modules:
            routers.append(self.polls.router)
        
        if "giveaways" in self.config.enabled_modules:
            routers.append(self.giveaways.router)
        
        return routers
    
    async def set_bot_status(self, status: str, message: Optional[str] = None):
        """Установить статус бота"""
        valid_statuses = ["active", "maintenance", "unavailable"]
        
        if status not in valid_statuses:
            raise ValueError(f"Неверный статус. Допустимые значения: {', '.join(valid_statuses)}")
        
        self.bot_status = status
        
        if status == "maintenance" and message:
            self.maintenance_message = message
        elif status == "unavailable" and message:
            self.unavailable_message = message
        
        # Логирование
        await self.security.log_action(
            user_id=0,  # Система
            action_type=9,  # SETTINGS_CHANGED
            action_data={
                "action": "bot_status_changed",
                "new_status": status,
                "message": message
            }
        )
        
        logger.info(f"Статус бота изменен на: {status}")
    
    async def get_system_info(self) -> Dict[str, Any]:
        """Получить информацию о системе"""
        # Статистика из БД
        users, total_users = await self.database.get_users(limit=1)
        chats, total_chats = await self.database.get_chats(limit=1)
        
        # Активные пользователи за 24 часа
        active_cutoff = datetime.now() - timedelta(hours=24)
        active_users, _ = await self.database.get_users(
            filters={"min_last_activity": active_cutoff},
            limit=1
        )
        
        info = {
            "bot": {
                "username": self.config.bot_username,
                "name": self.config.bot_name,
                "status": self.bot_status,
                "language": self.config.default_language,
                "timezone": self.config.timezone
            },
            "statistics": {
                "total_users": total_users,
                "active_users_24h": len(active_users),
                "total_chats": total_chats,
                "enabled_modules": len(self.config.enabled_modules)
            },
            "database": {
                "path": self.config.database.path,
                "prefix": self.config.database.prefix,
                "using_redis": self.config.database.use_redis
            },
            "security": {
                "throttling_enabled": self.config.security.throttling_enabled,
                "main_admins_count": len(self.config.main_admins)
            },
            "system": {
                "version": "2.0.0",
                "initialized": self._is_initialized,
                "background_tasks": len(self._background_tasks)
            }
        }
        
        return info
    
    async def shutdown(self):
        """Корректное завершение работы системы"""
        logger.info("Завершение работы системы...")
        
        # Отмена фоновых задач
        for task in self._background_tasks:
            task.cancel()
        
        # Ожидание завершения задач
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        
        # Закрытие соединения с БД
        if self.database:
            await self.database.close()
        
        # Закрытие сессии бота
        if self.bot:
            await self.bot.session.close()
        
        logger.info("✅ Система завершила работу")
    
    def setup_signal_handlers(self):
        """Настройка обработчиков сигналов"""
        def signal_handler(signum, frame):
            logger.info(f"Получен сигнал {signum}. Завершение работы...")
            asyncio.create_task(self.shutdown())
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)