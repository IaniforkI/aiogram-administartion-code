from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum
import asyncio
from pathlib import Path
import json

class BotStatus(str, Enum):
    """Статусы бота"""
    ACTIVE = "active"
    MAINTENANCE = "maintenance"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"

class AdminLevel(int, Enum):
    """Уровни админов бота"""
    JUNIOR = 1
    SENIOR = 2
    MAIN = 3

class ChatAdminLevel(int, Enum):
    """Уровни админов чата"""
    OBSERVER = 1
    ASSISTANT = 2
    MODERATOR = 3
    SENIOR_MODERATOR = 4
    OWNER = 5

@dataclass
class DatabaseConfig:
    """Конфигурация базы данных"""
    path: str = "bot_admin.db"
    prefix: str = ""
    use_redis: bool = False
    redis_url: str = "redis://localhost:6379"
    cache_ttl: int = 300  # TTL кэша в секундах
    
    def get_table_name(self, base_name: str) -> str:
        """Получить имя таблицы с префиксом"""
        if self.prefix:
            return f"{self.prefix}_{base_name}"
        return base_name

@dataclass
class SecurityConfig:
    """Конфигурация безопасности"""
    throttling_enabled: bool = True
    max_requests_per_minute: int = 30
    max_dangerous_requests_per_hour: int = 10
    require_2fa_for_main_admins: bool = False
    session_timeout_minutes: int = 60
    log_all_actions: bool = True
    
    # Лимиты для разных уровней
    limits: Dict[str, Dict[str, int]] = field(default_factory=lambda: {
        "user": {"per_second": 5, "per_minute": 30},
        "admin_junior": {"per_second": 10, "per_minute": 60},
        "admin_senior": {"per_second": 20, "per_minute": 120},
        "admin_main": {"per_second": 50, "per_minute": 300}
    })

@dataclass
class StatisticsConfig:
    """Конфигурация статистики"""
    update_mode: str = "realtime"  # realtime, periodic, on_demand
    update_interval_minutes: int = 5
    keep_history_days: int = 365
    enable_user_statistics: bool = True
    enable_chat_statistics: bool = True
    enable_activity_graphs: bool = True
    
    # Собираемые метрики
    collect_metrics: List[str] = field(default_factory=lambda: [
        "messages", "commands", "voice_messages", "photos",
        "videos", "documents", "stickers", "active_days",
        "online_time", "reactions"
    ])

@dataclass
class BroadcastingConfig:
    """Конфигурация рассылок"""
    max_messages_per_day: int = 1000
    delay_between_messages_ms: int = 100
    require_confirmation: bool = True
    track_delivery: bool = True
    max_scheduled_messages: int = 50
    
    allowed_formats: List[str] = field(default_factory=lambda: [
        "text", "photo", "video", "document", "poll", "quiz"
    ])

@dataclass 
class AdminConfig:
    """Основная конфигурация системы"""
    # Обязательные параметры
    bot_token: str
    main_admins: List[int]
    
    # Настройки БД
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    
    # Настройки безопасности
    security: SecurityConfig = field(default_factory=SecurityConfig)
    
    # Настройки статистики
    statistics: StatisticsConfig = field(default_factory=StatisticsConfig)
    
    # Настройки рассылок
    broadcasting: BroadcastingConfig = field(default_factory=BroadcastingConfig)
    
    # Дополнительные настройки
    bot_username: str = ""
    bot_name: str = ""
    default_language: str = "ru"
    timezone: str = "Europe/Moscow"
    
    # Включенные модули
    enabled_modules: List[str] = field(default_factory=lambda: [
        "admin_panel", "user_management", "chat_management",
        "statistics", "broadcasting", "automoderation",
        "polls", "giveaways", "reports", "custom_commands",
        "rating", "logs", "backup"
    ])
    
    # Пути
    backup_path: str = "backups"
    logs_path: str = "logs"
    
    def __post_init__(self):
        """Проверка конфигурации после инициализации"""
        if not self.bot_token:
            raise ValueError("Токен бота обязателен")
        
        if not self.main_admins:
            raise ValueError("Необходимо указать хотя бы одного главного админа")
        
        # Создание директорий
        Path(self.backup_path).mkdir(exist_ok=True)
        Path(self.logs_path).mkdir(exist_ok=True)
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертация в словарь"""
        return {
            "bot_username": self.bot_username,
            "bot_name": self.bot_name,
            "main_admins": self.main_admins,
            "default_language": self.default_language,
            "timezone": self.timezone,
            "enabled_modules": self.enabled_modules,
            "database": {
                "path": self.database.path,
                "prefix": self.database.prefix,
                "use_redis": self.database.use_redis
            },
            "security": {
                "throttling_enabled": self.security.throttling_enabled,
                "max_requests_per_minute": self.security.max_requests_per_minute
            }
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AdminConfig':
        """Создание конфигурации из словаря"""
        config = cls(
            bot_token=data.get("bot_token", ""),
            main_admins=data.get("main_admins", [])
        )
        
        # Обновление полей
        for key, value in data.items():
            if hasattr(config, key):
                setattr(config, key, value)
        
        return config
    
    def save_to_file(self, path: str = "admin_config.json"):
        """Сохранение конфигурации в файл"""
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
    
    @classmethod
    def load_from_file(cls, path: str = "admin_config.json") -> 'AdminConfig':
        """Загрузка конфигурации из файла"""
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.from_dict(data)