"""
Aiogram Admin System - Полная система администрирования для Telegram ботов
Версия: 2.0.0
"""

from .admin_system import AdminSystem, AdminConfig
from .database import DatabaseManager
from .models import *
from .security import SecurityManager
from .admin_panel import AdminPanel

__version__ = "2.0.0"
__author__ = "Aiogram Admin System"
__all__ = [
    'AdminSystem',
    'AdminConfig',
    'DatabaseManager',
    'SecurityManager',
    'AdminPanel'
]

# Информация о системе
SYSTEM_INFO = {
    'name': 'Aiogram Admin System',
    'version': __version__,
    'description': 'Полная система администрирования для Telegram ботов',
    'requirements': [
        'aiogram>=3.0.0',
        'aiosqlite>=0.19.0',
        'redis>=4.5.0 (опционально)',
        'pandas>=1.5.0 (для статистики)',
        'matplotlib>=3.6.0 (для графиков)'
    ],
    'features': [
        'Многоуровневая система прав',
        'Управление пользователями и чатами',
        'Расширенная статистика и аналитика',
        'Система рассылок',
        'Автомодерация',
        'Кастомные команды',
        'Рейтинговая система',
        'Логирование и аудит',
        'Резервное копирование'
    ]
}