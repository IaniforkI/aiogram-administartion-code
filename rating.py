import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, CommandObject

from .models import User, ActionType
from .ui import create_keyboard, create_pagination_keyboard
from .database import DatabaseManager

logger = logging.getLogger(__name__)

class RatingAction(Enum):
    """Действия, за которые начисляется рейтинг"""
    MESSAGE_SENT = 1
    ACTIVE_DAY = 2
    HELPED_USER = 3
    CREATED_CONTENT = 4
    PARTICIPATED_POLL = 5
    INVITED_USER = 6
    NO_VIOLATIONS_WEEK = 7
    PREMIUM_SUBSCRIBED = 8

class RatingManager:
    """Менеджер системы рейтинга"""
    
    def __init__(self, admin_system):
        self.admin_system = admin_system
        self.bot = admin_system.bot
        self.router = Router()
        
        # Настройки системы рейтинга
        self.settings = {
            "enabled": True,
            "points": {
                RatingAction.MESSAGE_SENT.value: 1,      # За сообщение
                RatingAction.ACTIVE_DAY.value: 10,       # За активный день
                RatingAction.HELPED_USER.value: 50,      # За помощь пользователю
                RatingAction.CREATED_CONTENT.value: 30,  # За создание контента
                RatingAction.PARTICIPATED_POLL.value: 5, # За участие в опросе
                RatingAction.INVITED_USER.value: 100,    # За приглашенного пользователя
                RatingAction.NO_VIOLATIONS_WEEK.value: 50, # За неделю без нарушений
                RatingAction.PREMIUM_SUBSCRIBED.value: 200 # За премиум подписку
            },
            "daily_limit": 100,  # Максимум в день
            "weekly_bonus": 100, # Бонус за активность недели
            "monthly_bonus": 500, # Бонус за активность месяца
            "decay_enabled": True,  # Снижение рейтинга за неактивность
            "decay_days": 30,       # Через сколько дней начинается снижение
            "decay_amount": 1       # На сколько снижать в день
        }
        
        # Кэш для быстрого доступа
        self._user_rating_cache: Dict[int, int] = {}
        self._top_cache: Dict[str, List[Tuple[int, str, int]]] = {}
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl = 300  # 5 минут
        
        self.setup_handlers()
        
    def setup_handlers(self):
        """Настройка обработчиков"""
        
        # Команда /rating
        @self.router.message(Command("rating"))
        async def show_rating(message: Message):
            """Показать рейтинг"""
            await self.handle_rating_command(message)
        
        # Команда /top
        @self.router.message(Command("top"))
        async def show_top(message: Message, command: CommandObject):
            """Показать топ пользователей"""
            await self.handle_top_command(message, command)
        
        # Команда /leaderboard
        @self.router.message(Command("leaderboard"))
        async def show_leaderboard(message: Message):
            """Показать таблицу лидеров"""
            await self.handle_leaderboard_command(message)
    
    async def handle_rating_command(self, message: Message):
        """Обработка команды /rating"""
        user_id = message.from_user.id
        
        # Получение рейтинга пользователя
        rating = await self.get_user_rating(user_id)
        
        # Получение позиции в топе
        position = await self.get_user_position(user_id)
        
        # Получение статистики
        stats = await self.get_user_rating_stats(user_id)
        
        text = f"⭐ Ваш рейтинг\n\n"
        text += f"📊 Текущий рейтинг: {rating:,} очков\n"
        text += f"🏆 Позиция в топе: {position}\n\n"
        
        text += "📈 Статистика:\n"
        text += f"• За сегодня: +{stats.get('today', 0):,}\n"
        text += f"• За неделю: +{stats.get('week', 0):,}\n"
        text += f"• За месяц: +{stats.get('month', 0):,}\n"
        text += f"• Всего заработано: {stats.get('total', 0):,}\n\n"
        
        # Достижения
        achievements = await self.get_user_achievements(user_id)
        if achievements:
            text += "🏅 Достижения:\n"
            for achievement in achievements[:3]:  # Показываем 3 достижения
                text += f"• {achievement}\n"
        
        # Следующий уровень
        next_level = await self.get_next_level_info(rating)
        if next_level:
            text += f"\n📊 До следующего уровня: {next_level['points_needed']:,} очков"
        
        await message.answer(text)
    
    async def handle_top_command(self, message: Message, command: CommandObject):
        """Обработка команды /top"""
        # Определение типа топа
        top_type = "rating"  # По умолчанию по рейтингу
        
        if command.args:
            args = command.args.lower()
            if "неделя" in args or "week" in args:
                top_type = "week"
            elif "месяц" in args or "month" in args:
                top_type = "month"
            elif "день" in args or "day" in args or "сегодня" in args:
                top_type = "today"
            elif "все" in args or "all" in args:
                top_type = "all"
        
        # Получение топа
        top = await self.get_top_users(top_type, limit=10)
        
        if not top:
            await message.answer("🏆 Топ пользователей пуст.")
            return
        
        # Формирование текста
        top_type_text = {
            "rating": "🏆 Топ по рейтингу",
            "today": "🏆 Топ за сегодня",
            "week": "🏆 Топ за неделю",
            "month": "🏆 Топ за месяц",
            "all": "🏆 Общий топ"
        }.get(top_type, "🏆 Топ")
        
        text = f"{top_type_text}\n\n"
        
        for i, (user_id, user_name, points) in enumerate(top, 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
            text += f"{medal} {user_name} - {points:,} очков\n"
        
        # Добавление позиции текущего пользователя
        if message.chat.type == "private":
            user_id = message.from_user.id
            position = await self.get_user_position(user_id, top_type)
            user_rating = await self.get_user_rating(user_id, top_type)
            
            if position > 10:  # Если не в топ-10
                text += f"\n...\n"
                text += f"{position}. Вы - {user_rating:,} очков"
        
        await message.answer(text)
    
    async def handle_leaderboard_command(self, message: Message):
        """Обработка команды /leaderboard"""
        # Создание интерактивной таблицы лидеров
        text = "📊 Таблица лидеров\n\n"
        text += "Выберите категорию:"
        
        buttons = [
            ("⭐ Общий рейтинг", "leaderboard_rating"),
            ("📅 За месяц", "leaderboard_month"),
            ("📆 За неделю", "leaderboard_week"),
            ("☀️ За сегодня", "leaderboard_today"),
            ("👥 По чатам", "leaderboard_chats"),
            ("❌ Закрыть", "leaderboard_close")
        ]
        
        keyboard = create_keyboard(buttons, columns=2)
        
        await message.answer(text, reply_markup=keyboard)
    
    async def add_rating_points(self, user_id: int, action: RatingAction, amount: Optional[int] = None, 
                               details: Optional[Dict] = None) -> int:
        """Добавить очки рейтинга пользователю"""
        if not self.settings["enabled"]:
            return 0
        
        # Получение текущего рейтинга
        current_rating = await self.get_user_rating(user_id)
        
        # Определение количества очков
        if amount is None:
            amount = self.settings["points"].get(action.value, 0)
        
        if amount <= 0:
            return current_rating
        
        # Проверка дневного лимита
        daily_points = await self.get_user_daily_points(user_id)
        if daily_points + amount > self.settings["daily_limit"]:
            amount = max(0, self.settings["daily_limit"] - daily_points)
        
        if amount <= 0:
            return current_rating
        
        # Обновление рейтинга в БД
        db = DatabaseManager.get_instance()
        
        # Получение пользователя
        user = await db.get_user(user_id)
        if not user:
            # Создание записи пользователя
            from .models import UserStatus
            user = User(
                user_id=user_id,
                first_name="Пользователь",
                status=UserStatus.ACTIVE
            )
            await db.add_user(user)
        
        # Обновление рейтинга
        user.rating += amount
        await db.update_user(user)
        
        # Обновление кэша
        self._user_rating_cache[user_id] = user.rating
        self._top_cache.clear()  # Сбрасываем кэш топа
        
        # Логирование действия
        security = self.admin_system.security
        await security.log_action(
            user_id=user_id,
            action_type=8,  # COMMAND_USED
            action_data={
                "action": "rating_added",
                "rating_action": action.value,
                "amount": amount,
                "new_rating": user.rating,
                "details": details
            }
        )
        
        # Проверка достижений
        await self.check_achievements(user_id, user.rating)
        
        return user.rating
    
    async def remove_rating_points(self, user_id: int, amount: int, reason: str = "") -> int:
        """Удалить очки рейтинга у пользователя"""
        if amount <= 0:
            return await self.get_user_rating(user_id)
        
        db = DatabaseManager.get_instance()
        
        # Получение пользователя
        user = await db.get_user(user_id)
        if not user:
            return 0
        
        # Уменьшение рейтинга
        user.rating = max(0, user.rating - amount)
        await db.update_user(user)
        
        # Обновление кэша
        self._user_rating_cache[user_id] = user.rating
        self._top_cache.clear()
        
        # Логирование
        if reason:
            security = self.admin_system.security
            await security.log_action(
                user_id=user_id,
                action_type=8,  # COMMAND_USED
                action_data={
                    "action": "rating_removed",
                    "amount": amount,
                    "reason": reason,
                    "new_rating": user.rating
                }
            )
        
        return user.rating
    
    async def get_user_rating(self, user_id: int, period: str = "all") -> int:
        """Получить рейтинг пользователя"""
        # Проверка кэша
        if period == "all" and user_id in self._user_rating_cache:
            return self._user_rating_cache[user_id]
        
        db = DatabaseManager.get_instance()
        
        if period == "all":
            # Общий рейтинг
            user = await db.get_user(user_id)
            rating = user.rating if user else 0
            
            # Обновление кэша
            self._user_rating_cache[user_id] = rating
            
            return rating
        
        else:
            # Рейтинг за период
            start_date = self._get_period_start(period)
            if not start_date:
                return 0
            
            # Здесь нужно получить рейтинг за период из статистики
            # Для простоты возвращаем 0
            return 0
    
    async def get_user_position(self, user_id: int, period: str = "all") -> int:
        """Получить позицию пользователя в топе"""
        top = await self.get_top_users(period, limit=1000)
        
        for i, (top_user_id, _, _) in enumerate(top, 1):
            if top_user_id == user_id:
                return i
        
        return len(top) + 1
    
    async def get_user_rating_stats(self, user_id: int) -> Dict[str, int]:
        """Получить статистику рейтинга пользователя"""
        # Здесь нужно получить статистику из БД
        # Для примера возвращаем фиктивные данные
        return {
            "today": 50,
            "week": 350,
            "month": 1200,
            "total": await self.get_user_rating(user_id)
        }
    
    async def get_user_daily_points(self, user_id: int) -> int:
        """Получить количество очков, заработанных сегодня"""
        # Здесь нужно посчитать очки за сегодня из логов
        # Для примеры возвращаем 0
        return 0
    
    async def get_top_users(self, period: str = "all", limit: int = 10) -> List[Tuple[int, str, int]]:
        """Получить топ пользователей"""
        cache_key = f"{period}_{limit}"
        
        # Проверка кэша
        if cache_key in self._top_cache:
            return self._top_cache[cache_key]
        
        db = DatabaseManager.get_instance()
        
        if period == "all":
            # Общий топ по рейтингу
            users, _ = await db.get_users(
                limit=limit,
                order_by="rating DESC"
            )
            
            top = []
            for user in users:
                top.append((user.user_id, user.full_name, user.rating))
            
            # Кэширование
            self._top_cache[cache_key] = top
            
            return top
        
        else:
            # Топ за период
            # Здесь нужно реализовать подсчет за период
            # Для простоты возвращаем общий топ
            return await self.get_top_users("all", limit)
    
    async def check_achievements(self, user_id: int, current_rating: int):
        """Проверить достижения пользователя"""
        achievements = []
        
        # Проверка уровней
        levels = [
            (100, "Новичок 🥉"),
            (500, "Активный участник 🥈"),
            (1000, "Опытный пользователь 🥇"),
            (5000, "Ветеран 👑"),
            (10000, "Легенда 💎"),
            (50000, "Бог рейтинга ⭐")
        ]
        
        for required_rating, achievement_name in levels:
            if current_rating >= required_rating:
                # Проверяем, было ли уже это достижение
                if not await self.has_achievement(user_id, achievement_name):
                    achievements.append(achievement_name)
                    await self.grant_achievement(user_id, achievement_name)
        
        # Уведомление о новых достижениях
        if achievements:
            await self.notify_about_achievements(user_id, achievements)
    
    async def has_achievement(self, user_id: int, achievement_name: str) -> bool:
        """Проверить, есть ли у пользователя достижение"""
        # Здесь нужно проверять в БД
        # Для простоты возвращаем False
        return False
    
    async def grant_achievement(self, user_id: int, achievement_name: str):
        """Выдать достижение пользователю"""
        # Здесь нужно сохранять в БД
        pass
    
    async def notify_about_achievements(self, user_id: int, achievements: List[str]):
        """Уведомить о новых достижениях"""
        try:
            text = "🏆 Новые достижения!\n\n"
            
            for achievement in achievements:
                text += f"• {achievement}\n"
            
            text += "\nПоздравляем! 🎉"
            
            await self.bot.send_message(
                chat_id=user_id,
                text=text
            )
        except:
            pass  # Пользователь может быть недоступен
    
    async def get_user_achievements(self, user_id: int) -> List[str]:
        """Получить достижения пользователя"""
        # Здесь нужно получать из БД
        # Для примера возвращаем фиктивные данные
        rating = await self.get_user_rating(user_id)
        
        achievements = []
        if rating >= 100:
            achievements.append("Новичок 🥉")
        if rating >= 500:
            achievements.append("Активный участник 🥈")
        if rating >= 1000:
            achievements.append("Опытный пользователь 🥇")
        if rating >= 5000:
            achievements.append("Ветеран 👑")
        
        return achievements
    
    async def get_next_level_info(self, current_rating: int) -> Optional[Dict[str, Any]]:
        """Получить информацию о следующем уровне"""
        levels = [
            (100, "Новичок 🥉"),
            (500, "Активный участник 🥈"),
            (1000, "Опытный пользователь 🥇"),
            (5000, "Ветеран 👑"),
            (10000, "Легенда 💎"),
            (50000, "Бог рейтинга ⭐")
        ]
        
        for required_rating, level_name in levels:
            if current_rating < required_rating:
                return {
                    "level_name": level_name,
                    "required_rating": required_rating,
                    "points_needed": required_rating - current_rating
                }
        
        return None
    
    def _get_period_start(self, period: str) -> Optional[datetime]:
        """Получить дату начала периода"""
        now = datetime.now()
        
        if period == "today":
            return now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "week":
            return now - timedelta(days=now.weekday())
        elif period == "month":
            return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        elif period == "year":
            return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        
        return None
    
    async def process_message_for_rating(self, message: Message):
        """Обработка сообщения для начисления рейтинга"""
        if not self.settings["enabled"]:
            return
        
        user_id = message.from_user.id
        
        # Начисление очков за сообщение
        await self.add_rating_points(
            user_id=user_id,
            action=RatingAction.MESSAGE_SENT,
            details={
                "chat_id": message.chat.id,
                "message_id": message.message_id,
                "text_length": len(message.text or "")
            }
        )
        
        # Проверка активного дня
        await self.check_active_day(user_id)
    
    async def check_active_day(self, user_id: int):
        """Проверить и начислить очки за активный день"""
        db = DatabaseManager.get_instance()
        
        # Получение последней активности
        user = await db.get_user(user_id)
        if not user:
            return
        
        # Проверяем, был ли сегодня уже начислен бонус за активность
        today = datetime.now().date()
        last_activity_date = user.last_activity.date()
        
        if last_activity_date < today:
            # Новый активный день
            await self.add_rating_points(
                user_id=user_id,
                action=RatingAction.ACTIVE_DAY,
                details={"date": today.isoformat()}
            )
    
    async def process_poll_participation(self, user_id: int, poll_id: int):
        """Обработка участия в опросе для рейтинга"""
        await self.add_rating_points(
            user_id=user_id,
            action=RatingAction.PARTICIPATED_POLL,
            details={"poll_id": poll_id}
        )
    
    async def process_user_invite(self, inviter_id: int, invited_id: int):
        """Обработка приглашения пользователя"""
        await self.add_rating_points(
            user_id=inviter_id,
            action=RatingAction.INVITED_USER,
            details={"invited_user_id": invited_id}
        )
    
    async def process_premium_subscription(self, user_id: int):
        """Обработка премиум подписки"""
        await self.add_rating_points(
            user_id=user_id,
            action=RatingAction.PREMIUM_SUBSCRIBED
        )
    
    async def process_no_violations_week(self, user_id: int):
        """Обработка недели без нарушений"""
        await self.add_rating_points(
            user_id=user_id,
            action=RatingAction.NO_VIOLATIONS_WEEK
        )
    
    async def apply_rating_decay(self):
        """Применить снижение рейтинга за неактивность"""
        if not self.settings["decay_enabled"]:
            return
        
        db = DatabaseManager.get_instance()
        
        # Получение неактивных пользователей
        cutoff_date = datetime.now() - timedelta(days=self.settings["decay_days"])
        
        users, _ = await db.get_users(
            filters={"max_last_activity": cutoff_date},
            limit=1000
        )
        
        for user in users:
            # Снижение рейтинга
            user.rating = max(0, user.rating - self.settings["decay_amount"])
            await db.update_user(user)
            
            # Обновление кэша
            self._user_rating_cache[user.user_id] = user.rating
        
        # Сброс кэша топа
        self._top_cache.clear()
        
        logger.info(f"Применено снижение рейтинга для {len(users)} неактивных пользователей")
    
    async def reset_daily_limits(self):
        """Сбросить дневные лимиты"""
        # Здесь нужно сбрасывать счетчики дневных очков
        # В реальной системе это должно быть в БД
        pass
    
    async def award_weekly_bonuses(self):
        """Начислить недельные бонусы"""
        if not self.settings["enabled"]:
            return
        
        # Находим топ пользователей за неделю
        top_users = await self.get_top_users("week", limit=10)
        
        for i, (user_id, _, points) in enumerate(top_users):
            bonus = self.settings["weekly_bonus"] // (i + 1)  # Уменьшаем бонус для нижних мест
            
            if bonus > 0:
                await self.add_rating_points(
                    user_id=user_id,
                    action=RatingAction.ACTIVE_DAY,  # Используем существующее действие
                    amount=bonus,
                    details={"weekly_rank": i + 1, "weekly_points": points}
                )
    
    async def award_monthly_bonuses(self):
        """Начислить месячные бонусы"""
        if not self.settings["enabled"]:
            return
        
        # Находим топ пользователей за месяц
        top_users = await self.get_top_users("month", limit=20)
        
        for i, (user_id, _, points) in enumerate(top_users):
            bonus = self.settings["monthly_bonus"] // (i // 2 + 1)  # Уменьшаем бонус
            
            if bonus > 0:
                await self.add_rating_points(
                    user_id=user_id,
                    action=RatingAction.ACTIVE_DAY,  # Используем существующее действие
                    amount=bonus,
                    details={"monthly_rank": i + 1, "monthly_points": points}
                )
    
    async def get_rating_stats(self) -> Dict[str, Any]:
        """Получить статистику системы рейтинга"""
        db = DatabaseManager.get_instance()
        
        # Общая статистика
        users, total_users = await db.get_users(limit=1)
        
        # Средний рейтинг
        cursor = await db.connection.execute(
            f"SELECT AVG(rating) as avg_rating FROM {db.get_table_name('users')} WHERE bot_id = ?",
            (self.admin_system.config.bot_id,)
        )
        
        row = await cursor.fetchone()
        await cursor.close()
        
        avg_rating = row["avg_rating"] if row and row["avg_rating"] else 0
        
        # Распределение по уровням
        levels = [
            (0, 99, "Новички"),
            (100, 499, "Начинающие"),
            (500, 999, "Активные"),
            (1000, 4999, "Опытные"),
            (5000, 9999, "Ветераны"),
            (10000, 999999999, "Легенды")
        ]
        
        distribution = {}
        for min_rating, max_rating, level_name in levels:
            cursor = await db.connection.execute(
                f"""
                SELECT COUNT(*) as count 
                FROM {db.get_table_name('users')} 
                WHERE rating BETWEEN ? AND ? AND bot_id = ?
                """,
                (min_rating, max_rating, self.admin_system.config.bot_id)
            )
            
            row = await cursor.fetchone()
            await cursor.close()
            
            distribution[level_name] = row["count"] if row else 0
        
        # Топ донатеров (премиум пользователей)
        cursor = await db.connection.execute(
            f"""
            SELECT COUNT(*) as premium_count 
            FROM {db.get_table_name('users')} 
            WHERE is_premium = 1 AND bot_id = ?
            """,
            (self.admin_system.config.bot_id,)
        )
        
        row = await cursor.fetchone()
        await cursor.close()
        
        premium_count = row["premium_count"] if row else 0
        
        return {
            "total_users": total_users,
            "avg_rating": round(avg_rating, 2),
            "distribution": distribution,
            "premium_users": premium_count,
            "top_user": await self._get_top_user_info(),
            "recent_activity": await self._get_recent_activity_stats()
        }
    
    async def _get_top_user_info(self) -> Optional[Dict[str, Any]]:
        """Получить информацию о топ пользователе"""
        top = await self.get_top_users("all", limit=1)
        
        if not top:
            return None
        
        user_id, user_name, rating = top[0]
        
        db = DatabaseManager.get_instance()
        user = await db.get_user(user_id)
        
        if not user:
            return None
        
        return {
            "user_id": user_id,
            "name": user_name,
            "rating": rating,
            "registration_date": user.registration_date.strftime("%d.%m.%Y"),
            "is_premium": user.is_premium,
            "warnings": user.warnings
        }
    
    async def _get_recent_activity_stats(self) -> Dict[str, int]:
        """Получить статистику недавней активности"""
        # Здесь нужно получить статистику из логов
        # Для примера возвращаем фиктивные данные
        return {
            "today_active": 150,
            "week_active": 500,
            "month_active": 2000
        }
    
    def get_router(self) -> Router:
        """Получить роутер системы рейтинга"""
        return self.router