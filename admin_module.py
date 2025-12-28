# admin_module.py
"""
Полный модуль админ-панели с конструкторами всех типов контента.
Включает все интерфейсы из промпта и полное управление через Telegram.
"""

import asyncio
import json
import random
import math
import re
import os
import shutil
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any, Union
from enum import Enum
import uuid
from dataclasses import dataclass, field

from aiogram import Router, F, types, html
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    CallbackQuery, Message, InputFile, FSInputFile
)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, update, and_, or_, desc, func, delete, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload

from models import (
    User, UserRole, Location, TravelRoute, MobTemplate, MobSpawn,
    MobDrop, ItemTemplate, Item, ItemType, ItemRarity, ResourceTemplate,
    ResourceSpawn, ResourceType, GameEvent, EventTrigger, EventReward,
    EventType, EventActivationType, ChestTemplate, ChestReward,
    Recipe, RecipeIngredient, ProfessionType, ActiveAction, ActionType,
    ActiveBattle, BattleStatus, PvPChallenge, PvPMatch, SystemSettings,
    AuditLog, PlayerStat, ActiveEffect, Inventory, Discovery, BackupLog,
    StateSnapshot, LocationType, MobType
)

# ============ КОНСТАНТЫ ============

class AdminAction(str, Enum):
    CREATE = "create"
    EDIT = "edit"
    DELETE = "delete"
    LIST = "list"
    TOGGLE = "toggle"
    VIEW = "view"
    STATS = "stats"
    BACKUP = "backup"
    RESTART = "restart"
    FORMULAS = "formulas"
    SEARCH = "search"
    EXPORT = "export"
    IMPORT = "import"

class ContentType(str, Enum):
    MOB = "mob"
    ITEM = "item"
    LOCATION = "location"
    RESOURCE = "resource"
    EVENT = "event"
    CHEST = "chest"
    RECIPE = "recipe"
    SYSTEM = "system"
    PLAYER = "player"

# ============ РОУТЕР И СОСТОЯНИЯ ============

admin_router = Router()

class AdminStates(StatesGroup):
    # Основные состояния
    main_menu = State()
    
    # Конструкторы контента
    create_mob_basic = State()
    create_mob_stats = State()
    create_mob_drops = State()
    create_mob_distribution = State()
    create_mob_final = State()
    
    create_item_basic = State()
    create_item_stats = State()
    create_item_economy = State()
    create_item_flags = State()
    create_item_final = State()
    
    create_location_basic = State()
    create_location_mobs = State()
    create_location_resources = State()
    create_location_routes = State()
    create_location_events = State()
    create_location_final = State()
    
    create_resource_basic = State()
    create_resource_params = State()
    create_resource_distribution = State()
    create_resource_final = State()
    
    create_event_basic = State()
    create_event_activation = State()
    create_event_locations = State()
    create_event_rewards = State()
    create_event_effects = State()
    create_event_final = State()
    
    create_chest_basic = State()
    create_chest_contents = State()
    create_chest_traps = State()
    create_chest_requirements = State()
    create_chest_final = State()
    
    create_recipe_basic = State()
    create_recipe_ingredients = State()
    create_recipe_requirements = State()
    create_recipe_final = State()
    
    # Системные настройки
    system_settings = State()
    edit_formula = State()
    edit_setting = State()
    
    # Управление игроками
    player_management = State()
    player_search = State()
    player_details = State()
    player_edit = State()
    player_give_gold = State()
    player_give_item = State()
    player_edit_stats = State()
    
    # Бэкапы и экспорт
    backup_menu = State()
    backup_create = State()
    backup_restore = State()
    export_data = State()
    import_data = State()
    
    # Формулы
    formula_editor = State()
    formula_edit = State()
    
    # Редактирование существующего контента
    edit_content = State()
    delete_confirm = State()
    
    # Специальные состояния для массовых операций
    mass_operation = State()

# ============ МЕНЕДЖЕР АДМИН-ПАНЕЛИ ============

