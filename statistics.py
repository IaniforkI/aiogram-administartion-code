import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')  # Для использования без GUI
import io
import pandas as pd
import numpy as np

from aiogram.types import CallbackQuery, InputFile
from aiogram.fsm.context import FSMContext

from .models import User, Chat, ActionType
from .ui import create_keyboard, create_pagination_keyboard
from .security import require_admin

logger = logging.getLogger(__name__)

class ChartType(Enum):
    """Типы графиков"""
    LINE = "line"
    BAR = "bar"
    PIE = "pie"
    HEATMAP = "heatmap"
    SCATTER = "scatter"

class PeriodType(Enum):
    """Типы периодов"""
    ALL_TIME = "all"
    LAST_24H = "24h"
    LAST_7D = "7d"
    LAST_30D = "30d"
    LAST_90D = "90d"
    CUSTOM = "custom"

class StatisticsManager:
    """Менеджер статистики и аналитики"""
    
    def __init__(self, admin_system):
        self.admin_system = admin_system
        self.cache = {}
        self.cache_ttl = 300  # 5 минут
        
    async def collect_statistics(self):
        """Сбор статистики (вызывается периодически)"""
        db = self.admin_system.database
        
        # Сбор общей статистики
        await self._collect_global_stats()
        
        # Сбор статистики по пользователям
        await self._collect_user_stats()
        
        # Сбор статистики по чатам
        await self._collect_chat_stats()
        
        logger.info("Статистика собрана")
    
    async def _collect_global_stats(self):
        """Сбор глобальной статистики"""
        db = self.admin_system.database
        
        now = datetime.now()
        period_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        period_end = now
        
        # Общее количество пользователей
        users, total_users = await db.get_users(limit=1)
        await db.add_statistic(
            metric_name="total_users",
            metric_value=total_users,
            period_start=period_start,
            period_end=period_end,
            entity_type="global"
        )
        
        # Количество активных пользователей за последние 24 часа
        active_cutoff = now - timedelta(hours=24)
        active_users, _ = await db.get_users(
            filters={"min_last_activity": active_cutoff},
            limit=1
        )
        
        await db.add_statistic(
            metric_name="active_users_24h",
            metric_value=len(active_users),
            period_start=period_start,
            period_end=period_end,
            entity_type="global"
        )
        
        # Количество чатов
        chats, total_chats = await db.get_chats(limit=1)
        await db.add_statistic(
            metric_name="total_chats",
            metric_value=total_chats,
            period_start=period_start,
            period_end=period_end,
            entity_type="global"
        )
        
        # Количество сообщений за день
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        logs, total_logs = await db.get_action_logs(
            action_type=ActionType.MESSAGE_SENT,
            start_date=start_of_day,
            limit=1
        )
        
        await db.add_statistic(
            metric_name="messages_today",
            metric_value=total_logs,
            period_start=period_start,
            period_end=period_end,
            entity_type="global"
        )
    
    async def _collect_user_stats(self):
        """Сбор статистики по пользователям"""
        db = self.admin_system.database
        
        now = datetime.now()
        period_start = now - timedelta(days=1)
        period_end = now
        
        # Получение всех пользователей
        batch_size = 100
        offset = 0
        
        while True:
            users, _ = await db.get_users(offset=offset, limit=batch_size)
            if not users:
                break
            
            for user in users:
                # Активность пользователя за последние 24 часа
                logs, activity_count = await db.get_action_logs(
                    user_id=user.user_id,
                    start_date=period_start,
                    limit=1000
                )
                
                await db.add_statistic(
                    metric_name="user_activity_24h",
                    metric_value=activity_count,
                    period_start=period_start,
                    period_end=period_end,
                    entity_type="user",
                    entity_id=user.user_id
                )
                
                # Распределение типов активности
                if logs:
                    activity_by_type = {}
                    for log in logs:
                        activity_by_type[log.action_type] = activity_by_type.get(log.action_type, 0) + 1
                    
                    for action_type, count in activity_by_type.items():
                        await db.add_statistic(
                            metric_name=f"user_activity_type_{action_type}",
                            metric_value=count,
                            period_start=period_start,
                            period_end=period_end,
                            entity_type="user",
                            entity_id=user.user_id
                        )
            
            offset += batch_size
    
    async def _collect_chat_stats(self):
        """Сбор статистики по чатам"""
        db = self.admin_system.database
        
        now = datetime.now()
        period_start = now - timedelta(days=1)
        period_end = now
        
        # Получение всех чатов
        batch_size = 50
        offset = 0
        
        while True:
            chats, _ = await db.get_chats(offset=offset, limit=batch_size)
            if not chats:
                break
            
            for chat in chats:
                # Активность в чате за последние 24 часа
                logs, activity_count = await db.get_action_logs(
                    chat_id=chat.chat_id,
                    start_date=period_start,
                    limit=1000
                )
                
                await db.add_statistic(
                    metric_name="chat_activity_24h",
                    metric_value=activity_count,
                    period_start=period_start,
                    period_end=period_end,
                    entity_type="chat",
                    entity_id=chat.chat_id
                )
                
                # Уникальные пользователи в чате
                if logs:
                    unique_users = set(log.user_id for log in logs if log.user_id)
                    await db.add_statistic(
                        metric_name="chat_unique_users_24h",
                        metric_value=len(unique_users),
                        period_start=period_start,
                        period_end=period_end,
                        entity_type="chat",
                        entity_id=chat.chat_id
                    )
            
            offset += batch_size
    
    async def show_overview(self, callback: CallbackQuery):
        """Показать обзор статистики"""
        user_id = callback.from_user.id
        
        security = self.admin_system.security
        if not await security.has_permission(user_id, "stats.view"):
            await callback.message.edit_text("❌ У вас нет прав для просмотра статистики.")
            return
        
        db = self.admin_system.database
        now = datetime.now()
        
        # Получение свежих данных
        cache_key = f"stats_overview_{now.strftime('%Y%m%d%H')}"
        if cache_key in self.cache:
            stats = self.cache[cache_key]
        else:
            stats = await self._get_overview_stats()
            self.cache[cache_key] = stats
            # Очистка кэша через TTL
            asyncio.create_task(self._clear_cache_after(cache_key, self.cache_ttl))
        
        text = "📊 Общая статистика бота\n\n"
        text += f"👥 Всего пользователей: {stats['total_users']:,}\n"
        text += f"📈 Активных за 24ч: {stats['active_users_24h']:,}\n"
        text += f"💬 Всего чатов: {stats['total_chats']:,}\n"
        text += f"📨 Сообщений сегодня: {stats['messages_today']:,}\n\n"
        
        text += "📈 Активность за последние 7 дней:\n"
        for day, count in stats['activity_7d'].items():
            text += f"  {day}: {count:,}\n"
        
        text += "\n🏆 Топ-5 пользователей по рейтингу:\n"
        for i, (user_id, user_name, rating) in enumerate(stats['top_rating'], start=1):
            text += f"  {i}. {user_name}: ⭐ {rating}\n"
        
        buttons = [
            ("👤 По пользователям", "admin_stats_users"),
            ("💬 По чатам", "admin_stats_chats"),
            ("📈 Графики", "admin_stats_charts"),
            ("◀️ Назад", "admin_menu")
        ]
        
        keyboard = create_keyboard(buttons, columns=2)
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def _get_overview_stats(self) -> Dict[str, Any]:
        """Получение данных для обзора"""
        db = self.admin_system.database
        
        stats = {}
        
        # Общее количество пользователей
        _, total_users = await db.get_users(limit=1)
        stats['total_users'] = total_users
        
        # Активные пользователи за 24 часа
        active_cutoff = datetime.now() - timedelta(hours=24)
        active_users, _ = await db.get_users(
            filters={"min_last_activity": active_cutoff},
            limit=1
        )
        stats['active_users_24h'] = len(active_users)
        
        # Общее количество чатов
        _, total_chats = await db.get_chats(limit=1)
        stats['total_chats'] = total_chats
        
        # Сообщения сегодня
        start_of_day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        _, messages_today = await db.get_action_logs(
            action_type=ActionType.MESSAGE_SENT,
            start_date=start_of_day,
            limit=1
        )
        stats['messages_today'] = messages_today
        
        # Активность за 7 дней
        stats['activity_7d'] = {}
        for i in range(6, -1, -1):
            day = datetime.now() - timedelta(days=i)
            day_str = day.strftime('%d.%m')
            start = day.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            
            _, count = await db.get_action_logs(
                start_date=start,
                end_date=end,
                limit=1
            )
            stats['activity_7d'][day_str] = count
        
        # Топ пользователей по рейтингу
        users, _ = await db.get_users(
            limit=5,
            order_by="rating DESC"
        )
        stats['top_rating'] = []
        for user in users:
            stats['top_rating'].append((user.user_id, user.full_name, user.rating))
        
        return stats
    
    async def show_users_stats(self, callback: CallbackQuery, page: int = 0):
        """Показать статистику по пользователям"""
        user_id = callback.from_user.id
        
        security = self.admin_system.security
        if not await security.has_permission(user_id, "stats.view"):
            await callback.message.edit_text("❌ У вас нет прав для просмотра статистики.")
            return
        
        db = self.admin_system.database
        offset = page * 10
        
        # Получение пользователей с сортировкой по активности
        users, total = await db.get_users(
            offset=offset,
            limit=10,
            order_by="last_activity DESC"
        )
        
        text = f"👤 Статистика по пользователям\n\n"
        text += f"📊 Всего: {total:,}\n"
        text += f"📄 Страница {page + 1}/{(total + 9) // 10}\n\n"
        
        for i, user in enumerate(users, start=1):
            # Получение активности за последние 7 дней
            week_ago = datetime.now() - timedelta(days=7)
            logs, activity_count = await db.get_action_logs(
                user_id=user.user_id,
                start_date=week_ago,
                limit=100
            )
            
            text += f"{i}. {user.full_name}\n"
            text += f"   🆔: {user.user_id} | ⭐: {user.rating}\n"
            text += f"   📊 Активность (7д): {activity_count}\n"
            text += f"   📅 Регистрация: {user.registration_date.strftime('%d.%m.%Y')}\n\n"
        
        buttons = [
            ("📈 График активности", "admin_stats_users_chart"),
            ("🏆 Топ по рейтингу", "admin_stats_top_rating"),
            ("⚡ Топ по активности", "admin_stats_top_active")
        ]
        
        keyboard = create_pagination_keyboard(
            current_page=page,
            total_pages=(total + 9) // 10,
            prefix="admin_stats_users",
            additional_buttons=buttons
        )
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def show_chats_stats(self, callback: CallbackQuery, page: int = 0):
        """Показать статистику по чатам"""
        user_id = callback.from_user.id
        
        security = self.admin_system.security
        if not await security.has_permission(user_id, "stats.view"):
            await callback.message.edit_text("❌ У вас нет прав для просмотра статистики.")
            return
        
        db = self.admin_system.database
        offset = page * 10
        
        chats, total = await db.get_chats(
            offset=offset,
            limit=10,
            order_by="last_activity DESC"
        )
        
        text = f"💬 Статистика по чатам\n\n"
        text += f"📊 Всего: {total:,}\n"
        text += f"📄 Страница {page + 1}/{(total + 9) // 10}\n\n"
        
        for i, chat in enumerate(chats, start=1):
            # Получение активности за последние 7 дней
            week_ago = datetime.now() - timedelta(days=7)
            logs, activity_count = await db.get_action_logs(
                chat_id=chat.chat_id,
                start_date=week_ago,
                limit=100
            )
            
            # Уникальные пользователи
            unique_users = set(log.user_id for log in logs if log.user_id) if logs else 0
            
            text += f"{i}. {chat.title}\n"
            text += f"   🆔: {chat.chat_id} | 👥: {chat.members_count}\n"
            text += f"   📊 Активность (7д): {activity_count}\n"
            text += f"   👤 Уникальных: {unique_users}\n\n"
        
        buttons = [
            ("📈 График активности", "admin_stats_chats_chart"),
            ("⚡ Топ по активности", "admin_stats_chats_top")
        ]
        
        keyboard = create_pagination_keyboard(
            current_page=page,
            total_pages=(total + 9) // 10,
            prefix="admin_stats_chats",
            additional_buttons=buttons
        )
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def show_charts_menu(self, callback: CallbackQuery):
        """Показать меню графиков"""
        user_id = callback.from_user.id
        
        security = self.admin_system.security
        if not await security.has_permission(user_id, "stats.charts"):
            await callback.message.edit_text("❌ У вас нет прав для просмотра графиков.")
            return
        
        text = "📈 Графики и визуализация\n\n"
        text += "Выберите тип графика:"
        
        buttons = [
            ("📊 Рост пользователей", "admin_chart_users_growth"),
            ("📈 Активность по дням", "admin_chart_daily_activity"),
            ("👥 Распределение по чатам", "admin_chart_chats_distribution"),
            ("⏰ Активность по часам", "admin_chart_hourly_activity"),
            ("◀️ Назад", "admin_stats")
        ]
        
        keyboard = create_keyboard(buttons, columns=2)
        
        await callback.message.edit_text(text, reply_markup=keyboard)
    
    async def create_chart(self, chart_type: ChartType, period: PeriodType = PeriodType.LAST_7D, **kwargs) -> io.BytesIO:
        """Создать график"""
        db = self.admin_system.database
        
        if chart_type == ChartType.LINE:
            return await self._create_line_chart(period, **kwargs)
        elif chart_type == ChartType.BAR:
            return await self._create_bar_chart(period, **kwargs)
        elif chart_type == ChartType.PIE:
            return await self._create_pie_chart(period, **kwargs)
        elif chart_type == ChartType.HEATMAP:
            return await self._create_heatmap(period, **kwargs)
        elif chart_type == ChartType.SCATTER:
            return await self._create_scatter_chart(period, **kwargs)
    
    async def _create_line_chart(self, period: PeriodType, **kwargs) -> io.BytesIO:
        """Создать линейный график"""
        db = self.admin_system.database
        
        # Определение периода
        end_date = datetime.now()
        if period == PeriodType.LAST_24H:
            start_date = end_date - timedelta(hours=24)
            interval = 'hour'
        elif period == PeriodType.LAST_7D:
            start_date = end_date - timedelta(days=7)
            interval = 'day'
        elif period == PeriodType.LAST_30D:
            start_date = end_date - timedelta(days=30)
            interval = 'day'
        else:
            start_date = end_date - timedelta(days=7)
            interval = 'day'
        
        # Получение данных
        dates = []
        values = []
        
        current = start_date
        while current <= end_date:
            next_interval = current + timedelta(**{f'{interval}s': 1})
            
            logs, count = await db.get_action_logs(
                start_date=current,
                end_date=next_interval,
                limit=1
            )
            
            dates.append(current)
            values.append(count)
            
            current = next_interval
        
        # Создание графика
        plt.figure(figsize=(10, 6))
        plt.plot(dates, values, marker='o', linewidth=2)
        plt.title(f'Активность за {period.value}')
        plt.xlabel('Дата')
        plt.ylabel('Количество действий')
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        
        # Сохранение в буфер
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        plt.close()
        buf.seek(0)
        
        return buf
    
    async def _create_bar_chart(self, period: PeriodType, **kwargs) -> io.BytesIO:
        """Создать столбчатую диаграмму"""
        db = self.admin_system.database
        
        # Получение топ пользователей по активности
        users, _ = await db.get_users(limit=10, order_by="last_activity DESC")
        
        user_names = []
        activity_counts = []
        
        for user in users:
            week_ago = datetime.now() - timedelta(days=7)
            logs, count = await db.get_action_logs(
                user_id=user.user_id,
                start_date=week_ago,
                limit=100
            )
            
            user_names.append(user.full_name[:15] + '...' if len(user.full_name) > 15 else user.full_name)
            activity_counts.append(count)
        
        # Создание графика
        plt.figure(figsize=(12, 6))
        bars = plt.bar(user_names, activity_counts, color='skyblue')
        plt.title('Топ пользователей по активности (7 дней)')
        plt.xlabel('Пользователь')
        plt.ylabel('Количество действий')
        plt.xticks(rotation=45, ha='right')
        
        # Добавление значений на столбцы
        for bar in bars:
            height = bar.get_height()
            plt.text(bar.get_x() + bar.get_width()/2., height,
                    f'{int(height)}', ha='center', va='bottom')
        
        plt.tight_layout()
        
        # Сохранение в буфер
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        plt.close()
        buf.seek(0)
        
        return buf
    
    async def _create_pie_chart(self, period: PeriodType, **kwargs) -> io.BytesIO:
        """Создать круговую диаграмму"""
        db = self.admin_system.database
        
        # Получение распределения по типам чатов
        group_chats, group_count = await db.get_chats(chat_type="group", limit=1)
        supergroup_chats, supergroup_count = await db.get_chats(chat_type="supergroup", limit=1)
        private_chats, private_count = await db.get_chats(chat_type="private", limit=1)
        
        labels = ['Группы', 'Супергруппы', 'Приватные']
        sizes = [group_count, supergroup_count, private_count]
        colors = ['lightcoral', 'lightskyblue', 'lightgreen']
        
        # Создание графика
        plt.figure(figsize=(8, 8))
        plt.pie(sizes, labels=labels, colors=colors, autopct='%1.1f%%', startangle=140)
        plt.title('Распределение чатов по типам')
        plt.axis('equal')
        
        # Сохранение в буфер
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        plt.close()
        buf.seek(0)
        
        return buf
    
    async def _create_heatmap(self, period: PeriodType, **kwargs) -> io.BytesIO:
        """Создать тепловую карту"""
        db = self.admin_system.database
        
        # Получение активности по дням недели и часам
        days = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
        hours = list(range(24))
        
        # Создание матрицы активности
        activity_matrix = np.zeros((7, 24))
        
        # Получение данных за последние 30 дней
        start_date = datetime.now() - timedelta(days=30)
        logs, _ = await db.get_action_logs(
            start_date=start_date,
            limit=10000
        )
        
        for log in logs:
            if hasattr(log, 'timestamp'):
                timestamp = log.timestamp
                day_of_week = timestamp.weekday()  # 0 = Monday
                hour = timestamp.hour
                activity_matrix[day_of_week][hour] += 1
        
        # Создание тепловой карты
        plt.figure(figsize=(12, 8))
        plt.imshow(activity_matrix, cmap='YlOrRd', aspect='auto')
        plt.colorbar(label='Количество действий')
        plt.title('Активность по дням недели и часам (30 дней)')
        plt.xlabel('Час дня')
        plt.ylabel('День недели')
        plt.xticks(range(24), [f'{h}:00' for h in range(24)], rotation=45)
        plt.yticks(range(7), days)
        
        plt.tight_layout()
        
        # Сохранение в буфер
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        plt.close()
        buf.seek(0)
        
        return buf
    
    async def _create_scatter_chart(self, period: PeriodType, **kwargs) -> io.BytesIO:
        """Создать точечную диаграмму"""
        db = self.admin_system.database
        
        # Получение данных: рейтинг vs активность
        users, _ = await db.get_users(limit=50)
        
        ratings = []
        activities = []
        user_names = []
        
        for user in users:
            week_ago = datetime.now() - timedelta(days=7)
            logs, activity_count = await db.get_action_logs(
                user_id=user.user_id,
                start_date=week_ago,
                limit=100
            )
            
            ratings.append(user.rating)
            activities.append(activity_count)
            user_names.append(user.full_name)
        
        # Создание графика
        plt.figure(figsize=(10, 6))
        scatter = plt.scatter(ratings, activities, alpha=0.6, s=100)
        plt.title('Зависимость рейтинга от активности (7 дней)')
        plt.xlabel('Рейтинг')
        plt.ylabel('Активность')
        plt.grid(True, alpha=0.3)
        
        # Добавление подписей для некоторых точек
        for i, (rating, activity, name) in enumerate(zip(ratings, activities, user_names)):
            if i % 5 == 0:  # Каждую 5-ю точку
                plt.annotate(name[:10], (rating, activity), fontsize=8)
        
        plt.tight_layout()
        
        # Сохранение в буфер
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100)
        plt.close()
        buf.seek(0)
        
        return buf
    
    async def _clear_cache_after(self, key: str, ttl: int):
        """Очистка кэша через указанное время"""
        await asyncio.sleep(ttl)
        if key in self.cache:
            del self.cache[key]
    
    async def get_user_statistics(self, user_id: int, period_days: int = 30) -> Dict[str, Any]:
        """Получить статистику пользователя"""
        db = self.admin_system.database
        
        user = await db.get_user(user_id)
        if not user:
            return {}
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=period_days)
        
        # Получение логов активности
        logs, total_activity = await db.get_action_logs(
            user_id=user_id,
            start_date=start_date,
            limit=1000
        )
        
        # Анализ типов активности
        activity_by_type = {}
        for log in logs:
            action_type = log.action_type
            activity_by_type[action_type] = activity_by_type.get(action_type, 0) + 1
        
        # Активность по дням
        activity_by_day = {}
        current = start_date
        while current <= end_date:
            day_str = current.strftime('%Y-%m-%d')
            activity_by_day[day_str] = 0
            current += timedelta(days=1)
        
        for log in logs:
            day_str = log.timestamp.strftime('%Y-%m-%d')
            if day_str in activity_by_day:
                activity_by_day[day_str] += 1
        
        # Активность по чатам
        activity_by_chat = {}
        for log in logs:
            if log.chat_id:
                activity_by_chat[log.chat_id] = activity_by_chat.get(log.chat_id, 0) + 1
        
        # Получение информации о чатах
        chat_details = {}
        for chat_id in list(activity_by_chat.keys())[:10]:  # Топ-10 чатов
            chat = await db.get_chat(chat_id)
            if chat:
                chat_details[chat_id] = {
                    'title': chat.title,
                    'activity': activity_by_chat[chat_id]
                }
        
        return {
            'user': user,
            'period': {
                'start': start_date,
                'end': end_date,
                'days': period_days
            },
            'total_activity': total_activity,
            'activity_by_type': activity_by_type,
            'activity_by_day': activity_by_day,
            'top_chats': chat_details,
            'daily_average': total_activity / period_days if period_days > 0 else 0
        }
    
    async def export_statistics(self, format_type: str = 'csv', **kwargs) -> bytes:
        """Экспорт статистики"""
        if format_type == 'csv':
            return await self._export_csv(**kwargs)
        elif format_type == 'json':
            return await self._export_json(**kwargs)
        elif format_type == 'excel':
            return await self._export_excel(**kwargs)
        else:
            raise ValueError(f"Неподдерживаемый формат: {format_type}")
    
    async def _export_csv(self, **kwargs) -> bytes:
        """Экспорт в CSV"""
        import csv
        import io
        
        db = self.admin_system.database
        
        # Получение данных
        users, total = await db.get_users(limit=1000)
        
        # Создание CSV
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Заголовки
        writer.writerow([
            'ID', 'Username', 'Имя', 'Фамилия', 'Язык',
            'Премиум', 'Email', 'Телефон', 'Рейтинг',
            'Варны', 'Статус', 'Дата регистрации',
            'Последняя активность'
        ])
        
        # Данные
        for user in users:
            writer.writerow([
                user.user_id,
                user.username or '',
                user.first_name,
                user.last_name or '',
                user.language_code,
                'Да' if user.is_premium else 'Нет',
                user.email or '',
                user.phone or '',
                user.rating,
                user.warnings,
                user.status.name,
                user.registration_date.strftime('%Y-%m-%d %H:%M:%S'),
                user.last_activity.strftime('%Y-%m-%d %H:%M:%S')
            ])
        
        return output.getvalue().encode('utf-8')
    
    async def _export_json(self, **kwargs) -> bytes:
        """Экспорт в JSON"""
        import json
        
        db = self.admin_system.database
        
        # Получение данных
        users, total = await db.get_users(limit=1000)
        
        # Преобразование в словари
        data = []
        for user in users:
            user_dict = user.to_dict()
            # Преобразование дат в строки
            user_dict['registration_date'] = user.registration_date.isoformat()
            user_dict['last_activity'] = user.last_activity.isoformat()
            data.append(user_dict)
        
        return json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
    
    async def _export_excel(self, **kwargs) -> bytes:
        """Экспорт в Excel"""
        try:
            import pandas as pd
        except ImportError:
            raise ImportError("Для экспорта в Excel установите pandas: pip install pandas openpyxl")
        
        db = self.admin_system.database
        
        # Получение данных
        users, total = await db.get_users(limit=1000)
        
        # Преобразование в DataFrame
        data = []
        for user in users:
            data.append({
                'ID': user.user_id,
                'Username': user.username or '',
                'Имя': user.first_name,
                'Фамилия': user.last_name or '',
                'Язык': user.language_code,
                'Премиум': 'Да' if user.is_premium else 'Нет',
                'Email': user.email or '',
                'Телефон': user.phone or '',
                'Рейтинг': user.rating,
                'Варны': user.warnings,
                'Статус': user.status.name,
                'Дата регистрации': user.registration_date,
                'Последняя активность': user.last_activity
            })
        
        df = pd.DataFrame(data)
        
        # Сохранение в буфер
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Пользователи', index=False)
        
        return output.getvalue()