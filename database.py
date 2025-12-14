import aiosqlite
import asyncio
import json
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timedelta
import logging
from pathlib import Path

from .models import (
    User, Chat, BotAdmin, ChatAdmin, ActionLog,
    Broadcast, CustomCommand, UserStatus, ActionType
)

logger = logging.getLogger(__name__)

class DatabaseManager:
    """Менеджер базы данных с поддержкой миграций"""
    
    _instance = None
    
    def __init__(self, db_path: str = "bot_admin.db", prefix: str = "", bot_id: int = 0):
        self.db_path = db_path
        self.prefix = prefix
        self.bot_id = bot_id
        self.connection: Optional[aiosqlite.Connection] = None
        self.lock = asyncio.Lock()
        
        DatabaseManager._instance = self
    
    @classmethod
    def get_instance(cls):
        """Получить экземпляр DatabaseManager"""
        return cls._instance
    
    async def connect(self):
        """Подключение к базе данных"""
        async with self.lock:
            if self.connection is None:
                self.connection = await aiosqlite.connect(self.db_path)
                self.connection.row_factory = aiosqlite.Row
                await self._initialize_database()
                await self._run_migrations()
    
    async def close(self):
        """Закрытие соединения с БД"""
        async with self.lock:
            if self.connection:
                await self.connection.close()
                self.connection = None
    
    def get_table_name(self, base_name: str) -> str:
        """Получить имя таблицы с префиксом"""
        if self.prefix:
            return f"{self.prefix}_{base_name}"
        return base_name
    
    async def _initialize_database(self):
        """Инициализация структуры БД"""
        tables = {
            "users": """
                CREATE TABLE IF NOT EXISTS {} (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT NOT NULL,
                    last_name TEXT,
                    language_code TEXT DEFAULT 'ru',
                    is_premium BOOLEAN DEFAULT FALSE,
                    email TEXT,
                    phone TEXT,
                    rating INTEGER DEFAULT 0,
                    warnings INTEGER DEFAULT 0,
                    status INTEGER DEFAULT 1,
                    registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    metadata TEXT DEFAULT '{}',
                    bot_id INTEGER DEFAULT 0,
                    UNIQUE(user_id, bot_id)
                )
            """,
            "chats": """
                CREATE TABLE IF NOT EXISTS {} (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    chat_type TEXT DEFAULT 'private',
                    username TEXT,
                    members_count INTEGER DEFAULT 0,
                    owner_id INTEGER,
                    bot_id INTEGER DEFAULT 0,
                    join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    settings TEXT DEFAULT '{}',
                    UNIQUE(chat_id, bot_id)
                )
            """,
            "bot_admins": """
                CREATE TABLE IF NOT EXISTS {} (
                    user_id INTEGER,
                    level INTEGER DEFAULT 1,
                    permissions TEXT DEFAULT '[]',
                    added_by INTEGER,
                    added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    bot_id INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, bot_id)
                )
            """,
            "chat_admins": """
                CREATE TABLE IF NOT EXISTS {} (
                    chat_id INTEGER,
                    user_id INTEGER,
                    level INTEGER DEFAULT 1,
                    permissions TEXT DEFAULT '[]',
                    added_by INTEGER,
                    added_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    bot_id INTEGER DEFAULT 0,
                    PRIMARY KEY (chat_id, user_id, bot_id)
                )
            """,
            "action_logs": """
                CREATE TABLE IF NOT EXISTS {} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    chat_id INTEGER,
                    action_type INTEGER NOT NULL,
                    action_data TEXT DEFAULT '{}',
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    bot_id INTEGER DEFAULT 0
                )
            """,
            "statistics": """
                CREATE TABLE IF NOT EXISTS {} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    metric_name TEXT NOT NULL,
                    metric_value REAL NOT NULL,
                    period_start TIMESTAMP NOT NULL,
                    period_end TIMESTAMP NOT NULL,
                    entity_type TEXT,  # user, chat, global
                    entity_id INTEGER,
                    bot_id INTEGER DEFAULT 0,
                    UNIQUE(metric_name, period_start, entity_type, entity_id, bot_id)
                )
            """,
            "broadcasts": """
                CREATE TABLE IF NOT EXISTS {} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_by INTEGER NOT NULL,
                    target_type TEXT NOT NULL,
                    target_filter TEXT DEFAULT '{}',
                    message_type TEXT NOT NULL,
                    message_data TEXT DEFAULT '{}',
                    status TEXT DEFAULT 'pending',
                    sent_count INTEGER DEFAULT 0,
                    failed_count INTEGER DEFAULT 0,
                    scheduled_for TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    bot_id INTEGER DEFAULT 0
                )
            """,
            "custom_commands": """
                CREATE TABLE IF NOT EXISTS {} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT,
                    command_text TEXT NOT NULL,
                    response_type TEXT DEFAULT 'text',
                    response_data TEXT DEFAULT '{}',
                    buttons TEXT DEFAULT '[]',
                    works_in TEXT DEFAULT 'everywhere',
                    access_level INTEGER DEFAULT 0,
                    usage_count INTEGER DEFAULT 0,
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    valid_from TIMESTAMP,
                    valid_until TIMESTAMP,
                    bot_id INTEGER DEFAULT 0,
                    UNIQUE(name, bot_id)
                )
            """,
            "reports": """
                CREATE TABLE IF NOT EXISTS {} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reporter_id INTEGER NOT NULL,
                    reported_user_id INTEGER NOT NULL,
                    chat_id INTEGER,
                    message_id INTEGER,
                    report_type INTEGER NOT NULL,
                    reason TEXT,
                    status TEXT DEFAULT 'pending',
                    handled_by INTEGER,
                    handled_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    bot_id INTEGER DEFAULT 0
                )
            """,
            "giveaways": """
                CREATE TABLE IF NOT EXISTS {} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT,
                    conditions TEXT DEFAULT '{}',
                    start_date TIMESTAMP NOT NULL,
                    end_date TIMESTAMP NOT NULL,
                    winners_count INTEGER DEFAULT 1,
                    prizes TEXT,
                    status TEXT DEFAULT 'pending',
                    created_by INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    bot_id INTEGER DEFAULT 0
                )
            """,
            "giveaway_participants": """
                CREATE TABLE IF NOT EXISTS {} (
                    giveaway_id INTEGER,
                    user_id INTEGER,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    bot_id INTEGER DEFAULT 0,
                    PRIMARY KEY (giveaway_id, user_id, bot_id)
                )
            """,
            "backups": """
                CREATE TABLE IF NOT EXISTS {} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT,
                    backup_data TEXT NOT NULL,
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    bot_id INTEGER DEFAULT 0
                )
            """,
            "polls": """
                CREATE TABLE IF NOT EXISTS {} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER,
                    message_id INTEGER,
                    question TEXT NOT NULL,
                    options TEXT NOT NULL,  # JSON array
                    is_anonymous BOOLEAN DEFAULT TRUE,
                    allows_multiple_answers BOOLEAN DEFAULT FALSE,
                    created_by INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    closes_at TIMESTAMP,
                    bot_id INTEGER DEFAULT 0
                )
            """,
            "poll_votes": """
                CREATE TABLE IF NOT EXISTS {} (
                    poll_id INTEGER,
                    user_id INTEGER,
                    option_index INTEGER,
                    voted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    bot_id INTEGER DEFAULT 0,
                    PRIMARY KEY (poll_id, user_id, bot_id)
                )
            """
        }
        
        async with self.connection.execute("BEGIN"):
            for table_name, create_sql in tables.items():
                table_name_full = self.get_table_name(table_name)
                await self.connection.execute(create_sql.format(table_name_full))
            
            # Создание индексов для производительности
            indexes = [
                ("idx_users_status", "users", "status"),
                ("idx_users_rating", "users", "rating"),
                ("idx_users_last_activity", "users", "last_activity"),
                ("idx_users_registration", "users", "registration_date"),
                ("idx_chats_type", "chats", "chat_type"),
                ("idx_chats_last_activity", "chats", "last_activity"),
                ("idx_action_logs_user", "action_logs", "user_id"),
                ("idx_action_logs_timestamp", "action_logs", "timestamp"),
                ("idx_action_logs_type", "action_logs", "action_type"),
                ("idx_reports_status", "reports", "status"),
                ("idx_reports_reported", "reports", "reported_user_id"),
                ("idx_broadcasts_status", "broadcasts", "status"),
                ("idx_broadcasts_scheduled", "broadcasts", "scheduled_for"),
            ]
            
            for index_name, table_name, column in indexes:
                table_name_full = self.get_table_name(table_name)
                index_name_full = f"{self.prefix}_{index_name}" if self.prefix else index_name
                await self.connection.execute(
                    f"CREATE INDEX IF NOT EXISTS {index_name_full} "
                    f"ON {table_name_full} ({column})"
                )
            
            await self.connection.commit()
    
    async def _run_migrations(self):
        """Выполнение миграций БД"""
        # Таблица для отслеживания миграций
        await self.connection.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.get_table_name('migrations')} (
                migration_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Получение уже примененных миграций
        cursor = await self.connection.execute(
            f"SELECT migration_id FROM {self.get_table_name('migrations')}"
        )
        applied_migrations = {row[0] for row in await cursor.fetchall()}
        await cursor.close()
        
        # Определение миграций
        migrations = [
            (1, "initial_schema", self._migration_1_initial),
            (2, "add_metadata_fields", self._migration_2_metadata),
            (3, "add_statistics_indexes", self._migration_3_indexes),
            (4, "add_chat_settings", self._migration_4_chat_settings),
        ]
        
        # Применение миграций
        for migration_id, name, migration_func in migrations:
            if migration_id not in applied_migrations:
                try:
                    logger.info(f"Применение миграции {migration_id}: {name}")
                    await migration_func()
                    
                    await self.connection.execute(
                        f"INSERT INTO {self.get_table_name('migrations')} (migration_id, name) VALUES (?, ?)",
                        (migration_id, name)
                    )
                    await self.connection.commit()
                    
                    logger.info(f"Миграция {migration_id} успешно применена")
                except Exception as e:
                    logger.error(f"Ошибка при применении миграции {migration_id}: {e}")
                    await self.connection.rollback()
                    raise
    
    async def _migration_1_initial(self):
        """Первоначальная миграция - уже выполнена в _initialize_database"""
        pass
    
    async def _migration_2_metadata(self):
        """Добавление полей metadata"""
        # Проверяем существование поля metadata в таблице users
        cursor = await self.connection.execute(
            f"PRAGMA table_info({self.get_table_name('users')})"
        )
        columns = [row[1] for row in await cursor.fetchall()]
        await cursor.close()
        
        if 'metadata' not in columns:
            await self.connection.execute(
                f"ALTER TABLE {self.get_table_name('users')} ADD COLUMN metadata TEXT DEFAULT '{{}}'"
            )
    
    async def _migration_3_indexes(self):
        """Добавление дополнительных индексов"""
        indexes = [
            ("idx_custom_commands_name", "custom_commands", "name"),
            ("idx_giveaways_status", "giveaways", "status"),
            ("idx_polls_chat", "polls", "chat_id"),
        ]
        
        for index_name, table_name, column in indexes:
            table_name_full = self.get_table_name(table_name)
            index_name_full = f"{self.prefix}_{index_name}" if self.prefix else index_name
            await self.connection.execute(
                f"CREATE INDEX IF NOT EXISTS {index_name_full} "
                f"ON {table_name_full} ({column})"
            )
    
    async def _migration_4_chat_settings(self):
        """Добавление таблицы настроек чата"""
        # Уже есть в основной схеме
        pass
    
    # === Методы для работы с пользователями ===
    
    async def add_user(self, user: User) -> bool:
        """Добавление пользователя"""
        try:
            await self.connection.execute(
                f"""
                INSERT OR REPLACE INTO {self.get_table_name('users')}
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user.user_id, user.username, user.first_name, user.last_name,
                    user.language_code, int(user.is_premium), user.email, user.phone,
                    user.rating, user.warnings, user.status.value,
                    user.registration_date.isoformat(), user.last_activity.isoformat(),
                    json.dumps(user.metadata, ensure_ascii=False), user.bot_id
                )
            )
            await self.connection.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка при добавлении пользователя: {e}")
            return False
    
    async def get_user(self, user_id: int, bot_id: Optional[int] = None) -> Optional[User]:
        """Получение пользователя"""
        bot_id = bot_id or self.bot_id
        
        cursor = await self.connection.execute(
            f"SELECT * FROM {self.get_table_name('users')} WHERE user_id = ? AND bot_id = ?",
            (user_id, bot_id)
        )
        row = await cursor.fetchone()
        await cursor.close()
        
        if row:
            return User.from_dict(dict(row))
        return None
    
    async def update_user(self, user: User) -> bool:
        """Обновление пользователя"""
        try:
            await self.connection.execute(
                f"""
                UPDATE {self.get_table_name('users')}
                SET username = ?, first_name = ?, last_name = ?, language_code = ?,
                    is_premium = ?, email = ?, phone = ?, rating = ?, warnings = ?,
                    status = ?, last_activity = ?, metadata = ?
                WHERE user_id = ? AND bot_id = ?
                """,
                (
                    user.username, user.first_name, user.last_name, user.language_code,
                    int(user.is_premium), user.email, user.phone, user.rating, user.warnings,
                    user.status.value, user.last_activity.isoformat(),
                    json.dumps(user.metadata, ensure_ascii=False),
                    user.user_id, user.bot_id
                )
            )
            await self.connection.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка при обновлении пользователя: {e}")
            return False
    
    async def get_users(
        self,
        offset: int = 0,
        limit: int = 50,
        filters: Optional[Dict] = None,
        order_by: str = "last_activity DESC",
        bot_id: Optional[int] = None
    ) -> Tuple[List[User], int]:
        """Получение списка пользователей с пагинацией"""
        bot_id = bot_id or self.bot_id
        
        where_clauses = ["bot_id = ?"]
        params = [bot_id]
        
        if filters:
            for key, value in filters.items():
                if key == "status":
                    where_clauses.append("status = ?")
                    params.append(value)
                elif key == "min_rating":
                    where_clauses.append("rating >= ?")
                    params.append(value)
                elif key == "max_rating":
                    where_clauses.append("rating <= ?")
                    params.append(value)
                elif key == "username_like":
                    where_clauses.append("username LIKE ?")
                    params.append(f"%{value}%")
                elif key == "is_blocked":
                    if value:
                        where_clauses.append("status IN (2, 3)")
                    else:
                        where_clauses.append("status = 1")
        
        where_sql = " AND ".join(where_clauses)
        
        # Получение общего количества
        count_cursor = await self.connection.execute(
            f"SELECT COUNT(*) FROM {self.get_table_name('users')} WHERE {where_sql}",
            params
        )
        total = (await count_cursor.fetchone())[0]
        await count_cursor.close()
        
        # Получение данных
        cursor = await self.connection.execute(
            f"""
            SELECT * FROM {self.get_table_name('users')}
            WHERE {where_sql}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset]
        )
        
        users = []
        async for row in cursor:
            users.append(User.from_dict(dict(row)))
        
        await cursor.close()
        return users, total
    
    # === Методы для работы с чатами ===
    
    async def add_chat(self, chat: Chat) -> bool:
        """Добавление чата"""
        try:
            await self.connection.execute(
                f"""
                INSERT OR REPLACE INTO {self.get_table_name('chats')}
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat.chat_id, chat.title, chat.chat_type, chat.username,
                    chat.members_count, chat.owner_id, chat.bot_id,
                    chat.join_date.isoformat(), chat.last_activity.isoformat(),
                    json.dumps(chat.settings, ensure_ascii=False)
                )
            )
            await self.connection.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка при добавлении чата: {e}")
            return False
    
    async def get_chat(self, chat_id: int, bot_id: Optional[int] = None) -> Optional[Chat]:
        """Получение чата"""
        bot_id = bot_id or self.bot_id
        
        cursor = await self.connection.execute(
            f"SELECT * FROM {self.get_table_name('chats')} WHERE chat_id = ? AND bot_id = ?",
            (chat_id, bot_id)
        )
        row = await cursor.fetchone()
        await cursor.close()
        
        if row:
            return Chat.from_dict(dict(row))
        return None
    
    async def get_chats(
        self,
        offset: int = 0,
        limit: int = 50,
        chat_type: Optional[str] = None,
        bot_id: Optional[int] = None
    ) -> Tuple[List[Chat], int]:
        """Получение списка чатов"""
        bot_id = bot_id or self.bot_id
        
        where_clauses = ["bot_id = ?"]
        params = [bot_id]
        
        if chat_type:
            where_clauses.append("chat_type = ?")
            params.append(chat_type)
        
        where_sql = " AND ".join(where_clauses)
        
        # Получение общего количества
        count_cursor = await self.connection.execute(
            f"SELECT COUNT(*) FROM {self.get_table_name('chats')} WHERE {where_sql}",
            params
        )
        total = (await count_cursor.fetchone())[0]
        await count_cursor.close()
        
        # Получение данных
        cursor = await self.connection.execute(
            f"""
            SELECT * FROM {self.get_table_name('chats')}
            WHERE {where_sql}
            ORDER BY last_activity DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset]
        )
        
        chats = []
        async for row in cursor:
            chats.append(Chat.from_dict(dict(row)))
        
        await cursor.close()
        return chats, total
    
    # === Методы для работы с админами ===
    
    async def add_bot_admin(self, admin: BotAdmin) -> bool:
        """Добавление админа бота"""
        try:
            await self.connection.execute(
                f"""
                INSERT OR REPLACE INTO {self.get_table_name('bot_admins')}
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    admin.user_id, admin.level, json.dumps(admin.permissions, ensure_ascii=False),
                    admin.added_by, admin.added_date.isoformat(), admin.bot_id
                )
            )
            await self.connection.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка при добавлении админа бота: {e}")
            return False
    
    async def get_bot_admin(self, user_id: int, bot_id: Optional[int] = None) -> Optional[BotAdmin]:
        """Получение админа бота"""
        bot_id = bot_id or self.bot_id
        
        cursor = await self.connection.execute(
            f"SELECT * FROM {self.get_table_name('bot_admins')} WHERE user_id = ? AND bot_id = ?",
            (user_id, bot_id)
        )
        row = await cursor.fetchone()
        await cursor.close()
        
        if row:
            return BotAdmin.from_dict(dict(row))
        return None
    
    async def get_bot_admins(
        self,
        level: Optional[int] = None,
        bot_id: Optional[int] = None
    ) -> List[BotAdmin]:
        """Получение списка админов бота"""
        bot_id = bot_id or self.bot_id
        
        where_clauses = ["bot_id = ?"]
        params = [bot_id]
        
        if level is not None:
            where_clauses.append("level = ?")
            params.append(level)
        
        where_sql = " AND ".join(where_clauses)
        
        cursor = await self.connection.execute(
            f"SELECT * FROM {self.get_table_name('bot_admins')} WHERE {where_sql}",
            params
        )
        
        admins = []
        async for row in cursor:
            admins.append(BotAdmin.from_dict(dict(row)))
        
        await cursor.close()
        return admins
    
    async def add_chat_admin(self, admin: ChatAdmin) -> bool:
        """Добавление админа чата"""
        try:
            expires_at = admin.expires_at.isoformat() if admin.expires_at else None
            
            await self.connection.execute(
                f"""
                INSERT OR REPLACE INTO {self.get_table_name('chat_admins')}
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    admin.chat_id, admin.user_id, admin.level,
                    json.dumps(admin.permissions, ensure_ascii=False),
                    admin.added_by, admin.added_date.isoformat(),
                    expires_at, admin.bot_id
                )
            )
            await self.connection.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка при добавлении админа чата: {e}")
            return False
    
    async def get_chat_admin(
        self,
        chat_id: int,
        user_id: int,
        bot_id: Optional[int] = None
    ) -> Optional[ChatAdmin]:
        """Получение админа чата"""
        bot_id = bot_id or self.bot_id
        
        cursor = await self.connection.execute(
            f"""
            SELECT * FROM {self.get_table_name('chat_admins')}
            WHERE chat_id = ? AND user_id = ? AND bot_id = ?
            """,
            (chat_id, user_id, bot_id)
        )
        row = await cursor.fetchone()
        await cursor.close()
        
        if row:
            return ChatAdmin.from_dict(dict(row))
        return None
    
    async def get_chat_admins(
        self,
        chat_id: int,
        min_level: Optional[int] = None,
        bot_id: Optional[int] = None
    ) -> List[ChatAdmin]:
        """Получение списка админов чата"""
        bot_id = bot_id or self.bot_id
        
        where_clauses = ["chat_id = ?", "bot_id = ?"]
        params = [chat_id, bot_id]
        
        if min_level is not None:
            where_clauses.append("level >= ?")
            params.append(min_level)
        
        where_sql = " AND ".join(where_clauses)
        
        cursor = await self.connection.execute(
            f"SELECT * FROM {self.get_table_name('chat_admins')} WHERE {where_sql}",
            params
        )
        
        admins = []
        async for row in cursor:
            admins.append(ChatAdmin.from_dict(dict(row)))
        
        await cursor.close()
        return admins
    
    async def remove_chat_admin(
        self,
        chat_id: int,
        user_id: int,
        bot_id: Optional[int] = None
    ) -> bool:
        """Удаление админа чата"""
        bot_id = bot_id or self.bot_id
        
        try:
            await self.connection.execute(
                f"""
                DELETE FROM {self.get_table_name('chat_admins')}
                WHERE chat_id = ? AND user_id = ? AND bot_id = ?
                """,
                (chat_id, user_id, bot_id)
            )
            await self.connection.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка при удалении админа чата: {e}")
            return False
    
    # === Методы для работы с логами ===
    
    async def add_action_log(self, log: ActionLog):
        """Добавление лога действия"""
        try:
            await self.connection.execute(
                f"""
                INSERT INTO {self.get_table_name('action_logs')}
                (user_id, chat_id, action_type, action_data, timestamp, bot_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    log.user_id, log.chat_id, log.action_type.value,
                    json.dumps(log.action_data, ensure_ascii=False),
                    log.timestamp.isoformat(), log.bot_id
                )
            )
            await self.connection.commit()
        except Exception as e:
            logger.error(f"Ошибка при добавлении лога: {e}")
    
    async def get_action_logs(
        self,
        user_id: Optional[int] = None,
        chat_id: Optional[int] = None,
        action_type: Optional[ActionType] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        offset: int = 0,
        limit: int = 100,
        bot_id: Optional[int] = None
    ) -> Tuple[List[ActionLog], int]:
        """Получение логов действий"""
        bot_id = bot_id or self.bot_id
        
        where_clauses = ["bot_id = ?"]
        params = [bot_id]
        
        if user_id is not None:
            where_clauses.append("user_id = ?")
            params.append(user_id)
        
        if chat_id is not None:
            where_clauses.append("chat_id = ?")
            params.append(chat_id)
        
        if action_type is not None:
            where_clauses.append("action_type = ?")
            params.append(action_type.value)
        
        if start_date:
            where_clauses.append("timestamp >= ?")
            params.append(start_date.isoformat())
        
        if end_date:
            where_clauses.append("timestamp <= ?")
            params.append(end_date.isoformat())
        
        where_sql = " AND ".join(where_clauses)
        
        # Получение общего количества
        count_cursor = await self.connection.execute(
            f"SELECT COUNT(*) FROM {self.get_table_name('action_logs')} WHERE {where_sql}",
            params
        )
        total = (await count_cursor.fetchone())[0]
        await count_cursor.close()
        
        # Получение данных
        cursor = await self.connection.execute(
            f"""
            SELECT * FROM {self.get_table_name('action_logs')}
            WHERE {where_sql}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset]
        )
        
        logs = []
        async for row in cursor:
            logs.append(ActionLog.from_dict(dict(row)))
        
        await cursor.close()
        return logs, total
    
    # === Методы для работы с рассылками ===
    
    async def add_broadcast(self, broadcast: Broadcast) -> int:
        """Добавление рассылки"""
        try:
            cursor = await self.connection.execute(
                f"""
                INSERT INTO {self.get_table_name('broadcasts')}
                (created_by, target_type, target_filter, message_type, message_data,
                 status, sent_count, failed_count, scheduled_for, created_at,
                 started_at, completed_at, bot_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    broadcast.created_by,
                    broadcast.target_type,
                    json.dumps(broadcast.target_filter, ensure_ascii=False),
                    broadcast.message_type,
                    json.dumps(broadcast.message_data, ensure_ascii=False),
                    broadcast.status,
                    broadcast.sent_count,
                    broadcast.failed_count,
                    broadcast.scheduled_for.isoformat() if broadcast.scheduled_for else None,
                    broadcast.created_at.isoformat(),
                    broadcast.started_at.isoformat() if broadcast.started_at else None,
                    broadcast.completed_at.isoformat() if broadcast.completed_at else None,
                    broadcast.bot_id
                )
            )
            await self.connection.commit()
            
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"Ошибка при добавлении рассылки: {e}")
            return -1
    
    async def update_broadcast(self, broadcast: Broadcast) -> bool:
        """Обновление рассылки"""
        try:
            await self.connection.execute(
                f"""
                UPDATE {self.get_table_name('broadcasts')}
                SET status = ?, sent_count = ?, failed_count = ?,
                    started_at = ?, completed_at = ?
                WHERE id = ? AND bot_id = ?
                """,
                (
                    broadcast.status,
                    broadcast.sent_count,
                    broadcast.failed_count,
                    broadcast.started_at.isoformat() if broadcast.started_at else None,
                    broadcast.completed_at.isoformat() if broadcast.completed_at else None,
                    broadcast.id,
                    broadcast.bot_id
                )
            )
            await self.connection.commit()
            return True
        except Exception as e:
            logger.error(f"Ошибка при обновлении рассылки: {e}")
            return False
    
    async def get_broadcasts(
        self,
        status: Optional[str] = None,
        offset: int = 0,
        limit: int = 50,
        bot_id: Optional[int] = None
    ) -> Tuple[List[Broadcast], int]:
        """Получение списка рассылок"""
        bot_id = bot_id or self.bot_id
        
        where_clauses = ["bot_id = ?"]
        params = [bot_id]
        
        if status:
            where_clauses.append("status = ?")
            params.append(status)
        
        where_sql = " AND ".join(where_clauses)
        
        # Получение общего количества
        count_cursor = await self.connection.execute(
            f"SELECT COUNT(*) FROM {self.get_table_name('broadcasts')} WHERE {where_sql}",
            params
        )
        total = (await count_cursor.fetchone())[0]
        await count_cursor.close()
        
        # Получение данных
        cursor = await self.connection.execute(
            f"""
            SELECT * FROM {self.get_table_name('broadcasts')}
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset]
        )
        
        broadcasts = []
        async for row in cursor:
            broadcasts.append(Broadcast.from_dict(dict(row)))
        
        await cursor.close()
        return broadcasts, total
    
    # === Методы для работы с кастомными командами ===
    
    async def add_custom_command(self, command: CustomCommand) -> int:
        """Добавление кастомной команды"""
        try:
            cursor = await self.connection.execute(
                f"""
                INSERT INTO {self.get_table_name('custom_commands')}
                (name, description, command_text, response_type, response_data,
                 buttons, works_in, access_level, usage_count, created_by,
                 created_at, valid_from, valid_until, bot_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command.name,
                    command.description,
                    command.command_text,
                    command.response_type,
                    json.dumps(command.response_data, ensure_ascii=False),
                    json.dumps(command.buttons, ensure_ascii=False),
                    command.works_in,
                    command.access_level,
                    command.usage_count,
                    command.created_by,
                    command.created_at.isoformat(),
                    command.valid_from.isoformat() if command.valid_from else None,
                    command.valid_until.isoformat() if command.valid_until else None,
                    command.bot_id
                )
            )
            await self.connection.commit()
            
            return cursor.lastrowid
        except Exception as e:
            logger.error(f"Ошибка при добавлении команды: {e}")
            return -1
    
    async def get_custom_command(self, name: str, bot_id: Optional[int] = None) -> Optional[CustomCommand]:
        """Получение кастомной команды"""
        bot_id = bot_id or self.bot_id
        
        cursor = await self.connection.execute(
            f"SELECT * FROM {self.get_table_name('custom_commands')} WHERE name = ? AND bot_id = ?",
            (name, bot_id)
        )
        row = await cursor.fetchone()
        await cursor.close()
        
        if row:
            return CustomCommand.from_dict(dict(row))
        return None
    
    async def get_custom_commands(
        self,
        works_in: Optional[str] = None,
        access_level: Optional[int] = None,
        valid_only: bool = True,
        offset: int = 0,
        limit: int = 50,
        bot_id: Optional[int] = None
    ) -> Tuple[List[CustomCommand], int]:
        """Получение списка кастомных команд"""
        bot_id = bot_id or self.bot_id
        
        where_clauses = ["bot_id = ?"]
        params = [bot_id]
        
        if works_in:
            where_clauses.append("works_in = ?")
            params.append(works_in)
        
        if access_level is not None:
            where_clauses.append("access_level <= ?")
            params.append(access_level)
        
        if valid_only:
            now = datetime.now().isoformat()
            where_clauses.append("(valid_from IS NULL OR valid_from <= ?)")
            params.append(now)
            where_clauses.append("(valid_until IS NULL OR valid_until >= ?)")
            params.append(now)
        
        where_sql = " AND ".join(where_clauses)
        
        # Получение общего количества
        count_cursor = await self.connection.execute(
            f"SELECT COUNT(*) FROM {self.get_table_name('custom_commands')} WHERE {where_sql}",
            params
        )
        total = (await count_cursor.fetchone())[0]
        await count_cursor.close()
        
        # Получение данных
        cursor = await self.connection.execute(
            f"""
            SELECT * FROM {self.get_table_name('custom_commands')}
            WHERE {where_sql}
            ORDER BY name ASC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset]
        )
        
        commands = []
        async for row in cursor:
            commands.append(CustomCommand.from_dict(dict(row)))
        
        await cursor.close()
        return commands, total
    
    async def increment_command_usage(self, command_id: int):
        """Увеличение счетчика использования команды"""
        try:
            await self.connection.execute(
                f"""
                UPDATE {self.get_table_name('custom_commands')}
                SET usage_count = usage_count + 1
                WHERE id = ?
                """,
                (command_id,)
            )
            await self.connection.commit()
        except Exception as e:
            logger.error(f"Ошибка при обновлении счетчика команды: {e}")
    
    # === Методы для работы со статистикой ===
    
    async def add_statistic(
        self,
        metric_name: str,
        metric_value: float,
        period_start: datetime,
        period_end: datetime,
        entity_type: Optional[str] = None,
        entity_id: Optional[int] = None,
        bot_id: Optional[int] = None
    ):
        """Добавление статистики"""
        bot_id = bot_id or self.bot_id
        
        try:
            await self.connection.execute(
                f"""
                INSERT OR REPLACE INTO {self.get_table_name('statistics')}
                (metric_name, metric_value, period_start, period_end,
                 entity_type, entity_id, bot_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    metric_name, metric_value,
                    period_start.isoformat(), period_end.isoformat(),
                    entity_type, entity_id, bot_id
                )
            )
            await self.connection.commit()
        except Exception as e:
            logger.error(f"Ошибка при добавлении статистики: {e}")
    
    async def get_statistics(
        self,
        metric_name: str,
        period_start: datetime,
        period_end: datetime,
        entity_type: Optional[str] = None,
        entity_id: Optional[int] = None,
        bot_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Получение статистики"""
        bot_id = bot_id or self.bot_id
        
        where_clauses = [
            "metric_name = ?",
            "period_start >= ?",
            "period_end <= ?",
            "bot_id = ?"
        ]
        params = [metric_name, period_start.isoformat(), period_end.isoformat(), bot_id]
        
        if entity_type:
            where_clauses.append("entity_type = ?")
            params.append(entity_type)
        
        if entity_id is not None:
            where_clauses.append("entity_id = ?")
            params.append(entity_id)
        
        where_sql = " AND ".join(where_clauses)
        
        cursor = await self.connection.execute(
            f"""
            SELECT * FROM {self.get_table_name('statistics')}
            WHERE {where_sql}
            ORDER BY period_start ASC
            """,
            params
        )
        
        stats = []
        async for row in cursor:
            stats.append({
                "period_start": datetime.fromisoformat(row["period_start"]),
                "period_end": datetime.fromisoformat(row["period_end"]),
                "value": row["metric_value"],
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"]
            })
        
        await cursor.close()
        return stats
    
    # === Методы для очистки старых данных ===
    
    async def cleanup_old_data(self, days_to_keep: int = 90):
        """Очистка старых данных"""
        try:
            cutoff_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            cutoff_date = cutoff_date - timedelta(days=days_to_keep)
            
            # Очистка старых логов
            await self.connection.execute(
                f"""
                DELETE FROM {self.get_table_name('action_logs')}
                WHERE timestamp < ?
                """,
                (cutoff_date.isoformat(),)
            )
            
            # Очистка старых статистик (оставляем только агрегированные данные)
            await self.connection.execute(
                f"""
                DELETE FROM {self.get_table_name('statistics')}
                WHERE period_end < ? AND entity_type IS NOT NULL
                """,
                (cutoff_date.isoformat(),)
            )
            
            await self.connection.commit()
            logger.info(f"Очищены данные старше {days_to_keep} дней")
        except Exception as e:
            logger.error(f"Ошибка при очистке старых данных: {e}")
    
    # === Методы для экспорта/импорта ===
    
    async def export_data(self, table_name: str) -> List[Dict[str, Any]]:
        """Экспорт данных из таблицы"""
        table_name_full = self.get_table_name(table_name)
        
        cursor = await self.connection.execute(f"SELECT * FROM {table_name_full}")
        
        data = []
        async for row in cursor:
            data.append(dict(row))
        
        await cursor.close()
        return data
    
    async def import_data(self, table_name: str, data: List[Dict[str, Any]]):
        """Импорт данных в таблицу"""
        if not data:
            return
        
        table_name_full = self.get_table_name(table_name)
        
        # Получение информации о таблице
        cursor = await self.connection.execute(f"PRAGMA table_info({table_name_full})")
        columns_info = await cursor.fetchall()
        await cursor.close()
        
        columns = [info[1] for info in columns_info]
        placeholders = ", ".join(["?" for _ in columns])
        
        try:
            await self.connection.execute("BEGIN")
            
            for row in data:
                # Подготовка значений в правильном порядке
                values = [row.get(col) for col in columns]
                await self.connection.execute(
                    f"INSERT OR REPLACE INTO {table_name_full} VALUES ({placeholders})",
                    values
                )
            
            await self.connection.commit()
        except Exception as e:
            await self.connection.rollback()
            logger.error(f"Ошибка при импорте данных: {e}")
            raise
    
    # === Методы для выполнения произвольных запросов ===
    
    async def execute_query(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """Выполнение произвольного запроса"""
        try:
            cursor = await self.connection.execute(query, params)
            
            results = []
            async for row in cursor:
                results.append(dict(row))
            
            await cursor.close()
            return results
        except Exception as e:
            logger.error(f"Ошибка при выполнении запроса: {e}")
            return []
    
    async def execute_update(self, query: str, params: tuple = ()) -> int:
        """Выполнение запроса на обновление"""
        try:
            cursor = await self.connection.execute(query, params)
            await self.connection.commit()
            return cursor.rowcount
        except Exception as e:
            logger.error(f"Ошибка при выполнении обновления: {e}")
            return 0