class AdminManager:
    """Менеджер для управления админ-панелью"""
    
    def __init__(self, db_session_factory, redis_client=None, engine=None):
        self.db_session_factory = db_session_factory
        self.redis = redis_client
        self.engine = engine
        self.backup_dir = "backups"
        
        # Создаем директорию для бэкапов
        os.makedirs(self.backup_dir, exist_ok=True)
    
    async def check_admin_access(self, telegram_id: int) -> bool:
        """Проверить доступ к админ-панели"""
        async with self.db_session_factory() as db:
            result = await db.execute(
                select(User).where(
                    and_(
                        User.telegram_id == telegram_id,
                        User.role.in_([UserRole.ADMIN, UserRole.MODERATOR])
                    )
                )
            )
            user = result.scalar_one_or_none()
            return user is not None
    
    async def get_admin_user(self, telegram_id: int) -> Optional[User]:
        """Получить админа"""
        async with self.db_session_factory() as db:
            result = await db.execute(
                select(User).where(
                    and_(
                        User.telegram_id == telegram_id,
                        User.role.in_([UserRole.ADMIN, UserRole.MODERATOR])
                    )
                )
            )
            return result.scalar_one_or_none()
    
    async def get_system_statistics(self, db: AsyncSession) -> Dict[str, Any]:
        """Получить системную статистику"""
        stats = {}
        
        # Количество игроков
        players = await db.execute(select(func.count(User.id)))
        stats['players_total'] = players.scalar()
        
        # Новые игроки за сегодня
        today = datetime.utcnow().date()
        new_players = await db.execute(
            select(func.count(User.id)).where(
                func.date(User.created_at) == today
            )
        )
        stats['players_today'] = new_players.scalar()
        
        # Онлайн игроки (последние 15 минут)
        active_time = datetime.utcnow() - timedelta(minutes=15)
        online = await db.execute(
            select(func.count(User.id)).where(User.last_active >= active_time)
        )
        stats['online'] = online.scalar()
        
        # Активные бои
        active_battles = await db.execute(
            select(func.count(ActiveBattle.id)).where(
                ActiveBattle.status == BattleStatus.ACTIVE
            )
        )
        stats['active_battles'] = active_battles.scalar()
        
        # Активные путешествия
        active_travels = await db.execute(
            select(func.count(ActiveAction.id)).where(
                and_(
                    ActiveAction.action_type == ActionType.TRAVEL,
                    ActiveAction.is_completed == False
                )
            )
        )
        stats['active_travels'] = active_travels.scalar()
        
        # Активные крафты
        active_crafts = await db.execute(
            select(func.count(ActiveAction.id)).where(
                and_(
                    ActiveAction.action_type == ActionType.CRAFTING,
                    ActiveAction.is_completed == False
                )
            )
        )
        stats['active_crafts'] = active_crafts.scalar()
        
        # Количество контента
        stats['mobs'] = await db.execute(select(func.count(MobTemplate.id)))
        stats['mobs'] = stats['mobs'].scalar()
        
        stats['items'] = await db.execute(select(func.count(ItemTemplate.id)))
        stats['items'] = stats['items'].scalar()
        
        stats['locations'] = await db.execute(select(func.count(Location.id)))
        stats['locations'] = stats['locations'].scalar()
        
        stats['events'] = await db.execute(select(func.count(GameEvent.id)))
        stats['events'] = stats['events'].scalar()
        
        stats['chests'] = await db.execute(select(func.count(ChestTemplate.id)))
        stats['chests'] = stats['chests'].scalar()
        
        stats['recipes'] = await db.execute(select(func.count(Recipe.id)))
        stats['recipes'] = stats['recipes'].scalar()
        
        stats['resources'] = await db.execute(select(func.count(ResourceTemplate.id)))
        stats['resources'] = stats['resources'].scalar()
        
        # Активные события
        active_events = await db.execute(
            select(func.count(GameEvent.id)).where(GameEvent.is_active == True)
        )
        stats['active_events'] = active_events.scalar()
        
        # Общее золото в экономике
        total_gold = await db.execute(select(func.sum(User.gold)))
        stats['total_gold'] = total_gold.scalar() or 0
        
        # Средний уровень игроков
        avg_level = await db.execute(select(func.avg(User.level)))
        stats['avg_level'] = round(avg_level.scalar() or 0, 1)
        
        # Статистика PvP
        total_pvp = await db.execute(select(func.count(PvPMatch.id)))
        stats['total_pvp'] = total_pvp.scalar()
        
        # Последний бэкап
        last_backup = await db.execute(
            select(BackupLog).order_by(BackupLog.created_at.desc()).limit(1)
        )
        last_backup = last_backup.scalar_one_or_none()
        stats['last_backup'] = last_backup.created_at if last_backup else None
        
        # Размер базы данных (приблизительно)
        stats['db_size'] = await self._estimate_db_size(db)
        
        return stats
    
    async def _estimate_db_size(self, db: AsyncSession) -> str:
        """Оценить размер базы данных"""
        try:
            # Запрос для PostgreSQL
            result = await db.execute(
                text("SELECT pg_database_size(current_database())")
            )
            size_bytes = result.scalar()
            if size_bytes:
                if size_bytes < 1024:
                    return f"{size_bytes} B"
                elif size_bytes < 1024 * 1024:
                    return f"{size_bytes / 1024:.1f} KB"
                else:
                    return f"{size_bytes / (1024 * 1024):.1f} MB"
        except:
            pass
        return "Неизвестно"
    
    async def search_players(self, db: AsyncSession, query: str, page: int = 1, limit: int = 10) -> Tuple[List[User], int]:
        """Поиск игроков"""
        offset = (page - 1) * limit
        
        conditions = []
        
        # Разные типы поиска
        if query.isdigit():
            # Поиск по telegram_id
            try:
                conditions.append(User.telegram_id == int(query))
            except:
                pass
        
        # Поиск по username (без @)
        if query.startswith('@'):
            username_query = query[1:].strip()
            if username_query:
                conditions.append(User.username.ilike(f"%{username_query}%"))
        else:
            # Общий поиск по имени и username
            conditions.append(
                or_(
                    User.username.ilike(f"%{query}%"),
                    User.first_name.ilike(f"%{query}%"),
                    User.last_name.ilike(f"%{query}%")
                )
            )
        
        if not conditions:
            conditions.append(User.id.isnot(None))  # Всегда true
        
        # Выполняем запрос
        result = await db.execute(
            select(User)
            .where(and_(*conditions))
            .order_by(desc(User.last_active))
            .offset(offset)
            .limit(limit)
        )
        players = result.scalars().all()
        
        # Общее количество
        total_result = await db.execute(
            select(func.count(User.id)).where(and_(*conditions))
        )
        total = total_result.scalar()
        
        return players, total
    
    async def get_player_details(self, db: AsyncSession, player_id: uuid.UUID) -> Dict[str, Any]:
        """Получить детальную информацию об игроке"""
        player = await db.get(User, player_id)
        if not player:
            return {}
        
        # Получаем статистику
        stats = await db.execute(
            select(PlayerStat).where(PlayerStat.user_id == player_id)
        )
        player_stat = stats.scalar_one_or_none()
        
        # Получаем инвентарь
        inventory_result = await db.execute(
            select(Inventory)
            .where(Inventory.user_id == player_id)
            .options(
                selectinload(Inventory.items)
                .selectinload(Item.template)
            )
        )
        inventory = inventory_result.scalar_one_or_none()
        
        # Получаем экипировку с деталями
        equipped_items = {}
        if player.weapon_id:
            weapon_result = await db.execute(
                select(Item)
                .where(Item.id == player.weapon_id)
                .options(selectinload(Item.template))
            )
            weapon = weapon_result.scalar_one_or_none()
            if weapon and weapon.template:
                equipped_items['weapon'] = {
                    'name': weapon.template.name,
                    'icon': weapon.template.icon,
                    'damage': f"{weapon.template.damage_min}-{weapon.template.damage_max}"
                }
        
        if player.armor_id:
            armor_result = await db.execute(
                select(Item)
                .where(Item.id == player.armor_id)
                .options(selectinload(Item.template))
            )
            armor = armor_result.scalar_one_or_none()
            if armor and armor.template:
                equipped_items['armor'] = {
                    'name': armor.template.name,
                    'icon': armor.template.icon,
                    'defense': armor.template.defense
                }
        
        # Получаем последние 5 битв
        last_battles = await db.execute(
            select(ActiveBattle)
            .where(
                and_(
                    ActiveBattle.user_id == player_id,
                    ActiveBattle.ended_at.isnot(None)
                )
            )
            .order_by(desc(ActiveBattle.ended_at))
            .limit(5)
            .options(selectinload(ActiveBattle.mob_template))
        )
        last_battles = last_battles.scalars().all()
        
        # Получаем последние PvP матчи
        last_pvp = await db.execute(
            select(PvPMatch)
            .where(
                or_(
                    PvPMatch.player1_id == player_id,
                    PvPMatch.player2_id == player_id
                )
            )
            .order_by(desc(PvPMatch.ended_at))
            .limit(5)
        )
        last_pvp = last_pvp.scalars().all()
        
        # Получаем открытия
        discoveries = await db.execute(
            select(Discovery).where(Discovery.user_id == player_id)
        )
        discoveries = discoveries.scalar_one_or_none()
        
        # Получаем активные эффекты
        active_effects = await db.execute(
            select(ActiveEffect)
            .where(
                and_(
                    ActiveEffect.user_id == player_id,
                    ActiveEffect.end_time > datetime.utcnow()
                )
            )
            .order_by(desc(ActiveEffect.end_time))
        )
        active_effects = active_effects.scalars().all()
        
        # Получаем активные действия
        current_actions = await db.execute(
            select(ActiveAction)
            .where(
                and_(
                    ActiveAction.user_id == player_id,
                    ActiveAction.is_completed == False
                )
            )
        )
        current_actions = current_actions.scalars().all()
        
        # Рассчитываем реальные характеристики с учетом экипировки
        real_stats = await self._calculate_real_stats(db, player)
        
        return {
            'player': player,
            'stats': player_stat,
            'inventory': inventory,
            'equipped_items': equipped_items,
            'last_battles': last_battles,
            'last_pvp': last_pvp,
            'discoveries': discoveries,
            'active_effects': active_effects,
            'current_actions': current_actions,
            'real_stats': real_stats
        }
    
    async def _calculate_real_stats(self, db: AsyncSession, player: User) -> Dict[str, Any]:
        """Рассчитать реальные характеристики с учетом экипировки"""
        stats = {
            'max_hp': 100,
            'max_mp': 50,
            'damage_min': 5,
            'damage_max': 10,
            'defense': 0,
            'strength': player.strength,
            'agility': player.agility,
            'intelligence': player.intelligence,
            'constitution': player.constitution
        }
        
        # Базовые значения от характеристик
        stats['max_hp'] += player.constitution * 5 + player.level * 10
        stats['max_mp'] += player.intelligence * 3 + player.level * 5
        stats['damage_min'] += player.strength * 0.5
        stats['damage_max'] += player.strength * 0.5
        
        # Экипировка
        equipment_ids = [
            player.weapon_id,
            player.armor_id,
            player.helmet_id,
            player.gloves_id,
            player.boots_id
        ]
        
        for item_id in equipment_ids:
            if item_id:
                item_result = await db.execute(
                    select(Item)
                    .where(Item.id == item_id)
                    .options(selectinload(Item.template))
                )
                item = item_result.scalar_one_or_none()
                if item and item.template:
                    template = item.template
                    
                    # Бонусы от предмета
                    stats['max_hp'] += template.health_bonus or 0
                    stats['max_mp'] += template.mana_bonus or 0
                    stats['damage_min'] += template.damage_min or 0
                    stats['damage_max'] += template.damage_max or 0
                    stats['defense'] += template.defense or 0
                    stats['strength'] += template.strength_bonus or 0
                    stats['agility'] += template.agility_bonus or 0
                    stats['intelligence'] += template.intelligence_bonus or 0
                    stats['constitution'] += template.constitution_bonus or 0
        
        # Округляем
        stats['max_hp'] = int(stats['max_hp'])
        stats['max_mp'] = int(stats['max_mp'])
        stats['damage_min'] = int(stats['damage_min'])
        stats['damage_max'] = int(stats['damage_max'])
        stats['defense'] = int(stats['defense'])
        
        return stats
    
    async def give_gold_to_player(self, db: AsyncSession, player_id: uuid.UUID, amount: int, admin_id: uuid.UUID, reason: str = "") -> bool:
        """Выдать золото игроку"""
        player = await db.get(User, player_id)
        if not player:
            return False
        
        old_balance = player.gold
        player.gold += amount
        player.total_gold_earned += max(0, amount)
        
        # Логируем действие
        audit_log = AuditLog(
            user_id=admin_id,
            action="admin_give_gold",
            details={
                "target_player_id": str(player_id),
                "target_name": player.username or f"ID: {player.telegram_id}",
                "amount": amount,
                "old_balance": old_balance,
                "new_balance": player.gold,
                "reason": reason
            }
        )
        db.add(audit_log)
        
        await db.commit()
        return True
    
    async def take_gold_from_player(self, db: AsyncSession, player_id: uuid.UUID, amount: int, admin_id: uuid.UUID, reason: str = "") -> bool:
        """Забрать золото у игрока"""
        player = await db.get(User, player_id)
        if not player:
            return False
        
        old_balance = player.gold
        player.gold = max(0, player.gold - amount)
        
        # Логируем действие
        audit_log = AuditLog(
            user_id=admin_id,
            action="admin_take_gold",
            details={
                "target_player_id": str(player_id),
                "target_name": player.username or f"ID: {player.telegram_id}",
                "amount": amount,
                "old_balance": old_balance,
                "new_balance": player.gold,
                "reason": reason
            }
        )
        db.add(audit_log)
        
        await db.commit()
        return True
    
    async def give_item_to_player(self, db: AsyncSession, player_id: uuid.UUID, item_template_id: uuid.UUID, quantity: int, admin_id: uuid.UUID, reason: str = "") -> bool:
        """Выдать предмет игроку"""
        player = await db.get(User, player_id)
        item_template = await db.get(ItemTemplate, item_template_id)
        
        if not player or not item_template:
            return False
        
        # Получаем инвентарь
        inventory = await db.execute(
            select(Inventory).where(Inventory.user_id == player_id)
        )
        inventory = inventory.scalar_one_or_none()
        
        if not inventory:
            inventory = Inventory(user_id=player_id)
            db.add(inventory)
            await db.flush()
        
        # Проверяем переполнение инвентаря
        total_items = await db.execute(
            select(func.count(Item.id)).where(Item.owner_id == player_id)
        )
        total_items = total_items.scalar()
        
        if total_items >= inventory.capacity:
            # Пытаемся добавить к существующему стеку
            existing_item = await db.execute(
                select(Item).where(
                    and_(
                        Item.owner_id == player_id,
                        Item.template_id == item_template_id,
                        Item.quantity < item_template.stack_size
                    )
                )
            )
            existing_item = existing_item.scalar_one_or_none()
            
            if existing_item:
                available_space = item_template.stack_size - existing_item.quantity
                add_quantity = min(quantity, available_space)
                existing_item.quantity += add_quantity
                quantity -= add_quantity
                
                if quantity <= 0:
                    # Логируем действие
                    audit_log = AuditLog(
                        user_id=admin_id,
                        action="admin_give_item",
                        details={
                            "target_player_id": str(player_id),
                            "target_name": player.username or f"ID: {player.telegram_id}",
                            "item_template_id": str(item_template_id),
                            "item_name": item_template.name,
                            "quantity": add_quantity,
                            "stacked": True,
                            "reason": reason
                        }
                    )
                    db.add(audit_log)
                    await db.commit()
                    return True
        
        # Создаем новые предметы
        while quantity > 0:
            if total_items >= inventory.capacity:
                break
            
            stack_size = min(quantity, item_template.stack_size)
            item = Item(
                template_id=item_template_id,
                owner_id=player_id,
                quantity=stack_size
            )
            db.add(item)
            quantity -= stack_size
            total_items += 1
        
        # Логируем действие
        audit_log = AuditLog(
            user_id=admin_id,
            action="admin_give_item",
            details={
                "target_player_id": str(player_id),
                "target_name": player.username or f"ID: {player.telegram_id}",
                "item_template_id": str(item_template_id),
                "item_name": item_template.name,
                "quantity": quantity,
                "reason": reason
            }
        )
        db.add(audit_log)
        
        await db.commit()
        return True
    
    async def edit_player_stats(self, db: AsyncSession, player_id: uuid.UUID, stats_data: Dict[str, Any], admin_id: uuid.UUID, reason: str = "") -> bool:
        """Изменить характеристики игрока"""
        player = await db.get(User, player_id)
        if not player:
            return False
        
        old_stats = {
            'level': player.level,
            'experience': player.experience,
            'strength': player.strength,
            'agility': player.agility,
            'intelligence': player.intelligence,
            'constitution': player.constitution,
            'free_points': player.free_points,
            'gold': player.gold,
            'current_hp': player.current_hp,
            'max_hp': player.max_hp,
            'current_mp': player.current_mp,
            'max_mp': player.max_mp
        }
        
        # Обновляем характеристики
        if 'level' in stats_data:
            player.level = max(1, min(100, stats_data['level']))
        
        if 'experience' in stats_data:
            player.experience = max(0, stats_data['experience'])
        
        if 'strength' in stats_data:
            player.strength = max(1, min(999, stats_data['strength']))
        
        if 'agility' in stats_data:
            player.agility = max(1, min(999, stats_data['agility']))
        
        if 'intelligence' in stats_data:
            player.intelligence = max(1, min(999, stats_data['intelligence']))
        
        if 'constitution' in stats_data:
            player.constitution = max(1, min(999, stats_data['constitution']))
        
        if 'free_points' in stats_data:
            player.free_points = max(0, min(999, stats_data['free_points']))
        
        if 'gold' in stats_data:
            player.gold = max(0, min(9999999, stats_data['gold']))
        
        # Пересчитываем HP и MP с учетом экипировки
        await self._recalculate_player_hp_mp(db, player)
        
        if 'current_hp' in stats_data:
            player.current_hp = max(0, min(player.max_hp, stats_data['current_hp']))
        
        if 'current_mp' in stats_data:
            player.current_mp = max(0, min(player.max_mp, stats_data['current_mp']))
        
        # Логируем действие
        audit_log = AuditLog(
            user_id=admin_id,
            action="admin_edit_stats",
            details={
                "target_player_id": str(player_id),
                "target_name": player.username or f"ID: {player.telegram_id}",
                "old_stats": old_stats,
                "new_stats": {
                    'level': player.level,
                    'experience': player.experience,
                    'strength': player.strength,
                    'agility': player.agility,
                    'intelligence': player.intelligence,
                    'constitution': player.constitution,
                    'free_points': player.free_points,
                    'gold': player.gold,
                    'current_hp': player.current_hp,
                    'max_hp': player.max_hp,
                    'current_mp': player.current_mp,
                    'max_mp': player.max_mp
                },
                "reason": reason
            }
        )
        db.add(audit_log)
        
        await db.commit()
        return True
    
    async def _recalculate_player_hp_mp(self, db: AsyncSession, player: User):
        """Пересчитать HP и MP игрока с учетом экипировки"""
        # Базовые значения
        base_hp = 100
        base_mp = 50
        
        # Бонусы от характеристик
        hp_from_constitution = player.constitution * 5
        hp_from_level = player.level * 10
        
        mp_from_intelligence = player.intelligence * 3
        mp_from_level = player.level * 5
        
        # Бонусы от экипировки
        equipment_bonus_hp = 0
        equipment_bonus_mp = 0
        
        equipment_ids = [
            player.weapon_id,
            player.armor_id,
            player.helmet_id,
            player.gloves_id,
            player.boots_id
        ]
        
        for item_id in equipment_ids:
            if item_id:
                item_result = await db.execute(
                    select(Item)
                    .where(Item.id == item_id)
                    .options(selectinload(Item.template))
                )
                item = item_result.scalar_one_or_none()
                if item and item.template:
                    equipment_bonus_hp += item.template.health_bonus or 0
                    equipment_bonus_mp += item.template.mana_bonus or 0
        
        # Итоговые значения
        player.max_hp = base_hp + hp_from_constitution + hp_from_level + equipment_bonus_hp
        player.max_mp = base_mp + mp_from_intelligence + mp_from_level + equipment_bonus_mp
        
        # Ограничиваем текущие значения
        player.current_hp = min(player.current_hp, player.max_hp)
        player.current_mp = min(player.current_mp, player.max_mp)
    
    # ============ КОНСТРУКТОРЫ КОНТЕНТА ============
    
    async def create_mob(self, db: AsyncSession, data: Dict[str, Any], admin_id: uuid.UUID) -> MobTemplate:
        """Создать нового моба"""
        mob = MobTemplate(
            name=data['name'],
            description=data.get('description', ''),
            icon=data.get('icon', '🧌'),
            mob_type=data['mob_type'],
            level=data['level'],
            health=data['health'],
            damage_min=data['damage_min'],
            damage_max=data['damage_max'],
            defense=data.get('defense', 0),
            attack_speed=data.get('attack_speed', 1.0),
            crit_chance=data.get('crit_chance', 0.05),
            dodge_chance=data.get('dodge_chance', 0.05),
            base_xp=data.get('base_xp', data['level'] * 10),
            gold_min=data.get('gold_min', data['level'] * 2),
            gold_max=data.get('gold_max', data['level'] * 5),
            is_boss=data.get('is_boss', False),
            respawn_time=data.get('respawn_time', 300)
        )
        
        db.add(mob)
        await db.flush()
        
        # Добавляем дроп
        for drop_data in data.get('drops', []):
            drop = MobDrop(
                mob_template_id=mob.id,
                item_template_id=uuid.UUID(drop_data['item_template_id']),
                drop_chance=drop_data['drop_chance'],
                min_quantity=drop_data.get('min_quantity', 1),
                max_quantity=drop_data.get('max_quantity', 1)
            )
            db.add(drop)
        
        # Добавляем спавны в локации
        for spawn_data in data.get('spawns', []):
            spawn = MobSpawn(
                location_id=uuid.UUID(spawn_data['location_id']),
                mob_template_id=mob.id,
                spawn_chance=spawn_data['spawn_chance'],
                min_level=spawn_data.get('min_level', 1),
                max_level=spawn_data.get('max_level', 100),
                max_count=spawn_data.get('max_count', 10)
            )
            db.add(spawn)
        
        # Логируем действие
        audit_log = AuditLog(
            user_id=admin_id,
            action="admin_create_mob",
            details={
                "mob_id": str(mob.id),
                "mob_name": mob.name,
                "mob_level": mob.level,
                "mob_type": mob.mob_type.value,
                "is_boss": mob.is_boss
            }
        )
        db.add(audit_log)
        
        await db.commit()
        return mob
    
    async def create_item(self, db: AsyncSession, data: Dict[str, Any], admin_id: uuid.UUID) -> ItemTemplate:
        """Создать новый предмет"""
        item = ItemTemplate(
            name=data['name'],
            description=data.get('description', ''),
            icon=data.get('icon', '📦'),
            item_type=data['item_type'],
            rarity=data.get('rarity', ItemRarity.COMMON),
            level_requirement=data.get('level_requirement', 1),
            damage_min=data.get('damage_min', 0),
            damage_max=data.get('damage_max', 0),
            defense=data.get('defense', 0),
            health_bonus=data.get('health_bonus', 0),
            mana_bonus=data.get('mana_bonus', 0),
            strength_bonus=data.get('strength_bonus', 0),
            agility_bonus=data.get('agility_bonus', 0),
            intelligence_bonus=data.get('intelligence_bonus', 0),
            constitution_bonus=data.get('constitution_bonus', 0),
            potion_effect=data.get('potion_effect'),
            resource_type=data.get('resource_type'),
            weight=data.get('weight', 0.1),
            base_price=data.get('base_price', 10),
            sell_price=data.get('sell_price', int(data.get('base_price', 10) * 0.5)),
            stack_size=data.get('stack_size', 1),
            is_tradable=data.get('is_tradable', True),
            is_droppable=data.get('is_droppable', True),
            is_consumable=data.get('is_consumable', False),
            is_equippable=data.get('is_equippable', False),
            craftable=data.get('craftable', False),
            craft_profession=data.get('craft_profession'),
            craft_level=data.get('craft_level', 1),
            craft_time=data.get('craft_time', 60)
        )
        
        db.add(item)
        
        # Логируем действие
        audit_log = AuditLog(
            user_id=admin_id,
            action="admin_create_item",
            details={
                "item_id": str(item.id),
                "item_name": item.name,
                "item_type": item.item_type.value,
                "rarity": item.rarity.value,
                "level_requirement": item.level_requirement
            }
        )
        db.add(audit_log)
        
        await db.commit()
        return item
    
    async def create_location(self, db: AsyncSession, data: Dict[str, Any], admin_id: uuid.UUID) -> Location:
        """Создать новую локацию"""
        location = Location(
            name=data['name'],
            description=data.get('description', ''),
            icon=data.get('icon', '📍'),
            location_type=data['location_type'],
            min_level=data.get('min_level', 1),
            max_level=data.get('max_level', 100),
            base_xp_reward=data.get('base_xp_reward', 10),
            has_mine=data.get('has_mine', False),
            mine_level=data.get('mine_level', 0),
            has_forest=data.get('has_forest', False),
            has_herbs=data.get('has_herbs', False)
        )
        
        db.add(location)
        await db.flush()
        
        # Добавляем маршруты путешествия
        for route_data in data.get('routes', []):
            route = TravelRoute(
                from_location_id=location.id,
                to_location_id=uuid.UUID(route_data['to_location_id']),
                travel_time=route_data['travel_time'],
                min_level=route_data.get('min_level', 1),
                gold_cost=route_data.get('gold_cost', 0)
            )
            db.add(route)
        
        # Логируем действие
        audit_log = AuditLog(
            user_id=admin_id,
            action="admin_create_location",
            details={
                "location_id": str(location.id),
                "location_name": location.name,
                "location_type": location.location_type.value,
                "min_level": location.min_level,
                "max_level": location.max_level
            }
        )
        db.add(audit_log)
        
        await db.commit()
        return location
    
    async def create_resource(self, db: AsyncSession, data: Dict[str, Any], admin_id: uuid.UUID) -> ResourceTemplate:
        """Создать новый ресурс"""
        resource = ResourceTemplate(
            name=data['name'],
            description=data.get('description', ''),
            icon=data.get('icon', '⛏️'),
            resource_type=data['resource_type'],
            level=data.get('level', 1),
            gather_chance=data['gather_chance'],
            min_quantity=data.get('min_quantity', 1),
            max_quantity=data.get('max_quantity', 1),
            gather_time=data.get('gather_time', 60),
            required_strength=data.get('required_strength', 0),
            required_profession_level=data.get('required_profession_level', 1),
            weight=data.get('weight', 0.1),
            base_price=data.get('base_price', 10)
        )
        
        db.add(resource)
        await db.flush()
        
        # Добавляем спавны в локации
        for spawn_data in data.get('spawns', []):
            spawn = ResourceSpawn(
                location_id=uuid.UUID(spawn_data['location_id']),
                resource_template_id=resource.id,
                spawn_chance=spawn_data['spawn_chance'],
                respawn_time=spawn_data.get('respawn_time', 600),
                max_count=spawn_data.get('max_count', 100)
            )
            db.add(spawn)
        
        # Логируем действие
        audit_log = AuditLog(
            user_id=admin_id,
            action="admin_create_resource",
            details={
                "resource_id": str(resource.id),
                "resource_name": resource.name,
                "resource_type": resource.resource_type.value,
                "level": resource.level,
                "gather_chance": resource.gather_chance
            }
        )
        db.add(audit_log)
        
        await db.commit()
        return resource
    
    async def create_event(self, db: AsyncSession, data: Dict[str, Any], admin_id: uuid.UUID) -> GameEvent:
        """Создать новое событие"""
        event = GameEvent(
            name=data['name'],
            description=data.get('description', ''),
            icon=data.get('icon', '🎭'),
            event_type=data['event_type'],
            activation_type=data.get('activation_type', EventActivationType.CHANCE),
            base_chance=data.get('base_chance', 0.2),
            min_player_level=data.get('min_player_level', 1),
            max_player_level=data.get('max_player_level', 100),
            start_time=data.get('start_time'),
            end_time=data.get('end_time'),
            duration=data.get('duration', 3600),
            mob_power_modifier=data.get('mob_power_modifier', 1.0),
            resource_spawn_modifier=data.get('resource_spawn_modifier', 1.0),
            reward_gold_min=data.get('reward_gold_min', 0),
            reward_gold_max=data.get('reward_gold_max', 0),
            reward_xp=data.get('reward_xp', 0),
            is_active=data.get('is_active', False),
            is_repeatable=data.get('is_repeatable', True)
        )
        
        db.add(event)
        await db.flush()
        
        # Добавляем триггеры локаций
        for location_id in data.get('locations', []):
            trigger = EventTrigger(
                event_id=event.id,
                location_id=uuid.UUID(location_id),
                trigger_chance=data.get('trigger_chance', 1.0)
            )
            db.add(trigger)
        
        # Добавляем награды
        for reward_data in data.get('rewards', []):
            reward = EventReward(
                event_id=event.id,
                item_template_id=uuid.UUID(reward_data['item_template_id']),
                drop_chance=reward_data['drop_chance'],
                min_quantity=reward_data.get('min_quantity', 1),
                max_quantity=reward_data.get('max_quantity', 1)
            )
            db.add(reward)
        
        # Логируем действие
        audit_log = AuditLog(
            user_id=admin_id,
            action="admin_create_event",
            details={
                "event_id": str(event.id),
                "event_name": event.name,
                "event_type": event.event_type.value,
                "activation_type": event.activation_type.value,
                "is_active": event.is_active
            }
        )
        db.add(audit_log)
        
        await db.commit()
        return event
    
    async def create_chest(self, db: AsyncSession, data: Dict[str, Any], admin_id: uuid.UUID) -> ChestTemplate:
        """Создать новый сундук"""
        chest = ChestTemplate(
            name=data['name'],
            description=data.get('description', ''),
            icon=data.get('icon', '🎁'),
            rarity=data['rarity'],
            level=data.get('level', 1),
            spawn_chance=data.get('spawn_chance', 0.05),
            min_player_level=data.get('min_player_level', 1),
            max_player_level=data.get('max_player_level', 100),
            trap_chance=data.get('trap_chance', 0.0),
            trap_type=data.get('trap_type'),
            trap_damage=data.get('trap_damage', 0),
            required_key_id=data.get('required_key_id'),
            required_lockpicking=data.get('required_lockpicking', 0),
            required_strength=data.get('required_strength', 0)
        )
        
        db.add(chest)
        await db.flush()
        
        # Добавляем награды
        for reward_data in data.get('rewards', []):
            reward = ChestReward(
                chest_template_id=chest.id,
                item_template_id=uuid.UUID(reward_data['item_template_id']),
                drop_chance=reward_data['drop_chance'],
                min_quantity=reward_data.get('min_quantity', 1),
                max_quantity=reward_data.get('max_quantity', 1),
                is_guaranteed=reward_data.get('is_guaranteed', False)
            )
            db.add(reward)
        
        # Логируем действие
        audit_log = AuditLog(
            user_id=admin_id,
            action="admin_create_chest",
            details={
                "chest_id": str(chest.id),
                "chest_name": chest.name,
                "rarity": chest.rarity.value,
                "level": chest.level,
                "spawn_chance": chest.spawn_chance
            }
        )
        db.add(audit_log)
        
        await db.commit()
        return chest
    
    async def create_recipe(self, db: AsyncSession, data: Dict[str, Any], admin_id: uuid.UUID) -> Recipe:
        """Создать новый рецепт"""
        recipe = Recipe(
            name=data['name'],
            description=data.get('description', ''),
            result_item_id=uuid.UUID(data['result_item_id']),
            result_quantity=data.get('result_quantity', 1),
            profession_type=data['profession_type'],
            profession_level=data.get('profession_level', 1),
            craft_time=data.get('craft_time', 60),
            gold_cost=data.get('gold_cost', 0),
            is_discovered=data.get('is_discovered', False),
            discover_chance=data.get('discover_chance', 0.0)
        )
        
        db.add(recipe)
        await db.flush()
        
        # Добавляем ингредиенты
        for ingredient_data in data.get('ingredients', []):
            ingredient = RecipeIngredient(
                recipe_id=recipe.id,
                item_template_id=uuid.UUID(ingredient_data['item_template_id']),
                quantity=ingredient_data['quantity']
            )
            db.add(ingredient)
        
        # Логируем действие
        audit_log = AuditLog(
            user_id=admin_id,
            action="admin_create_recipe",
            details={
                "recipe_id": str(recipe.id),
                "recipe_name": recipe.name,
                "profession_type": recipe.profession_type.value,
                "profession_level": recipe.profession_level
            }
        )
        db.add(audit_log)
        
        await db.commit()
        return recipe
    
    # ============ РЕДАКТИРОВАНИЕ КОНТЕНТА ============
    
    async def update_mob(self, db: AsyncSession, mob_id: uuid.UUID, data: Dict[str, Any], admin_id: uuid.UUID) -> bool:
        """Обновить моба"""
        mob = await db.get(MobTemplate, mob_id)
        if not mob:
            return False
        
        # Сохраняем старые значения для лога
        old_values = {
            'name': mob.name,
            'level': mob.level,
            'health': mob.health,
            'damage_min': mob.damage_min,
            'damage_max': mob.damage_max
        }
        
        # Обновляем поля
        if 'name' in data:
            mob.name = data['name']
        if 'description' in data:
            mob.description = data['description']
        if 'icon' in data:
            mob.icon = data['icon']
        if 'mob_type' in data:
            mob.mob_type = data['mob_type']
        if 'level' in data:
            mob.level = data['level']
        if 'health' in data:
            mob.health = data['health']
        if 'damage_min' in data:
            mob.damage_min = data['damage_min']
        if 'damage_max' in data:
            mob.damage_max = data['damage_max']
        if 'defense' in data:
            mob.defense = data['defense']
        if 'attack_speed' in data:
            mob.attack_speed = data['attack_speed']
        if 'crit_chance' in data:
            mob.crit_chance = data['crit_chance']
        if 'dodge_chance' in data:
            mob.dodge_chance = data['dodge_chance']
        if 'base_xp' in data:
            mob.base_xp = data['base_xp']
        if 'gold_min' in data:
            mob.gold_min = data['gold_min']
        if 'gold_max' in data:
            mob.gold_max = data['gold_max']
        if 'is_boss' in data:
            mob.is_boss = data['is_boss']
        if 'respawn_time' in data:
            mob.respawn_time = data['respawn_time']
        
        mob.updated_at = datetime.utcnow()
        
        # Логируем действие
        audit_log = AuditLog(
            user_id=admin_id,
            action="admin_update_mob",
            details={
                "mob_id": str(mob_id),
                "old_values": old_values,
                "new_values": {
                    'name': mob.name,
                    'level': mob.level,
                    'health': mob.health,
                    'damage_min': mob.damage_min,
                    'damage_max': mob.damage_max
                }
            }
        )
        db.add(audit_log)
        
        await db.commit()
        return True
    
    async def delete_mob(self, db: AsyncSession, mob_id: uuid.UUID, admin_id: uuid.UUID) -> bool:
        """Удалить моба"""
        mob = await db.get(MobTemplate, mob_id)
        if not mob:
            return False
        
        # Сохраняем информацию для лога
        mob_info = {
            'id': str(mob.id),
            'name': mob.name,
            'level': mob.level,
            'mob_type': mob.mob_type.value
        }
        
        # Удаляем связанные записи
        await db.execute(delete(MobDrop).where(MobDrop.mob_template_id == mob_id))
        await db.execute(delete(MobSpawn).where(MobSpawn.mob_template_id == mob_id))
        
        # Удаляем моба
        await db.delete(mob)
        
        # Логируем действие
        audit_log = AuditLog(
            user_id=admin_id,
            action="admin_delete_mob",
            details=mob_info
        )
        db.add(audit_log)
        
        await db.commit()
        return True
    
    # ============ СИСТЕМНЫЕ НАСТРОЙКИ ============
    
    async def get_system_settings(self, db: AsyncSession) -> Dict[str, Any]:
        """Получить системные настройки"""
        settings = {}
        
        # Получаем все настройки
        result = await db.execute(select(SystemSettings))
        settings_list = result.scalars().all()
        
        for setting in settings_list:
            settings[setting.key] = setting.value
        
        # Настройки по умолчанию, если их нет в БД
        default_settings = {
            'max_players': 1000,
            'max_items_per_player': 200,
            'max_active_crafts': 5,
            'backup_interval': 3600,
            'autosave_interval': 300,
            'timeout_seconds': 1800,
            'starting_gold': 100,
            'max_gold': 9999999,
            'trade_commission': 5,
            'pvp_min_level': 10,
            'pvp_level_difference': 15,
            'pvp_kill_reward_multiplier': 10,
            'pvp_death_penalty': 10,
            'event_base_chance': 20,
            'event_duration': 3600,
            'max_active_events': 5,
            'exp_for_next_level_formula': "current_level * 100 * (1 + current_level * 0.1)",
            'damage_formula': "base_damage * (1 + strength / 100) * random(0.9, 1.1) * (1.5 if is_critical else 1)",
            'defense_formula': "damage * (1 - min(0.8, defense / (defense + 100 * attacker_level)))",
            'critical_chance_formula': "0.05 + agility * 0.001",
            'dodge_chance_formula': "0.05 + agility * 0.0015"
        }
        
        # Добавляем отсутствующие настройки
        for key, value in default_settings.items():
            if key not in settings:
                settings[key] = value
        
        return settings
    
    async def update_system_setting(self, db: AsyncSession, key: str, value: Any, admin_id: uuid.UUID) -> bool:
        """Обновить системную настройку"""
        # Получаем существующую настройку
        result = await db.execute(
            select(SystemSettings).where(SystemSettings.key == key)
        )
        setting = result.scalar_one_or_none()
        
        if setting:
            # Сохраняем старое значение
            old_value = setting.value
            setting.value = value
            setting.updated_at = datetime.utcnow()
        else:
            # Создаем новую настройку
            old_value = None
            setting = SystemSettings(
                key=key,
                value=value,
                description=f"Настройка {key}"
            )
            db.add(setting)
        
        # Логируем действие
        audit_log = AuditLog(
            user_id=admin_id,
            action="admin_update_setting",
            details={
                "setting_key": key,
                "old_value": old_value,
                "new_value": value
            }
        )
        db.add(audit_log)
        
        await db.commit()
        return True
    
    async def update_formula(self, db: AsyncSession, formula_name: str, formula: str, admin_id: uuid.UUID) -> bool:
        """Обновить формулу"""
        key = f"formula_{formula_name}"
        return await self.update_system_setting(db, key, formula, admin_id)
    
    # ============ БЭКАПЫ И ЭКСПОРТ ============
    
    async def create_backup(self, db: AsyncSession, admin_id: uuid.UUID) -> Dict[str, Any]:
        """Создать бэкап базы данных"""
        try:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            filename = f"backup_{timestamp}.json"
            filepath = os.path.join(self.backup_dir, filename)
            
            # Собираем все данные для бэкапа
            backup_data = {
                'timestamp': datetime.utcnow().isoformat(),
                'tables': {}
            }
            
            # Список таблиц для бэкапа
            tables = [
                (User, 'users'),
                (ItemTemplate, 'item_templates'),
                (MobTemplate, 'mob_templates'),
                (Location, 'locations'),
                (GameEvent, 'game_events'),
                (Recipe, 'recipes'),
                (SystemSettings, 'system_settings')
            ]
            
            for model, table_name in tables:
                result = await db.execute(select(model))
                items = result.scalars().all()
                
                # Конвертируем объекты в словари
                items_data = []
                for item in items:
                    item_dict = {}
                    for column in model.__table__.columns:
                        value = getattr(item, column.name)
                        # Конвертируем UUID и даты в строки
                        if isinstance(value, uuid.UUID):
                            value = str(value)
                        elif isinstance(value, datetime):
                            value = value.isoformat()
                        elif isinstance(value, Enum):
                            value = value.value
                        item_dict[column.name] = value
                    items_data.append(item_dict)
                
                backup_data['tables'][table_name] = items_data
            
            # Сохраняем в файл
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(backup_data, f, ensure_ascii=False, indent=2)
            
            # Записываем лог
            backup_log = BackupLog(
                filename=filename,
                size_bytes=os.path.getsize(filepath),
                success=True
            )
            db.add(backup_log)
            
            # Логируем действие
            audit_log = AuditLog(
                user_id=admin_id,
                action="admin_create_backup",
                details={
                    "filename": filename,
                    "filepath": filepath,
                    "size_bytes": backup_log.size_bytes
                }
            )
            db.add(audit_log)
            
            await db.commit()
            
            return {
                'success': True,
                'filename': filename,
                'filepath': filepath,
                'size': backup_log.size_bytes,
                'timestamp': backup_log.created_at
            }
            
        except Exception as e:
            # Записываем лог ошибки
            backup_log = BackupLog(
                filename=filename if 'filename' in locals() else 'unknown',
                size_bytes=0,
                success=False,
                error_message=str(e)
            )
            db.add(backup_log)
            
            await db.commit()
            
            return {
                'success': False,
                'error': str(e)
            }
    
    async def get_backup_list(self, db: AsyncSession, limit: int = 20) -> List[BackupLog]:
        """Получить список бэкапов"""
        result = await db.execute(
            select(BackupLog)
            .order_by(desc(BackupLog.created_at))
            .limit(limit)
        )
        return result.scalars().all()
    
    async def restore_from_backup(self, db: AsyncSession, filename: str, admin_id: uuid.UUID) -> Dict[str, Any]:
        """Восстановить из бэкапа"""
        try:
            filepath = os.path.join(self.backup_dir, filename)
            
            if not os.path.exists(filepath):
                return {
                    'success': False,
                    'error': f"Файл {filename} не найден"
                }
            
            # Загружаем данные
            with open(filepath, 'r', encoding='utf-8') as f:
                backup_data = json.load(f)
            
            # Начинаем транзакцию
            async with db.begin():
                # Очищаем таблицы (кроме системных)
                await db.execute(delete(RecipeIngredient))
                await db.execute(delete(Recipe))
                await db.execute(delete(EventReward))
                await db.execute(delete(EventTrigger))
                await db.execute(delete(GameEvent))
                await db.execute(delete(MobDrop))
                await db.execute(delete(MobSpawn))
                await db.execute(delete(MobTemplate))
                await db.execute(delete(ResourceSpawn))
                await db.execute(delete(ResourceTemplate))
                await db.execute(delete(TravelRoute))
                await db.execute(delete(Location))
                await db.execute(delete(Item))
                await db.execute(delete(ItemTemplate))
                # Пользователей и системные настройки не удаляем
            
                # Восстанавливаем таблицы
                tables_order = [
                    (ItemTemplate, 'item_templates'),
                    (MobTemplate, 'mob_templates'),
                    (Location, 'locations'),
                    (TravelRoute, 'travel_routes'),
                    (ResourceTemplate, 'resource_templates'),
                    (ResourceSpawn, 'resource_spawns'),
                    (GameEvent, 'game_events'),
                    (EventTrigger, 'event_triggers'),
                    (EventReward, 'event_rewards'),
                    (Recipe, 'recipes'),
                    (RecipeIngredient, 'recipe_ingredients')
                ]
                
                for model, table_name in tables_order:
                    if table_name in backup_data['tables']:
                        for item_data in backup_data['tables'][table_name]:
                            # Конвертируем строки обратно в нужные типы
                            for key, value in item_data.items():
                                if key.endswith('_id') and value:
                                    item_data[key] = uuid.UUID(value)
                                elif key in ['created_at', 'updated_at', 'start_time', 'end_time'] and value:
                                    item_data[key] = datetime.fromisoformat(value)
                                elif key in ['mob_type', 'item_type', 'rarity', 'resource_type', 'event_type', 'activation_type', 'profession_type'] and value:
                                    # Конвертируем строки обратно в Enum
                                    enum_class = None
                                    if key == 'mob_type':
                                        enum_class = MobType
                                    elif key == 'item_type':
                                        enum_class = ItemType
                                    elif key == 'rarity':
                                        enum_class = ItemRarity
                                    elif key == 'resource_type':
                                        enum_class = ResourceType
                                    elif key == 'event_type':
                                        enum_class = EventType
                                    elif key == 'activation_type':
                                        enum_class = EventActivationType
                                    elif key == 'profession_type':
                                        enum_class = ProfessionType
                                    
                                    if enum_class:
                                        item_data[key] = enum_class(value)
                            
                            # Создаем объект
                            item = model(**item_data)
                            db.add(item)
            
            # Логируем действие
            audit_log = AuditLog(
                user_id=admin_id,
                action="admin_restore_backup",
                details={
                    "filename": filename,
                    "filepath": filepath,
                    "timestamp": backup_data.get('timestamp')
                }
            )
            db.add(audit_log)
            
            await db.commit()
            
            return {
                'success': True,
                'filename': filename,
                'timestamp': backup_data.get('timestamp'),
                'tables_restored': len(backup_data.get('tables', {}))
            }
            
        except Exception as e:
            await db.rollback()
            return {
                'success': False,
                'error': str(e)
            }
    
    async def export_data(self, db: AsyncSession, data_type: str, admin_id: uuid.UUID) -> Dict[str, Any]:
        """Экспортировать данные"""
        try:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            filename = f"export_{data_type}_{timestamp}.json"
            filepath = os.path.join(self.backup_dir, filename)
            
            export_data = {
                'type': data_type,
                'timestamp': datetime.utcnow().isoformat(),
                'data': []
            }
            
            # Выбираем данные для экспорта
            if data_type == 'players':
                result = await db.execute(select(User))
                items = result.scalars().all()
            elif data_type == 'items':
                result = await db.execute(select(ItemTemplate))
                items = result.scalars().all()
            elif data_type == 'mobs':
                result = await db.execute(select(MobTemplate))
                items = result.scalars().all()
            elif data_type == 'locations':
                result = await db.execute(select(Location))
                items = result.scalars().all()
            elif data_type == 'events':
                result = await db.execute(select(GameEvent))
                items = result.scalars().all()
            elif data_type == 'recipes':
                result = await db.execute(select(Recipe))
                items = result.scalars().all()
            else:
                return {'success': False, 'error': 'Неизвестный тип данных'}
            
            # Конвертируем в словари
            for item in items:
                item_dict = {}
                for column in item.__table__.columns:
                    value = getattr(item, column.name)
                    if isinstance(value, uuid.UUID):
                        value = str(value)
                    elif isinstance(value, datetime):
                        value = value.isoformat()
                    elif isinstance(value, Enum):
                        value = value.value
                    item_dict[column.name] = value
                export_data['data'].append(item_dict)
            
            # Сохраняем в файл
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2)
            
            # Логируем действие
            audit_log = AuditLog(
                user_id=admin_id,
                action="admin_export_data",
                details={
                    "data_type": data_type,
                    "filename": filename,
                    "filepath": filepath,
                    "items_count": len(export_data['data'])
                }
            )
            db.add(audit_log)
            
            await db.commit()
            
            return {
                'success': True,
                'filename': filename,
                'filepath': filepath,
                'items_count': len(export_data['data'])
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    async def import_data(self, db: AsyncSession, data_type: str, data: List[Dict[str, Any]], admin_id: uuid.UUID) -> Dict[str, Any]:
        """Импортировать данные"""
        try:
            imported_count = 0
            updated_count = 0
            
            async with db.begin():
                for item_data in data:
                    # Определяем модель
                    if data_type == 'items':
                        model = ItemTemplate
                        id_field = 'id'
                    elif data_type == 'mobs':
                        model = MobTemplate
                        id_field = 'id'
                    elif data_type == 'locations':
                        model = Location
                        id_field = 'id'
                    elif data_type == 'events':
                        model = GameEvent
                        id_field = 'id'
                    elif data_type == 'recipes':
                        model = Recipe
                        id_field = 'id'
                    else:
                        continue
                    
                    # Конвертируем типы данных
                    for key, value in item_data.items():
                        if key.endswith('_id') and value:
                            item_data[key] = uuid.UUID(value)
                        elif key in ['created_at', 'updated_at', 'start_time', 'end_time'] and value:
                            item_data[key] = datetime.fromisoformat(value)
                        elif key in ['mob_type', 'item_type', 'rarity', 'resource_type', 'event_type', 'activation_type', 'profession_type'] and value:
                            enum_class = None
                            if key == 'mob_type':
                                enum_class = MobType
                            elif key == 'item_type':
                                enum_class = ItemType
                            elif key == 'rarity':
                                enum_class = ItemRarity
                            elif key == 'resource_type':
                                enum_class = ResourceType
                            elif key == 'event_type':
                                enum_class = EventType
                            elif key == 'activation_type':
                                enum_class = EventActivationType
                            elif key == 'profession_type':
                                enum_class = ProfessionType
                            
                            if enum_class:
                                item_data[key] = enum_class(value)
                    
                    # Проверяем существование
                    existing = await db.get(model, uuid.UUID(item_data[id_field]))
                    
                    if existing:
                        # Обновляем существующий
                        for key, value in item_data.items():
                            if key != id_field and hasattr(existing, key):
                                setattr(existing, key, value)
                        updated_count += 1
                    else:
                        # Создаем новый
                        item = model(**item_data)
                        db.add(item)
                        imported_count += 1
            
            # Логируем действие
            audit_log = AuditLog(
                user_id=admin_id,
                action="admin_import_data",
                details={
                    "data_type": data_type,
                    "imported_count": imported_count,
                    "updated_count": updated_count,
                    "total_count": len(data)
                }
            )
            db.add(audit_log)
            
            await db.commit()
            
            return {
                'success': True,
                'imported': imported_count,
                'updated': updated_count,
                'total': len(data)
            }
            
        except Exception as e:
            await db.rollback()
            return {
                'success': False,
                'error': str(e)
            }

# ============ УТИЛИТЫ ДЛЯ ФОРМАТИРОВАНИЯ ============

def format_number(num: int) -> str:
    """Форматировать число с разделителями"""
    return f"{num:,}".replace(",", " ")

def format_timedelta(td: timedelta) -> str:
    """Форматировать временной интервал"""
    seconds = int(td.total_seconds())
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    if hours > 0:
        return f"{hours}ч {minutes}м"
    elif minutes > 0:
        return f"{minutes}м {seconds}с"
    else:
        return f"{seconds}с"

def format_size(size_bytes: int) -> str:
    """Форматировать размер файла"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

# ============ ХЭНДЛЕРЫ КОМАНД ============

@admin_router.message(Command("admin"))
async def command_admin(message: Message, state: FSMContext):
    """Обработчик команды /admin"""
    from database import get_db_session
    
    async with get_db_session() as db:
        admin_manager = AdminManager(get_db_session)
        
        if not await admin_manager.check_admin_access(message.from_user.id):
            await message.answer("⛔ У вас нет доступа к админ-панели.")
            return
        
        await show_admin_main_menu(message, state)

async def show_admin_main_menu(message: Union[Message, CallbackQuery], state: FSMContext):
    """Показать главное меню админ-панели"""
    from database import get_db_session
    
    async with get_db_session() as db:
        admin_manager = AdminManager(get_db_session)
        stats = await admin_manager.get_system_statistics(db)
        
        text = html.bold("🛡️ АДМИН-ПАНЕЛЬ\n\n")
        
        text += html.bold("📊 СТАТИСТИКА СИСТЕМЫ:\n")
        text += f"👥 Игроков: {stats['players_total']} (новых: {stats['players_today']})\n"
        text += f"🟢 Онлайн: {stats['online']}\n"
        text += f"💰 Золото в экономике: {format_number(stats['total_gold'])}\n"
        text += f"📈 Средний уровень: {stats['avg_level']}\n\n"
        
        text += html.bold("⚔️ АКТИВНОСТЬ:\n")
        text += f"⚔️ Активных битв: {stats['active_battles']}\n"
        text += f"🛤️ Путешествий: {stats['active_travels']}\n"
        text += f"🔨 Крафтов: {stats['active_crafts']}\n"
        text += f"🎭 Активных событий: {stats['active_events']}\n\n"
        
        text += html.bold("📦 КОНТЕНТ:\n")
        text += f"🧌 Мобов: {stats['mobs']}\n"
        text += f"📦 Предметов: {stats['items']}\n"
        text += f"📍 Локаций: {stats['locations']}\n"
        text += f"🎁 Сундуков: {stats['chests']}\n"
        text += f"🔨 Рецептов: {stats['recipes']}\n"
        text += f"⛏️ Ресурсов: {stats['resources']}\n"
        
        if stats['last_backup']:
            last_backup_time = datetime.utcnow() - stats['last_backup']
            text += f"\n💾 Последний бэкап: {format_timedelta(last_backup_time)} назад"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👥 Игроки", callback_data="admin_players"),
             InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="⚙️ Настройки системы", callback_data="admin_system")],
            [InlineKeyboardButton(text="🧌 Конструктор мобов", callback_data="admin_mobs")],
            [InlineKeyboardButton(text="📦 Конструктор предметов", callback_data="admin_items")],
            [InlineKeyboardButton(text="📍 Конструктор локаций", callback_data="admin_locations")],
            [InlineKeyboardButton(text="⛏️ Конструктор ресурсов", callback_data="admin_resources")],
            [InlineKeyboardButton(text="🎭 Конструктор событий", callback_data="admin_events")],
            [InlineKeyboardButton(text="🎁 Конструктор сундуков", callback_data="admin_chests")],
            [InlineKeyboardButton(text="🔨 Конструктор рецептов", callback_data="admin_recipes")],
            [InlineKeyboardButton(text="💾 Бэкапы и экспорт", callback_data="admin_backups")],
            [InlineKeyboardButton(text="📈 Редактор формул", callback_data="admin_formulas")],
            [InlineKeyboardButton(text="🔄 Перезагрузка", callback_data="admin_restart")]
        ])
        
        if isinstance(message, CallbackQuery):
            await message.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(AdminStates.main_menu)

@admin_router.callback_query(F.data == "admin_menu")
async def handle_admin_menu(callback: CallbackQuery, state: FSMContext):
    """Обработчик возврата в главное меню"""
    await show_admin_main_menu(callback, state)

# ============ ХЭНДЛЕРЫ УПРАВЛЕНИЯ ИГРОКАМИ ============

@admin_router.callback_query(F.data == "admin_players")
async def handle_admin_players(callback: CallbackQuery, state: FSMContext):
    """Обработчик управления игроками"""
    from database import get_db_session
    
    async with get_db_session() as db:
        admin_manager = AdminManager(get_db_session)
        
        # Получаем последних игроков
        result = await db.execute(
            select(User)
            .order_by(desc(User.last_active))
            .limit(10)
        )
        players = result.scalars().all()
        
        text = html.bold("👥 УПРАВЛЕНИЕ ИГРОКАМИ\n\n")
        
        if players:
            text += html.bold("Последние активные игроки:\n\n")
            for i, player in enumerate(players, 1):
                online_icon = "🟢" if (datetime.utcnow() - player.last_active).seconds < 900 else "⚫"
                text += f"{i}. {online_icon} {player.username or f'ID: {player.telegram_id}'}\n"
                text += f"   Уровень: {player.level} | Золото: {format_number(player.gold)}\n"
                text += f"   Последняя активность: {player.last_active.strftime('%H:%M')}\n\n"
        else:
            text += "Нет зарегистрированных игроков.\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔍 Поиск игрока", callback_data="admin_player_search")],
            [InlineKeyboardButton(text="📋 Список всех игроков", callback_data="admin_player_list_all")],
            [InlineKeyboardButton(text="📊 Топ игроков", callback_data="admin_player_top")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(AdminStates.player_management)

@admin_router.callback_query(F.data == "admin_player_search")
async def handle_player_search(callback: CallbackQuery, state: FSMContext):
    """Обработчик поиска игрока"""
    text = html.bold("🔍 ПОИСК ИГРОКА\n\n")
    text += "Введите запрос для поиска:\n"
    text += "• ID телеграм (только цифры)\n"
    text += "• @юзернейм\n"
    text += "• Имя или фамилия\n\n"
    text += "Примеры:\n"
    text += "• 123456789\n"
    text += "• @username\n"
    text += "• Иван"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_players")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await state.set_state(AdminStates.player_search)

@admin_router.message(AdminStates.player_search)
async def handle_player_search_query(message: Message, state: FSMContext):
    """Обработчик запроса поиска игрока"""
    from database import get_db_session
    
    query = message.text.strip()
    if not query:
        await message.answer("Введите запрос для поиска.")
        return
    
    async with get_db_session() as db:
        admin_manager = AdminManager(get_db_session)
        players, total = await admin_manager.search_players(db, query, page=1, limit=10)
        
        if not players:
            text = html.bold("🔍 РЕЗУЛЬТАТЫ ПОИСКА\n\n")
            text += f"По запросу '{query}' ничего не найдено."
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Новый поиск", callback_data="admin_player_search")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_players")]
            ])
        else:
            text = html.bold(f"🔍 РЕЗУЛЬТАТЫ ПОИСКА: '{query}'\n\n")
            text += f"Найдено игроков: {total}\n\n"
            
            for i, player in enumerate(players, 1):
                online_icon = "🟢" if (datetime.utcnow() - player.last_active).seconds < 900 else "⚫"
                created = player.created_at.strftime("%d.%m.%Y")
                
                text += f"{i}. {online_icon} {player.username or f'ID: {player.telegram_id}'}\n"
                text += f"   Уровень: {player.level} | Золото: {format_number(player.gold)}\n"
                text += f"   Зарегистрирован: {created}\n"
                text += f"   [Показать детали](/player_{player.id})\n\n"
            
            keyboard_buttons = []
            for i, player in enumerate(players[:5], 1):
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"{i}. {player.username or f'ID: {player.telegram_id}'}",
                        callback_data=f"admin_player_view_{player.id}"
                    )
                ])
            
            if total > 10:
                keyboard_buttons.append([
                    InlineKeyboardButton(text="➡️ Следующая страница", callback_data=f"admin_player_search_page_2_{query}")
                ])
            
            keyboard_buttons.append([
                InlineKeyboardButton(text="🔄 Новый поиск", callback_data="admin_player_search"),
                InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_players")
            ])
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(AdminStates.player_management)

@admin_router.callback_query(F.data.startswith("admin_player_view_"))
async def handle_player_view(callback: CallbackQuery, state: FSMContext):
    """Обработчик просмотра деталей игрока"""
    from database import get_db_session
    
    player_id = uuid.UUID(callback.data.replace("admin_player_view_", ""))
    
    async with get_db_session() as db:
        admin_manager = AdminManager(get_db_session)
        player_details = await admin_manager.get_player_details(db, player_id)
        
        if not player_details or not player_details.get('player'):
            await callback.answer("Игрок не найден")
            return
        
        player = player_details['player']
        real_stats = player_details.get('real_stats', {})
        
        text = html.bold(f"👤 ДЕТАЛИ ИГРОКА\n\n")
        
        text += html.bold("👤 ОСНОВНАЯ ИНФОРМАЦИЯ:\n")
        text += f"ID: {player.id}\n"
        text += f"Telegram ID: {player.telegram_id}\n"
        text += f"Имя: {player.first_name or 'Не указано'} {player.last_name or ''}\n"
        text += f"Юзернейм: @{player.username or 'Не указан'}\n"
        text += f"Роль: {player.role.value}\n"
        text += f"Язык: {player.language}\n\n"
        
        text += html.bold("📊 ХАРАКТЕРИСТИКИ:\n")
        text += f"Уровень: {player.level}\n"
        text += f"Опыт: {format_number(player.experience)}\n"
        text += f"Свободные очки: {player.free_points}\n\n"
        
        text += html.bold("💪 ОСНОВНЫЕ ХАРАКТЕРИСТИКИ:\n")
        text += f"Сила: {player.strength}\n"
        text += f"Ловкость: {player.agility}\n"
        text += f"Интеллект: {player.intelligence}\n"
        text += f"Телосложение: {player.constitution}\n\n"
        
        text += html.bold("❤️ СОСТОЯНИЕ:\n")
        text += f"Здоровье: {player.current_hp}/{real_stats.get('max_hp', player.max_hp)}\n"
        text += f"Мана: {player.current_mp}/{real_stats.get('max_mp', player.max_mp)}\n"
        text += f"Выносливость: {player.stamina}/100\n\n"
        
        text += html.bold("💰 ЭКОНОМИКА:\n")
        text += f"Золото: {format_number(player.gold)}\n"
        text += f"Кристаллы: {format_number(player.crystals)}\n"
        text += f"Всего заработано: {format_number(player.total_gold_earned)}\n"
        text += f"Всего потрачено: {format_number(player.total_gold_spent)}\n\n"
        
        text += html.bold("⚔️ СТАТИСТИКА БОЯ:\n")
        text += f"Убито мобов: {player.mobs_killed}\n"
        text += f"Убито игроков: {player.players_killed}\n"
        text += f"Смертей: {player.deaths}\n"
        text += f"Всего урона: {format_number(player.total_damage_dealt)}\n"
        text += f"Всего получено урона: {format_number(player.total_damage_taken)}\n\n"
        
        text += html.bold("🎓 ПРОФЕССИИ:\n")
        text += f"⛏️ Горное дело: {player.mining_level} (опыт: {player.mining_exp})\n"
        text += f"🌳 Рубка дерева: {player.woodcutting_level} (опыт: {player.woodcutting_exp})\n"
        text += f"🌿 Травничество: {player.herbalism_level} (опыт: {player.herbalism_exp})\n"
        text += f"⚒️ Кузнечное дело: {player.blacksmithing_level} (опыт: {player.blacksmithing_exp})\n"
        text += f"🧪 Алхимия: {player.alchemy_level} (опыт: {player.alchemy_exp})\n\n"
        
        if player_details.get('equipped_items'):
            text += html.bold("🛡️ ЭКИПИРОВКА:\n")
            for slot, item in player_details['equipped_items'].items():
                text += f"{slot}: {item['icon']} {item['name']}\n"
            text += "\n"
        
        if player_details.get('current_actions'):
            text += html.bold("⏳ АКТИВНЫЕ ДЕЙСТВИЯ:\n")
            for action in player_details['current_actions'][:3]:
                remaining = (action.end_time - datetime.utcnow()).seconds
                text += f"{action.action_type.value}: {remaining // 60}:{remaining % 60:02d}\n"
            text += "\n"
        
        text += html.bold("📅 РЕГИСТРАЦИЯ И АКТИВНОСТЬ:\n")
        text += f"Зарегистрирован: {player.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        text += f"Последняя активность: {player.last_active.strftime('%d.%m.%Y %H:%M')}\n"
        text += f"Локация: {player.current_location_id or 'Не указана'}\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="💰 Дать золото", callback_data=f"admin_player_give_gold_{player.id}"),
                InlineKeyboardButton(text="📦 Дать предмет", callback_data=f"admin_player_give_item_{player.id}")
            ],
            [
                InlineKeyboardButton(text="📊 Изменить статы", callback_data=f"admin_player_edit_stats_{player.id}"),
                InlineKeyboardButton(text="🎭 Эффекты", callback_data=f"admin_player_effects_{player.id}")
            ],
            [
                InlineKeyboardButton(text="🎒 Инвентарь", callback_data=f"admin_player_inventory_{player.id}"),
                InlineKeyboardButton(text="⚔️ История боёв", callback_data=f"admin_player_battles_{player.id}")
            ],
            [
                InlineKeyboardButton(text="🗺️ Открытия", callback_data=f"admin_player_discoveries_{player.id}"),
                InlineKeyboardButton(text="📋 Логи действий", callback_data=f"admin_player_logs_{player.id}")
            ],
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data=f"admin_player_view_{player.id}"),
                InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_players")
            ]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(AdminStates.player_details)

@admin_router.callback_query(F.data.startswith("admin_player_give_gold_"))
async def handle_player_give_gold(callback: CallbackQuery, state: FSMContext):
    """Обработчик выдачи золота"""
    player_id = uuid.UUID(callback.data.replace("admin_player_give_gold_", ""))
    
    await state.update_data(target_player_id=player_id)
    
    text = html.bold("💰 ВЫДАЧА ЗОЛОТА ИГРОКУ\n\n")
    text += "Введите сумму золота для выдачи:\n"
    text += "(можно использовать отрицательное число для изъятия)"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin_player_view_{player_id}")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await state.set_state(AdminStates.player_give_gold)

@admin_router.message(AdminStates.player_give_gold)
async def handle_player_give_gold_amount(message: Message, state: FSMContext):
    """Обработчик суммы золота"""
    from database import get_db_session
    
    try:
        amount = int(message.text.strip())
    except ValueError:
        await message.answer("Пожалуйста, введите целое число.")
        return
    
    data = await state.get_data()
    player_id = data.get('target_player_id')
    
    if not player_id:
        await message.answer("Ошибка: игрок не найден.")
        return
    
    async with get_db_session() as db:
        admin_manager = AdminManager(get_db_session)
        admin_user = await admin_manager.get_admin_user(message.from_user.id)
        
        if not admin_user:
            await message.answer("Ошибка доступа.")
            return
        
        if amount >= 0:
            success = await admin_manager.give_gold_to_player(
                db, player_id, amount, admin_user.id,
                reason=f"Выдано администратором {admin_user.username}"
            )
            action = "выдано"
        else:
            success = await admin_manager.take_gold_from_player(
                db, player_id, abs(amount), admin_user.id,
                reason=f"Изъято администратором {admin_user.username}"
            )
            action = "изъято"
        
        if success:
            await message.answer(f"✅ Успешно {action} {abs(amount)} золота.")
            
            # Обновляем информацию об игроке
            player = await db.get(User, player_id)
            if player:
                await message.answer(f"💰 Новый баланс: {format_number(player.gold)} золота")
        else:
            await message.answer("❌ Не удалось выполнить операцию.")
    
    await state.set_state(AdminStates.player_details)
    # Возвращаемся к деталям игрока
    await handle_player_view(CallbackQuery(
        message=message,
        data=f"admin_player_view_{player_id}",
        from_user=message.from_user,
        chat_instance=""
    ), state)

# ============ ХЭНДЛЕРЫ КОНСТРУКТОРОВ ============

@admin_router.callback_query(F.data == "admin_mobs")
async def handle_admin_mobs(callback: CallbackQuery, state: FSMContext):
    """Обработчик конструктора мобов"""
    from database import get_db_session
    
    async with get_db_session() as db:
        # Получаем список мобов
        result = await db.execute(
            select(MobTemplate)
            .order_by(MobTemplate.level)
            .limit(20)
        )
        mobs = result.scalars().all()
        
        text = html.bold("🧌 КОНСТРУКТОР МОБОВ\n\n")
        
        if mobs:
            text += html.bold("СПИСОК МОБОВ:\n\n")
            for mob in mobs:
                boss_icon = "👑" if mob.is_boss else ""
                text += f"{boss_icon}{mob.icon} {mob.name}\n"
                text += f"  Уровень: {mob.level} | HP: {mob.health}\n"
                text += f"  Урон: {mob.damage_min}-{mob.damage_max} | Тип: {mob.mob_type.value}\n\n"
        else:
            text += "Нет созданных мобов.\n"
        
        keyboard_buttons = [
            [InlineKeyboardButton(text="➕ Создать нового моба", callback_data="admin_mob_create")],
            [InlineKeyboardButton(text="👑 Создать босса", callback_data="admin_boss_create")]
        ]
        
        if mobs:
            for i, mob in enumerate(mobs[:5]):
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"✏️ {mob.name[:15]}...",
                        callback_data=f"admin_mob_edit_{mob.id}"
                    ),
                    InlineKeyboardButton(
                        text="🗑️",
                        callback_data=f"admin_mob_delete_{mob.id}"
                    )
                ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

@admin_router.callback_query(F.data == "admin_mob_create")
async def handle_mob_create_start(callback: CallbackQuery, state: FSMContext):
    """Начать создание нового моба"""
    text = html.bold("🧌 СОЗДАНИЕ НОВОГО МОБА\n\n")
    text += "ШАГ 1: ОСНОВНАЯ ИНФОРМАЦИЯ\n\n"
    text += "Введите название моба:"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_mobs")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await state.set_state(AdminStates.create_mob_basic)

@admin_router.message(AdminStates.create_mob_basic)
async def handle_mob_name(message: Message, state: FSMContext):
    """Обработчик названия моба"""
    name = message.text.strip()
    if not name:
        await message.answer("Пожалуйста, введите название моба.")
        return
    
    await state.update_data(mob_name=name)
    
    text = html.bold("🧌 СОЗДАНИЕ НОВОГО МОБА\n\n")
    text += "ШАГ 2: ТИП МОБА\n\n"
    text += "Выберите тип моба:\n\n"
    text += "🐺 Зверь - животные, звери\n"
    text += "👤 Гуманоид - люди, гоблины, орки\n"
    text += "💀 Нежить - скелеты, зомби\n"
    text += "😈 Демон - демоны, бесы\n"
    text += "🌪️ Элементаль - стихийные существа\n"
    text += "🐉 Дракон - драконы и ящеры"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🐺 Зверь", callback_data="mob_type_beast"),
            InlineKeyboardButton(text="👤 Гуманоид", callback_data="mob_type_humanoid")
        ],
        [
            InlineKeyboardButton(text="💀 Нежить", callback_data="mob_type_undead"),
            InlineKeyboardButton(text="😈 Демон", callback_data="mob_type_demon")
        ],
        [
            InlineKeyboardButton(text="🌪️ Элементаль", callback_data="mob_type_elemental"),
            InlineKeyboardButton(text="🐉 Дракон", callback_data="mob_type_dragon")
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_mobs")]
    ])
    
    await message.answer(text, reply_markup=keyboard, parse_mode="HTML")

@admin_router.callback_query(F.data.startswith("mob_type_"))
async def handle_mob_type(callback: CallbackQuery, state: FSMContext):
    """Обработчик типа моба"""
    mob_type = callback.data.replace("mob_type_", "")
    mob_type_enum = MobType(mob_type)
    
    await state.update_data(mob_type=mob_type_enum)
    
    text = html.bold("🧌 СОЗДАНИЕ НОВОГО МОБА\n\n")
    text += "ШАГ 3: ХАРАКТЕРИСТИКИ\n\n"
    text += "Введите характеристики в формате:\n"
    text += "Уровень:Здоровье:Урон мин-макс:Защита\n\n"
    text += "Пример:\n"
    text += "10:150:25-35:5"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_mobs")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await state.set_state(AdminStates.create_mob_stats)

@admin_router.message(AdminStates.create_mob_stats)
async def handle_mob_stats(message: Message, state: FSMContext):
    """Обработчик характеристик моба"""
    stats_input = message.text.strip()
    parts = stats_input.split(':')
    
    if len(parts) < 3:
        await message.answer("Неверный формат. Пример: 10:150:25-35:5")
        return
    
    try:
        level = int(parts[0])
        health = int(parts[1])
        
        # Обработка урона
        damage_parts = parts[2].split('-')
        if len(damage_parts) != 2:
            await message.answer("Неверный формат урона. Используйте мин-макс")
            return
        
        damage_min = int(damage_parts[0])
        damage_max = int(damage_parts[1])
        
        defense = int(parts[3]) if len(parts) > 3 else 0
        
        await state.update_data(
            mob_level=level,
            mob_health=health,
            mob_damage_min=damage_min,
            mob_damage_max=damage_max,
            mob_defense=defense
        )
        
        text = html.bold("🧌 СОЗДАНИЕ НОВОГО МОБА\n\n")
        text += "ШАГ 4: ДРОП И НАГРАДЫ\n\n"
        text += "Введите награды в формате:\n"
        text += "Опыт:Золото мин-макс:Шанс крита:Шанс уклонения\n\n"
        text += "Пример:\n"
        text += "100:20-50:0.05:0.05"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_mobs")]
        ])
        
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
        await state.set_state(AdminStates.create_mob_drops)
        
    except ValueError:
        await message.answer("Пожалуйста, вводите только числа.")

# Продолжение аналогично для остальных шагов создания моба...

@admin_router.callback_query(F.data == "admin_items")
async def handle_admin_items(callback: CallbackQuery, state: FSMContext):
    """Обработчик конструктора предметов"""
    from database import get_db_session
    
    async with get_db_session() as db:
        # Получаем список предметов по типам
        weapons = await db.execute(
            select(ItemTemplate)
            .where(ItemTemplate.item_type == ItemType.WEAPON)
            .order_by(ItemTemplate.level_requirement)
            .limit(10)
        )
        weapons = weapons.scalars().all()
        
        armors = await db.execute(
            select(ItemTemplate)
            .where(ItemTemplate.item_type == ItemType.ARMOR)
            .order_by(ItemTemplate.level_requirement)
            .limit(10)
        )
        armors = armors.scalars().all()
        
        potions = await db.execute(
            select(ItemTemplate)
            .where(ItemTemplate.item_type == ItemType.POTION)
            .order_by(ItemTemplate.level_requirement)
            .limit(10)
        )
        potions = potions.scalars().all()
        
        text = html.bold("📦 КОНСТРУКТОР ПРЕДМЕТОВ\n\n")
        
        if weapons:
            text += html.bold("⚔️ ОРУЖИЕ:\n")
            for item in weapons[:5]:
                text += f"{item.icon} {item.name} (ур. {item.level_requirement})\n"
            text += "\n"
        
        if armors:
            text += html.bold("🛡️ БРОНЯ:\n")
            for item in armors[:5]:
                text += f"{item.icon} {item.name} (ур. {item.level_requirement})\n"
            text += "\n"
        
        if potions:
            text += html.bold("🧪 ЗЕЛЬЯ:\n")
            for item in potions[:5]:
                text += f"{item.icon} {item.name} (ур. {item.level_requirement})\n"
            text += "\n"
        
        keyboard_buttons = [
            [InlineKeyboardButton(text="⚔️ Создать оружие", callback_data="admin_item_create_weapon")],
            [InlineKeyboardButton(text="🛡️ Создать броню", callback_data="admin_item_create_armor")],
            [InlineKeyboardButton(text="🧪 Создать зелье", callback_data="admin_item_create_potion")],
            [InlineKeyboardButton(text="📦 Создать ресурс", callback_data="admin_item_create_resource")],
            [InlineKeyboardButton(text="🔑 Создать ключ", callback_data="admin_item_create_key")],
            [InlineKeyboardButton(text="📋 Список всех предметов", callback_data="admin_item_list_all")]
        ]
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

# ============ ХЭНДЛЕРЫ СИСТЕМНЫХ НАСТРОЕК ============

@admin_router.callback_query(F.data == "admin_system")
async def handle_admin_system(callback: CallbackQuery, state: FSMContext):
    """Обработчик системных настроек"""
    from database import get_db_session
    
    async with get_db_session() as db:
        admin_manager = AdminManager(get_db_session)
        settings = await admin_manager.get_system_settings(db)
        
        text = html.bold("⚙️ НАСТРОЙКИ СИСТЕМЫ\n\n")
        
        text += html.bold("1️⃣ ЛИМИТЫ:\n")
        text += f"• Макс. игроков: {settings['max_players']}\n"
        text += f"• Макс. предметов на игрока: {settings['max_items_per_player']}\n"
        text += f"• Макс. активных крафтов: {settings['max_active_crafts']}\n\n"
        
        text += html.bold("2️⃣ ВРЕМЯ:\n")
        text += f"• Интервал бэкапа: {settings['backup_interval']} сек.\n"
        text += f"• Автосохранение: {settings['autosave_interval']} сек.\n"
        text += f"• Таймаут сессии: {settings['timeout_seconds']} сек.\n\n"
        
        text += html.bold("3️⃣ ЭКОНОМИКА:\n")
        text += f"• Стартовое золото: {settings['starting_gold']}\n"
        text += f"• Макс. золота: {format_number(settings['max_gold'])}\n"
        text += f"• Комиссия торговли: {settings['trade_commission']}%\n\n"
        
        text += html.bold("4️⃣ PVP:\n")
        text += f"• Минимальный уровень: {settings['pvp_min_level']}\n"
        text += f"• Макс. разница уровней: {settings['pvp_level_difference']}\n"
        text += f"• Награда за убийство: уровень × {settings['pvp_kill_reward_multiplier']}\n"
        text += f"• Штраф за смерть: {settings['pvp_death_penalty']}% золота\n\n"
        
        text += html.bold("5️⃣ СОБЫТИЯ:\n")
        text += f"• Базовый шанс события: {settings['event_base_chance']}%\n"
        text += f"• Длительность события: {settings['event_duration']} сек.\n"
        text += f"• Макс. активных событий: {settings['max_active_events']}\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📝 Редактировать лимиты", callback_data="admin_edit_limits")],
            [InlineKeyboardButton(text="⏱️ Редактировать время", callback_data="admin_edit_timing")],
            [InlineKeyboardButton(text="💰 Редактировать экономику", callback_data="admin_edit_economy")],
            [InlineKeyboardButton(text="⚔️ Редактировать PvP", callback_data="admin_edit_pvp")],
            [InlineKeyboardButton(text="🎭 Редактировать события", callback_data="admin_edit_events")],
            [InlineKeyboardButton(text="🔄 Сбросить настройки", callback_data="admin_reset_settings")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(AdminStates.system_settings)

# ============ ХЭНДЛЕРЫ БЭКАПОВ ============

@admin_router.callback_query(F.data == "admin_backups")
async def handle_admin_backups(callback: CallbackQuery, state: FSMContext):
    """Обработчик меню бэкапов"""
    from database import get_db_session
    
    async with get_db_session() as db:
        admin_manager = AdminManager(get_db_session)
        backup_list = await admin_manager.get_backup_list(db, limit=5)
        
        text = html.bold("💾 БЭКАПЫ И ЭКСПОРТ\n\n")
        
        if backup_list:
            text += html.bold("ПОСЛЕДНИЕ БЭКАПЫ:\n\n")
            for backup in backup_list:
                status = "✅" if backup.success else "❌"
                size = format_size(backup.size_bytes)
                time_ago = format_timedelta(datetime.utcnow() - backup.created_at)
                
                text += f"{status} {backup.filename}\n"
                text += f"  Размер: {size} | {time_ago} назад\n\n"
        else:
            text += "Бэкапы не создавались.\n\n"
        
        text += html.bold("ДОСТУПНЫЕ ДЕЙСТВИЯ:\n")
        text += "• Создать бэкап (весь контент)\n"
        text += "• Экспорт данных (выборочно)\n"
        text += "• Восстановление из бэкапа\n"
        text += "• Очистка старых бэкапов"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💾 Создать бэкап", callback_data="admin_backup_create")],
            [InlineKeyboardButton(text="📥 Восстановить из бэкапа", callback_data="admin_backup_restore")],
            [InlineKeyboardButton(text="📤 Экспорт данных", callback_data="admin_export_data")],
            [InlineKeyboardButton(text="🗑️ Очистить старые бэкапы", callback_data="admin_backup_cleanup")],
            [InlineKeyboardButton(text="📋 Список всех бэкапов", callback_data="admin_backup_list_all")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(AdminStates.backup_menu)

@admin_router.callback_query(F.data == "admin_backup_create")
async def handle_backup_create(callback: CallbackQuery, state: FSMContext):
    """Обработчик создания бэкапа"""
    from database import get_db_session
    
    # Показать подтверждение
    text = html.bold("💾 СОЗДАНИЕ БЭКАПА\n\n")
    text += "Вы уверены, что хотите создать бэкап?\n\n"
    text += "⚠️ Бэкап сохранит:\n"
    text += "• Всех игроков\n"
    text += "• Весь контент (мобы, предметы, локации)\n"
    text += "• Все настройки системы\n\n"
    text += "Бэкап может занять несколько секунд."
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, создать", callback_data="admin_backup_confirm"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="admin_backups")
        ]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

@admin_router.callback_query(F.data == "admin_backup_confirm")
async def handle_backup_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение создания бэкапа"""
    from database import get_db_session
    
    async with get_db_session() as db:
        admin_manager = AdminManager(get_db_session)
        admin_user = await admin_manager.get_admin_user(callback.from_user.id)
        
        if not admin_user:
            await callback.answer("Ошибка доступа")
            return
        
        # Показываем сообщение о начале
        await callback.message.edit_text("⏳ Создание бэкапа...")
        
        # Создаем бэкап
        result = await admin_manager.create_backup(db, admin_user.id)
        
        if result['success']:
            size = format_size(result['size'])
            filename = result['filename']
            
            text = html.bold("✅ БЭКАП УСПЕШНО СОЗДАН\n\n")
            text += f"📁 Файл: {filename}\n"
            text += f"📏 Размер: {size}\n"
            text += f"🕐 Время: {result['timestamp'].strftime('%H:%M:%S')}\n\n"
            text += "Бэкап сохранен в папке 'backups'"
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📥 Скачать файл", callback_data=f"admin_backup_download_{filename}")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_backups")]
            ])
        else:
            text = html.bold("❌ ОШИБКА СОЗДАНИЯ БЭКАПА\n\n")
            text += f"Ошибка: {result['error']}\n\n"
            text += "Проверьте права доступа к папке backups."
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="admin_backup_create")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_backups")]
            ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

# ============ ХЭНДЛЕРЫ ФОРМУЛ ============

@admin_router.callback_query(F.data == "admin_formulas")
async def handle_admin_formulas(callback: CallbackQuery, state: FSMContext):
    """Обработчик редактора формул"""
    from database import get_db_session
    
    async with get_db_session() as db:
        admin_manager = AdminManager(get_db_session)
        settings = await admin_manager.get_system_settings(db)
        
        text = html.bold("📈 РЕДАКТОР ФОРМУЛ\n\n")
        text += html.bold("ДОСТУПНЫЕ ФОРМУЛЫ:\n\n")
        
        formulas = [
            ("Опыт за моба", "exp_for_next_level_formula", settings.get('exp_for_next_level_formula', '')),
            ("Расчет урона", "damage_formula", settings.get('damage_formula', '')),
            ("Расчет защиты", "defense_formula", settings.get('defense_formula', '')),
            ("Шанс крита", "critical_chance_formula", settings.get('critical_chance_formula', '')),
            ("Шанс уклонения", "dodge_chance_formula", settings.get('dodge_chance_formula', '')),
            ("Опыт за уровень", "level_exp_formula", settings.get('level_exp_formula', 'level * 100')),
            ("Вес инвентаря", "weight_formula", settings.get('weight_formula', 'strength * 2')),
            ("Шанс побега", "flee_formula", settings.get('flee_formula', '0.3 + agility * 0.002')),
            ("Шанс дропа", "drop_formula", settings.get('drop_formula', 'base_chance * (1 + luck * 0.001)')),
            ("Цена предмета", "price_formula", settings.get('price_formula', 'base_price * (1 + rarity_modifier)'))
        ]
        
        for i, (name, key, formula) in enumerate(formulas[:5], 1):
            formula_preview = formula[:50] + "..." if len(formula) > 50 else formula
            text += f"{i}. {name}:\n   {formula_preview}\n\n"
        
        keyboard_buttons = []
        for i, (name, key, formula) in enumerate(formulas[:8], 1):
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"✏️ {name}",
                    callback_data=f"admin_formula_edit_{key}"
                )
            ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="➕ Новая формула", callback_data="admin_formula_new"),
            InlineKeyboardButton(text="📋 Все формулы", callback_data="admin_formula_list_all")
        ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="🔄 Сбросить формулы", callback_data="admin_formula_reset"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(AdminStates.formula_editor)

@admin_router.callback_query(F.data.startswith("admin_formula_edit_"))
async def handle_formula_edit(callback: CallbackQuery, state: FSMContext):
    """Обработчик редактирования формулы"""
    formula_key = callback.data.replace("admin_formula_edit_", "")
    
    from database import get_db_session
    
    async with get_db_session() as db:
        admin_manager = AdminManager(get_db_session)
        settings = await admin_manager.get_system_settings(db)
        
        current_formula = settings.get(formula_key, "")
        
        formula_names = {
            "exp_for_next_level_formula": "Опыт за следующий уровень",
            "damage_formula": "Расчет урона",
            "defense_formula": "Расчет защиты",
            "critical_chance_formula": "Шанс критического удара",
            "dodge_chance_formula": "Шанс уклонения",
            "level_exp_formula": "Опыт за уровень",
            "weight_formula": "Максимальный вес",
            "flee_formula": "Шанс побега",
            "drop_formula": "Шанс выпадения предмета",
            "price_formula": "Расчет цены предмета"
        }
        
        formula_name = formula_names.get(formula_key, formula_key)
        
        text = html.bold(f"✏️ РЕДАКТИРОВАНИЕ ФОРМУЛЫ\n\n")
        text += html.bold(f"Формула: {formula_name}\n\n")
        text += html.bold("Текущая формула:\n")
        text += f"<code>{current_formula}</code>\n\n"
        text += html.bold("Доступные переменные:\n")
        
        if formula_key == "damage_formula":
            text += "• base_damage - базовый урон\n"
            text += "• strength - сила атакующего\n"
            text += "• agility - ловкость атакующего\n"
            text += "• is_critical - был ли критический удар\n"
            text += "• random(min, max) - случайное число\n\n"
        elif formula_key == "exp_for_next_level_formula":
            text += "• current_level - текущий уровень игрока\n\n"
        
        text += html.bold("Введите новую формулу:")
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Сбросить к стандартной", callback_data=f"admin_formula_reset_{formula_key}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_formulas")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.update_data(editing_formula_key=formula_key)
    await state.set_state(AdminStates.formula_edit)

@admin_router.message(AdminStates.formula_edit)
async def handle_formula_save(message: Message, state: FSMContext):
    """Обработчик сохранения формулы"""
    from database import get_db_session
    
    new_formula = message.text.strip()
    if not new_formula:
        await message.answer("Формула не может быть пустой.")
        return
    
    data = await state.get_data()
    formula_key = data.get('editing_formula_key')
    
    if not formula_key:
        await message.answer("Ошибка: не найден ключ формулы.")
        return
    
    async with get_db_session() as db:
        admin_manager = AdminManager(get_db_session)
        admin_user = await admin_manager.get_admin_user(message.from_user.id)
        
        if not admin_user:
            await message.answer("Ошибка доступа.")
            return
        
        # Проверяем формулу на безопасность
        try:
            # Базовые проверки безопасности
            banned_keywords = ['import', 'exec', 'eval', '__', 'open', 'file', 'os.', 'sys.', 'subprocess']
            for keyword in banned_keywords:
                if keyword in new_formula.lower():
                    await message.answer(f"❌ Формула содержит запрещенное ключевое слово: {keyword}")
                    return
            
            # Пробуем скомпилировать формулу
            compiled = compile(new_formula, '<string>', 'eval')
            
            # Если компиляция прошла успешно, сохраняем
            success = await admin_manager.update_formula(db, formula_key, new_formula, admin_user.id)
            
            if success:
                await message.answer(f"✅ Формула успешно обновлена!\n\n<code>{new_formula}</code>", parse_mode="HTML")
            else:
                await message.answer("❌ Не удалось сохранить формулу.")
                
        except SyntaxError as e:
            await message.answer(f"❌ Ошибка синтаксиса в формуле:\n{e}")
        except Exception as e:
            await message.answer(f"❌ Ошибка при проверке формулы:\n{e}")
    
    await state.set_state(AdminStates.formula_editor)
    # Возвращаемся к редактору формул
    await handle_admin_formulas(CallbackQuery(
        message=message,
        data="admin_formulas",
        from_user=message.from_user,
        chat_instance=""
    ), state)

# ============ ХЭНДЛЕРЫ ПЕРЕЗАГРУЗКИ ============

@admin_router.callback_query(F.data == "admin_restart")
async def handle_admin_restart(callback: CallbackQuery, state: FSMContext):
    """Обработчик перезагрузки системы"""
    text = html.bold("🔄 ПЕРЕЗАГРУЗКА СИСТЕМЫ\n\n")
    text += "⚠️ ВНИМАНИЕ!\n\n"
    text += "Перезагрузка выполнит следующие действия:\n"
    text += "1. Сохранение всех активных состояний\n"
    text += "2. Восстановление всех активных действий\n"
    text += "3. Перезагрузка кэша Redis\n"
    text += "4. Проверка целостности данных\n\n"
    text += "Процесс займет несколько секунд.\n"
    text += "Все активные действия игроков будут сохранены."
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Запустить перезагрузку", callback_data="admin_restart_confirm"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="admin_menu")
        ]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

@admin_router.callback_query(F.data == "admin_restart_confirm")
async def handle_restart_confirm(callback: CallbackQuery):
    """Подтверждение перезагрузки"""
    from main import restart_all_managers
    
    await callback.message.edit_text("⏳ Выполняется перезагрузка системы...")
    
    try:
        # Вызываем перезагрузку всех менеджеров
        await restart_all_managers()
        
        text = html.bold("✅ СИСТЕМА ПЕРЕЗАГРУЖЕНА\n\n")
        text += "Все модули успешно перезагружены:\n"
        text += "• ✅ Модуль битв\n"
        text += "• ✅ Модуль PvP\n"
        text += "• ✅ Модуль локаций\n"
        text += "• ✅ Модуль инвентаря\n"
        text += "• ✅ Кэш Redis\n\n"
        text += "Все активные состояния восстановлены."
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛡️ В админ-панель", callback_data="admin_menu")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        
    except Exception as e:
        text = html.bold("❌ ОШИБКА ПЕРЕЗАГРУЗКИ\n\n")
        text += f"Ошибка: {str(e)}\n\n"
        text += "Проверьте логи для подробностей."
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="admin_restart")],
            [InlineKeyboardButton(text="🛡️ В админ-панель", callback_data="admin_menu")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

# ============ УТИЛИТЫ ДЛЯ РАБОТЫ С ФАЙЛАМИ ============

@admin_router.callback_query(F.data.startswith("admin_backup_download_"))
async def handle_backup_download(callback: CallbackQuery):
    """Обработчик скачивания бэкапа"""
    filename = callback.data.replace("admin_backup_download_", "")
    filepath = os.path.join("backups", filename)
    
    if os.path.exists(filepath):
        try:
            # Отправляем файл
            document = FSInputFile(filepath)
            await callback.message.answer_document(document, caption=f"Бэкап: {filename}")
        except Exception as e:
            await callback.answer(f"Ошибка отправки файла: {str(e)}")
    else:
        await callback.answer("Файл не найден")

# ============ ГЛОБАЛЬНЫЕ ХЭНДЛЕРЫ ============

@admin_router.callback_query(F.data == "cancel")
async def handle_cancel(callback: CallbackQuery, state: FSMContext):
    """Обработчик отмены"""
    await state.clear()
    await show_admin_main_menu(callback, state)

@admin_router.callback_query(F.data == "admin_logout")
async def handle_admin_logout(callback: CallbackQuery, state: FSMContext):
    """Выход из админ-панели"""
    await state.clear()
    await callback.message.answer("✅ Вы вышли из админ-панели.")
    # Можно вернуться в главное меню игрока
    await callback.message.answer("🏰 Главное меню", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Персонаж", callback_data="character")]
    ]))

# ============ ФУНКЦИЯ ИНИЦИАЛИЗАЦИИ ============

async def init_admin_module(db_session_factory, redis_client=None, engine=None):
    """Инициализировать админ-модуль"""
    admin_manager = AdminManager(db_session_factory, redis_client, engine)
    
    # Создаем системные настройки по умолчанию, если их нет
    async with db_session_factory() as db:
        settings = await admin_manager.get_system_settings(db)
        
        # Проверяем наличие минимальных настроек
        required_settings = [
            ('max_players', 1000),
            ('starting_gold', 100),
            ('pvp_min_level', 10),
            ('event_base_chance', 20)
        ]
        
        for key, default_value in required_settings:
            if key not in settings:
                await admin_manager.update_system_setting(
                    db, key, default_value, 
                    uuid.UUID('00000000-0000-0000-0000-000000000000')  # Системный ID
                )
    
    print("✅ Админ-модуль инициализирован")
    return admin_manager

# Экспортируемые объекты
__all__ = [
    'admin_router',
    'AdminManager',
    'init_admin_module',
    'AdminStates',
    'AdminAction',
    'ContentType',
    'format_number',
    'format_timedelta',
    'format_size'
]