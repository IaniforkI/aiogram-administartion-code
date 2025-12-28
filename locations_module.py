# locations_module.py
"""
Полный модуль для управления локациями, путешествиями, ресурсами и событиями.
Включает конструкторы для админ-панели и полное восстановление состояния.
"""

import asyncio
import json
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any, Union
from enum import Enum
import uuid
from dataclasses import dataclass, field

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, update, and_, or_, desc, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload

from models import (
    User, Location, TravelRoute, MobSpawn, ResourceSpawn, 
    ActiveAction, ActionType, StateSnapshot, MobTemplate,
    ResourceTemplate, GameEvent, EventTrigger, ChestTemplate,
    SystemSettings, AuditLog, Discovery, ItemTemplate,
    LocationType, EventType, EventActivationType, ResourceType,
    Item, Inventory
)

# ============ КОНСТАНТЫ ============

class TravelStatus(str, Enum):
    TRAVELING = "traveling"
    ARRIVED = "arrived"
    INTERRUPTED = "interrupted"
    EVENT_TRIGGERED = "event_triggered"

# ============ РОУТЕР И СОСТОЯНИЯ ============

locations_router = Router()

class LocationStates(StatesGroup):
    # Админ-состояния
    admin_create_location = State()
    admin_create_location_name = State()
    admin_create_location_type = State()
    admin_create_location_levels = State()
    admin_create_location_resources = State()
    admin_create_location_mobs = State()
    admin_create_location_routes = State()
    admin_create_location_events = State()
    
    admin_edit_location = State()
    admin_delete_location = State()
    
    admin_create_resource = State()
    admin_create_resource_name = State()
    admin_create_resource_type = State()
    admin_create_resource_params = State()
    
    admin_create_travel_route = State()
    
    admin_create_event = State()
    admin_create_event_basic = State()
    admin_create_event_activation = State()
    admin_create_event_locations = State()
    admin_create_event_rewards = State()
    
    # Игровые состояния
    exploring_location = State()
    traveling_to_location = State()
    handling_event = State()
    
    # Ресурсы
    gathering_resource = State()
    mining_ore = State()
    woodcutting = State()
    herbalism = State()
    
    # Локация выбора
    location_selection = State()

# ============ МЕНЕДЖЕР ЛОКАЦИЙ ============

class LocationManager:
    """Менеджер для управления локациями и путешествиями"""
    
    def __init__(self, redis_client, db_session_factory):
        self.redis = redis_client
        self.db_session_factory = db_session_factory
        self.active_travels = {}  # {user_id: travel_data}
        self.active_gathering = {}  # {user_id: gathering_data}
        self.active_events = {}  # {event_id: event_data}
    
    async def restore_state(self):
        """Восстановить все активные состояния при запуске бота"""
        async with self.db_session_factory() as db:
            try:
                # 1. Восстановить активные путешествия
                result = await db.execute(
                    select(ActiveAction).where(
                        and_(
                            ActiveAction.action_type == ActionType.TRAVEL,
                            ActiveAction.is_completed == False
                        )
                    ).options(selectinload(ActiveAction.user))
                )
                travels = result.scalars().all()
                
                for travel in travels:
                    if travel.end_time < datetime.utcnow():
                        # Путешествие завершено
                        await self.complete_travel(db, travel)
                    else:
                        travel_key = f"travel:{travel.user_id}"
                        travel_data = {
                            "action_id": str(travel.id),
                            "user_id": str(travel.user_id),
                            "target_id": str(travel.target_id),
                            "start_time": travel.start_time.isoformat(),
                            "end_time": travel.end_time.isoformat(),
                            "progress": travel.progress,
                            "data": travel.data or {}
                        }
                        
                        remaining_time = (travel.end_time - datetime.utcnow()).seconds
                        await self.redis.setex(
                            travel_key,
                            remaining_time,
                            json.dumps(travel_data)
                        )
                        self.active_travels[str(travel.user_id)] = travel_data
                
                # 2. Восстановить активный сбор ресурсов
                result = await db.execute(
                    select(ActiveAction).where(
                        and_(
                            ActiveAction.action_type.in_([
                                ActionType.MINING, 
                                ActionType.WOODCUTTING, 
                                ActionType.HERBALISM
                            ]),
                            ActiveAction.is_completed == False
                        )
                    ).options(selectinload(ActiveAction.user))
                )
                gatherings = result.scalars().all()
                
                for gathering in gatherings:
                    if gathering.end_time < datetime.utcnow():
                        # Сбор завершен
                        await self.complete_gathering(db, gathering)
                    else:
                        gathering_key = f"gathering:{gathering.user_id}"
                        gathering_data = {
                            "action_id": str(gathering.id),
                            "user_id": str(gathering.user_id),
                            "action_type": gathering.action_type.value,
                            "target_id": str(gathering.target_id),
                            "start_time": gathering.start_time.isoformat(),
                            "end_time": gathering.end_time.isoformat(),
                            "progress": gathering.progress,
                            "data": gathering.data or {}
                        }
                        
                        remaining_time = (gathering.end_time - datetime.utcnow()).seconds
                        await self.redis.setex(
                            gathering_key,
                            remaining_time,
                            json.dumps(gathering_data)
                        )
                        self.active_gathering[str(gathering.user_id)] = gathering_data
                
                # 3. Восстановить активные события
                result = await db.execute(
                    select(GameEvent).where(
                        GameEvent.is_active == True
                    ).options(
                        selectinload(GameEvent.triggers),
                        selectinload(GameEvent.rewards)
                    )
                )
                events = result.scalars().all()
                
                for event in events:
                    if event.end_time and event.end_time < datetime.utcnow():
                        event.is_active = False
                    else:
                        event_key = f"event:{event.id}"
                        event_data = {
                            "id": str(event.id),
                            "name": event.name,
                            "event_type": event.event_type.value,
                            "start_time": event.start_time.isoformat() if event.start_time else None,
                            "end_time": event.end_time.isoformat() if event.end_time else None,
                            "is_active": event.is_active,
                            "triggers": [
                                {
                                    "location_id": str(trigger.location_id),
                                    "trigger_chance": trigger.trigger_chance
                                }
                                for trigger in event.triggers
                            ]
                        }
                        
                        if event.end_time:
                            remaining_time = (event.end_time - datetime.utcnow()).seconds
                            await self.redis.setex(
                                event_key,
                                remaining_time,
                                json.dumps(event_data)
                            )
                        else:
                            await self.redis.set(event_key, json.dumps(event_data))
                        
                        self.active_events[str(event.id)] = event_data
                
                # 4. Восстановить снапшоты состояний
                result = await db.execute(
                    select(StateSnapshot).where(
                        and_(
                            StateSnapshot.is_restored == False,
                            StateSnapshot.expires_at > datetime.utcnow(),
                            StateSnapshot.snapshot_type.in_([
                                "travel", "gathering", "location_event"
                            ])
                        )
                    )
                )
                snapshots = result.scalars().all()
                
                for snapshot in snapshots:
                    await self.restore_from_snapshot(db, snapshot)
                
                await db.commit()
                print(f"✅ Восстановлено {len(travels)} путешествий, {len(gatherings)} сборов и {len(events)} событий")
                
            except Exception as e:
                print(f"❌ Ошибка при восстановлении состояния локаций: {e}")
                await db.rollback()
    
    async def restore_from_snapshot(self, db: AsyncSession, snapshot: StateSnapshot):
        """Восстановить из снапшота"""
        try:
            snapshot_data = snapshot.snapshot_data
            snapshot_type = snapshot.snapshot_type
            
            if snapshot_type == "travel":
                await self.restore_travel(db, snapshot)
            elif snapshot_type == "gathering":
                await self.restore_gathering(db, snapshot)
            elif snapshot_type == "location_event":
                await self.restore_event(db, snapshot)
            
            snapshot.is_restored = True
            
        except Exception as e:
            print(f"❌ Ошибка восстановления из снапшота: {e}")
    
    async def restore_travel(self, db: AsyncSession, snapshot: StateSnapshot):
        """Восстановить путешествие"""
        snapshot_data = snapshot.snapshot_data
        user_id = snapshot.user_id
        
        # Проверяем не завершилось ли путешествие
        end_time = datetime.fromisoformat(snapshot_data.get("end_time"))
        if end_time < datetime.utcnow():
            return
        
        # Создаем новое активное действие
        travel = ActiveAction(
            id=uuid.uuid4(),
            user_id=user_id,
            action_type=ActionType.TRAVEL,
            target_id=uuid.UUID(snapshot_data.get("target_location_id")),
            start_time=datetime.fromisoformat(snapshot_data.get("start_time")),
            end_time=end_time,
            progress=snapshot_data.get("progress", 0),
            data=snapshot_data.get("travel_data", {})
        )
        
        db.add(travel)
        
        # Сохраняем в Redis
        travel_key = f"travel:{user_id}"
        travel_data = {
            "action_id": str(travel.id),
            "user_id": str(user_id),
            "target_id": str(travel.target_id),
            "start_time": travel.start_time.isoformat(),
            "end_time": travel.end_time.isoformat(),
            "progress": travel.progress,
            "data": travel.data or {}
        }
        
        remaining_time = (travel.end_time - datetime.utcnow()).seconds
        await self.redis.setex(
            travel_key,
            remaining_time,
            json.dumps(travel_data)
        )
        self.active_travels[str(user_id)] = travel_data
    
    # ============ ОСНОВНЫЕ МЕТОДЫ ЛОКАЦИЙ ============
    
    async def get_location_by_id(self, db: AsyncSession, location_id: uuid.UUID) -> Optional[Location]:
        """Получить локацию по ID"""
        result = await db.execute(
            select(Location).where(Location.id == location_id).options(
                selectinload(Location.mob_spawns).selectinload(MobSpawn.mob_template),
                selectinload(Location.resource_spawns).selectinload(ResourceSpawn.resource_template),
                selectinload(Location.event_triggers).selectinload(EventTrigger.game_event)
            )
        )
        return result.scalar_one_or_none()
    
    async def get_current_location(self, db: AsyncSession, user_id: uuid.UUID) -> Optional[Location]:
        """Получить текущую локацию игрока"""
        user = await db.get(User, user_id)
        if not user or not user.current_location_id:
            return None
        
        return await self.get_location_by_id(db, user.current_location_id)
    
    async def explore_location(self, db: AsyncSession, user_id: uuid.UUID) -> Dict[str, Any]:
        """Исследовать локацию - получить информацию о мобах и ресурсах"""
        location = await self.get_current_location(db, user_id)
        if not location:
            return {"error": "Локация не найдена"}
        
        # Получаем мобов в локации
        mob_spawns = []
        result = await db.execute(
            select(MobSpawn).where(MobSpawn.location_id == location.id).options(
                selectinload(MobSpawn.mob_template)
            )
        )
        spawns = result.scalars().all()
        
        for spawn in spawns:
            if random.random() < spawn.spawn_chance:
                mob_spawns.append({
                    "id": str(spawn.mob_template_id),
                    "name": spawn.mob_template.name,
                    "icon": spawn.mob_template.icon,
                    "level": spawn.mob_template.level,
                    "health": spawn.mob_template.health,
                    "count": random.randint(1, 3)  # Случайное количество
                })
        
        # Получаем ресурсы
        resources = []
        result = await db.execute(
            select(ResourceSpawn).where(ResourceSpawn.location_id == location.id).options(
                selectinload(ResourceSpawn.resource_template)
            )
        )
        resource_spawns = result.scalars().all()
        
        for spawn in resource_spawns:
            if random.random() < spawn.spawn_chance:
                resources.append({
                    "id": str(spawn.resource_template_id),
                    "name": spawn.resource_template.name,
                    "icon": spawn.resource_template.icon,
                    "type": spawn.resource_template.resource_type.value,
                    "chance": spawn.spawn_chance,
                    "min_quantity": spawn.resource_template.min_quantity,
                    "max_quantity": spawn.resource_template.max_quantity
                })
        
        # Проверяем наличие шахты
        mine_info = None
        if location.has_mine:
            mine_info = {
                "level": location.mine_level,
                "available": True
            }
        
        # Проверяем активные события
        active_events = []
        result = await db.execute(
            select(EventTrigger).where(
                and_(
                    EventTrigger.location_id == location.id,
                    EventTrigger.game_event.has(GameEvent.is_active == True)
                )
            ).options(
                selectinload(EventTrigger.game_event)
            )
        )
        event_triggers = result.scalars().all()
        
        for trigger in event_triggers:
            if random.random() < trigger.trigger_chance:
                active_events.append({
                    "id": str(trigger.game_event.id),
                    "name": trigger.game_event.name,
                    "icon": trigger.game_event.icon,
                    "type": trigger.game_event.event_type.value,
                    "description": trigger.game_event.description
                })
        
        return {
            "location": {
                "id": str(location.id),
                "name": location.name,
                "icon": location.icon,
                "type": location.location_type.value,
                "description": location.description
            },
            "mobs": mob_spawns,
            "resources": resources,
            "mine": mine_info,
            "events": active_events,
            "has_forest": location.has_forest,
            "has_herbs": location.has_herbs
        }
    
    async def travel_to_location(self, db: AsyncSession, user_id: uuid.UUID, 
                                to_location_id: uuid.UUID) -> Dict[str, Any]:
        """Начать путешествие в другую локацию"""
        user = await db.get(User, user_id)
        if not user:
            return {"error": "Игрок не найден"}
        
        # Проверяем есть ли активное действие
        result = await db.execute(
            select(ActiveAction).where(
                and_(
                    ActiveAction.user_id == user_id,
                    ActiveAction.is_completed == False
                )
            )
        )
        active_action = result.scalar_one_or_none()
        
        if active_action:
            return {"error": "У вас уже есть активное действие"}
        
        # Проверяем маршрут
        result = await db.execute(
            select(TravelRoute).where(
                and_(
                    TravelRoute.from_location_id == user.current_location_id,
                    TravelRoute.to_location_id == to_location_id
                )
            )
        )
        route = result.scalar_one_or_none()
        
        if not route:
            return {"error": "Маршрут не найден"}
        
        # Проверяем уровень
        if user.level < route.min_level:
            return {"error": f"Требуется уровень {route.min_level}"}
        
        # Проверяем достаточно ли золота
        if user.gold < route.gold_cost:
            return {"error": f"Недостаточно золота. Нужно: {route.gold_cost}"}
        
        # Списываем золото
        user.gold -= route.gold_cost
        
        # Создаем активное действие путешествия
        start_time = datetime.utcnow()
        end_time = start_time + timedelta(seconds=route.travel_time)
        
        travel_action = ActiveAction(
            user_id=user_id,
            action_type=ActionType.TRAVEL,
            target_id=to_location_id,
            start_time=start_time,
            end_time=end_time,
            progress=0.0,
            data={
                "from_location_id": str(user.current_location_id),
                "route_id": str(route.id),
                "gold_cost": route.gold_cost,
                "travel_time": route.travel_time
            }
        )
        
        db.add(travel_action)
        
        # Создаем снапшот для восстановления
        snapshot = StateSnapshot(
            snapshot_type="travel",
            user_id=user_id,
            entity_id=travel_action.id,
            entity_type="active_action",
            snapshot_data={
                "target_location_id": str(to_location_id),
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "progress": 0.0,
                "travel_data": travel_action.data
            },
            expires_at=end_time + timedelta(hours=1)
        )
        db.add(snapshot)
        
        # Логируем
        audit_log = AuditLog(
            user_id=user_id,
            action="travel_started",
            details={
                "from_location_id": str(user.current_location_id),
                "to_location_id": str(to_location_id),
                "route_id": str(route.id),
                "gold_cost": route.gold_cost,
                "travel_time": route.travel_time
            }
        )
        db.add(audit_log)
        
        await db.commit()
        
        # Сохраняем в Redis
        travel_key = f"travel:{user_id}"
        travel_data = {
            "action_id": str(travel_action.id),
            "user_id": str(user_id),
            "target_id": str(to_location_id),
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "progress": 0.0,
            "data": travel_action.data
        }
        
        await self.redis.setex(
            travel_key,
            route.travel_time,
            json.dumps(travel_data)
        )
        self.active_travels[str(user_id)] = travel_data
        
        # Запускаем таймер для проверки путешествия
        asyncio.create_task(self._monitor_travel(travel_action.id, route.travel_time))
        
        return {
            "success": True,
            "travel_time": route.travel_time,
            "end_time": end_time,
            "action_id": str(travel_action.id)
        }
    
    async def _monitor_travel(self, action_id: uuid.UUID, travel_time: int):
        """Мониторинг путешествия"""
        await asyncio.sleep(travel_time)
        
        async with self.db_session_factory() as db:
            action = await db.get(ActiveAction, action_id)
            if action and not action.is_completed:
                await self.complete_travel(db, action)
    
    async def complete_travel(self, db: AsyncSession, travel_action: ActiveAction):
        """Завершить путешествие"""
        travel_action.is_completed = True
        travel_action.progress = 1.0
        
        user = await db.get(User, travel_action.user_id)
        if user:
            # Обновляем локацию игрока
            user.current_location_id = travel_action.target_id
            
            # Проверяем открытие локации
            await self._check_location_discovery(db, user.id, travel_action.target_id)
            
            # Проверяем случайное событие
            event_result = await self._check_travel_event(db, user.id, travel_action.target_id)
            
            # Добавляем опыт за путешествие
            xp_reward = 5  # Базовый опыт за путешествие
            user.experience += xp_reward
            
            # Обновляем статистику
            stats = await db.execute(
                select(PlayerStat).where(PlayerStat.user_id == user.id)
            )
            stats = stats.scalar_one_or_none()
            if stats:
                stats.last_travel_time = datetime.utcnow()
        
        await db.commit()
        
        # Удаляем из Redis
        await self.redis.delete(f"travel:{travel_action.user_id}")
        if str(travel_action.user_id) in self.active_travels:
            del self.active_travels[str(travel_action.user_id)]
        
        return event_result
    
    async def _check_location_discovery(self, db: AsyncSession, user_id: uuid.UUID, location_id: uuid.UUID):
        """Проверить открытие новой локации"""
        discovery = await db.execute(
            select(Discovery).where(Discovery.user_id == user_id)
        )
        discovery = discovery.scalar_one_or_none()
        
        if not discovery:
            discovery = Discovery(
                user_id=user_id,
                discovered_locations=[str(location_id)],
                total_discoveries=1
            )
            db.add(discovery)
        elif str(location_id) not in discovery.discovered_locations:
            discovery.discovered_locations.append(str(location_id))
            discovery.total_discoveries += 1
        
        await db.commit()
    
    async def _check_travel_event(self, db: AsyncSession, user_id: uuid.UUID, location_id: uuid.UUID) -> Optional[Dict]:
        """Проверить событие во время путешествия"""
        # Получаем события для локации
        result = await db.execute(
            select(EventTrigger).where(
                and_(
                    EventTrigger.location_id == location_id,
                    EventTrigger.game_event.has(
                        and_(
                            GameEvent.is_active == True,
                            GameEvent.activation_type == EventActivationType.CHANCE
                        )
                    )
                )
            ).options(selectinload(EventTrigger.game_event))
        )
        event_triggers = result.scalars().all()
        
        for trigger in event_triggers:
            if random.random() < trigger.trigger_chance:
                # Триггерим событие
                event_data = await self.trigger_event(db, user_id, trigger.game_event, location_id)
                return event_data
        
        return None
    
    # ============ РЕСУРСЫ ============
    
    async def gather_resource(self, db: AsyncSession, user_id: uuid.UUID, 
                             resource_id: uuid.UUID, action_type: ActionType) -> Dict[str, Any]:
        """Начать сбор ресурса"""
        user = await db.get(User, user_id)
        if not user:
            return {"error": "Игрок не найден"}
        
        # Проверяем есть ли активное действие
        result = await db.execute(
            select(ActiveAction).where(
                and_(
                    ActiveAction.user_id == user_id,
                    ActiveAction.is_completed == False
                )
            )
        )
        active_action = result.scalar_one_or_none()
        
        if active_action:
            return {"error": "У вас уже есть активное действие"}
        
        # Получаем ресурс
        resource = await db.get(ResourceTemplate, resource_id)
        if not resource:
            return {"error": "Ресурс не найден"}
        
        # Проверяем требования
        if action_type == ActionType.MINING:
            if user.mining_level < resource.required_profession_level:
                return {"error": f"Требуется горное дело {resource.required_profession_level}"}
        elif action_type == ActionType.WOODCUTTING:
            if user.woodcutting_level < resource.required_profession_level:
                return {"error": f"Требуется рубка дерева {resource.required_profession_level}"}
        elif action_type == ActionType.HERBALISM:
            if user.herbalism_level < resource.required_profession_level:
                return {"error": f"Требуется травничество {resource.required_profession_level}"}
        
        if user.strength < resource.required_strength:
            return {"error": f"Требуется сила {resource.required_strength}"}
        
        # Проверяем шанс
        if random.random() > resource.gather_chance:
            return {"error": "Не удалось найти ресурс"}
        
        # Создаем активное действие
        start_time = datetime.utcnow()
        end_time = start_time + timedelta(seconds=resource.gather_time)
        
        gathering_action = ActiveAction(
            user_id=user_id,
            action_type=action_type,
            target_id=resource_id,
            start_time=start_time,
            end_time=end_time,
            progress=0.0,
            data={
                "resource_id": str(resource_id),
                "resource_name": resource.name,
                "gather_chance": resource.gather_chance,
                "min_quantity": resource.min_quantity,
                "max_quantity": resource.max_quantity
            }
        )
        
        db.add(gathering_action)
        
        # Снапшот для восстановления
        snapshot = StateSnapshot(
            snapshot_type="gathering",
            user_id=user_id,
            entity_id=gathering_action.id,
            entity_type="active_action",
            snapshot_data={
                "resource_id": str(resource_id),
                "action_type": action_type.value,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "progress": 0.0,
                "gathering_data": gathering_action.data
            },
            expires_at=end_time + timedelta(hours=1)
        )
        db.add(snapshot)
        
        await db.commit()
        
        # Сохраняем в Redis
        gathering_key = f"gathering:{user_id}"
        gathering_data = {
            "action_id": str(gathering_action.id),
            "user_id": str(user_id),
            "action_type": action_type.value,
            "target_id": str(resource_id),
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "progress": 0.0,
            "data": gathering_action.data
        }
        
        await self.redis.setex(
            gathering_key,
            resource.gather_time,
            json.dumps(gathering_data)
        )
        self.active_gathering[str(user_id)] = gathering_data
        
        # Запускаем таймер
        asyncio.create_task(self._monitor_gathering(gathering_action.id, resource.gather_time))
        
        return {
            "success": True,
            "gather_time": resource.gather_time,
            "end_time": end_time,
            "action_id": str(gathering_action.id),
            "resource_name": resource.name
        }
    
    async def _monitor_gathering(self, action_id: uuid.UUID, gather_time: int):
        """Мониторинг сбора ресурсов"""
        await asyncio.sleep(gather_time)
        
        async with self.db_session_factory() as db:
            action = await db.get(ActiveAction, action_id)
            if action and not action.is_completed:
                await self.complete_gathering(db, action)
    
    async def complete_gathering(self, db: AsyncSession, gathering_action: ActiveAction):
        """Завершить сбор ресурсов"""
        gathering_action.is_completed = True
        gathering_action.progress = 1.0
        
        user = await db.get(User, gathering_action.user_id)
        resource = await db.get(ResourceTemplate, gathering_action.target_id)
        
        if user and resource:
            # Определяем количество
            quantity = random.randint(resource.min_quantity, resource.max_quantity)
            
            # Добавляем опыт в профессию
            if gathering_action.action_type == ActionType.MINING:
                user.mining_exp += quantity * 10
                # Проверяем повышение уровня
                await self._check_profession_level_up(db, user, "mining")
            elif gathering_action.action_type == ActionType.WOODCUTTING:
                user.woodcutting_exp += quantity * 10
                await self._check_profession_level_up(db, user, "woodcutting")
            elif gathering_action.action_type == ActionType.HERBALISM:
                user.herbalism_exp += quantity * 10
                await self._check_profession_level_up(db, user, "herbalism")
            
            # Добавляем предмет в инвентарь
            await self._add_resource_to_inventory(db, user.id, resource, quantity)
            
            # Обновляем статистику
            stats = await db.execute(
                select(PlayerStat).where(PlayerStat.user_id == user.id)
            )
            stats = stats.scalar_one_or_none()
            if stats:
                stats.daily_items_found += quantity
        
        await db.commit()
        
        # Удаляем из Redis
        await self.redis.delete(f"gathering:{gathering_action.user_id}")
        if str(gathering_action.user_id) in self.active_gathering:
            del self.active_gathering[str(gathering_action.user_id)]
    
    async def _check_profession_level_up(self, db: AsyncSession, user: User, profession: str):
        """Проверить повышение уровня профессии"""
        if profession == "mining":
            current_level = user.mining_level
            current_exp = user.mining_exp
        elif profession == "woodcutting":
            current_level = user.woodcutting_level
            current_exp = user.woodcutting_exp
        elif profession == "herbalism":
            current_level = user.herbalism_level
            current_exp = user.herbalism_exp
        else:
            return
        
        # Формула для следующего уровня
        exp_needed = current_level * 100
        
        if current_exp >= exp_needed:
            # Повышаем уровень
            if profession == "mining":
                user.mining_level += 1
                user.mining_exp -= exp_needed
            elif profession == "woodcutting":
                user.woodcutting_level += 1
                user.woodcutting_exp -= exp_needed
            elif profession == "herbalism":
                user.herbalism_level += 1
                user.herbalism_exp -= exp_needed
            
            # Логируем
            audit_log = AuditLog(
                user_id=user.id,
                action=f"{profession}_level_up",
                details={
                    "new_level": current_level + 1,
                    "profession": profession
                }
            )
            db.add(audit_log)
    
    async def _add_resource_to_inventory(self, db: AsyncSession, user_id: uuid.UUID, 
                                        resource: ResourceTemplate, quantity: int):
        """Добавить ресурс в инвентарь"""
        # Ищем предмет-шаблон для ресурса
        result = await db.execute(
            select(ItemTemplate).where(
                and_(
                    ItemTemplate.name == resource.name,
                    ItemTemplate.item_type == ItemType.RESOURCE
                )
            )
        )
        item_template = result.scalar_one_or_none()
        
        if not item_template:
            # Создаем шаблон
            item_template = ItemTemplate(
                name=resource.name,
                description=resource.description,
                icon=resource.icon,
                item_type=ItemType.RESOURCE,
                rarity=ItemRarity.COMMON,
                level_requirement=resource.level,
                resource_type=resource.resource_type,
                weight=resource.weight,
                base_price=resource.base_price,
                sell_price=int(resource.base_price * 0.5),
                stack_size=99,
                is_tradable=True,
                is_droppable=True,
                is_consumable=False,
                is_equippable=False
            )
            db.add(item_template)
            await db.flush()
        
        # Ищем инвентарь
        result = await db.execute(
            select(Inventory).where(Inventory.user_id == user_id)
        )
        inventory = result.scalar_one_or_none()
        
        if not inventory:
            inventory = Inventory(user_id=user_id)
            db.add(inventory)
            await db.flush()
        
        # Проверяем есть ли такой предмет в инвентаре
        result = await db.execute(
            select(Item).where(
                and_(
                    Item.owner_id == user_id,
                    Item.template_id == item_template.id
                )
            )
        )
        existing_item = result.scalar_one_or_none()
        
        if existing_item:
            # Увеличиваем количество
            existing_item.quantity += quantity
        else:
            # Создаем новый предмет
            new_item = Item(
                template_id=item_template.id,
                owner_id=user_id,
                quantity=quantity
            )
            db.add(new_item)
    
    # ============ СОБЫТИЯ ============
    
    async def trigger_event(self, db: AsyncSession, user_id: uuid.UUID, 
                           event: GameEvent, location_id: uuid.UUID) -> Dict[str, Any]:
        """Активировать событие"""
        event_data = {
            "id": str(event.id),
            "name": event.name,
            "description": event.description,
            "icon": event.icon,
            "event_type": event.event_type.value,
            "rewards": []
        }
        
        # Добавляем награды
        for reward in event.rewards:
            if random.random() < reward.drop_chance:
                quantity = random.randint(reward.min_quantity, reward.max_quantity)
                event_data["rewards"].append({
                    "item_name": reward.item_template.name,
                    "quantity": quantity,
                    "icon": reward.item_template.icon
                })
                
                # Добавляем предмет игроку
                await self._add_item_to_inventory(db, user_id, reward.item_template, quantity)
        
        # Добавляем золото
        if event.reward_gold_max > 0:
            gold = random.randint(event.reward_gold_min, event.reward_gold_max)
            user = await db.get(User, user_id)
            if user:
                user.gold += gold
                event_data["gold"] = gold
        
        # Добавляем опыт
        if event.reward_xp > 0:
            user = await db.get(User, user_id)
            if user:
                user.experience += event.reward_xp
                event_data["xp"] = event.reward_xp
        
        # Логируем
        audit_log = AuditLog(
            user_id=user_id,
            action="event_triggered",
            details={
                "event_id": str(event.id),
                "event_name": event.name,
                "location_id": str(location_id),
                "rewards": event_data["rewards"]
            }
        )
        db.add(audit_log)
        
        await db.commit()
        
        return event_data
    
    async def _add_item_to_inventory(self, db: AsyncSession, user_id: uuid.UUID, 
                                    item_template: ItemTemplate, quantity: int):
        """Добавить предмет в инвентарь"""
        # Ищем инвентарь
        result = await db.execute(
            select(Inventory).where(Inventory.user_id == user_id)
        )
        inventory = result.scalar_one_or_none()
        
        if not inventory:
            inventory = Inventory(user_id=user_id)
            db.add(inventory)
            await db.flush()
        
        # Проверяем есть ли такой предмет в инвентаре
        result = await db.execute(
            select(Item).where(
                and_(
                    Item.owner_id == user_id,
                    Item.template_id == item_template.id
                )
            )
        )
        existing_item = result.scalar_one_or_none()
        
        if existing_item and item_template.stack_size > 1:
            # Увеличиваем количество
            existing_item.quantity += quantity
        else:
            # Создаем новый предмет
            new_item = Item(
                template_id=item_template.id,
                owner_id=user_id,
                quantity=quantity
            )
            db.add(new_item)
    
    # ============ АДМИН-МЕТОДЫ ============
    
    async def create_location(self, db: AsyncSession, data: Dict[str, Any]) -> Location:
        """Создать новую локацию (админ)"""
        location = Location(
            name=data["name"],
            description=data.get("description", ""),
            icon=data.get("icon", "📍"),
            location_type=data["location_type"],
            min_level=data.get("min_level", 1),
            max_level=data.get("max_level", 100),
            has_mine=data.get("has_mine", False),
            mine_level=data.get("mine_level", 0),
            has_forest=data.get("has_forest", False),
            has_herbs=data.get("has_herbs", False)
        )
        
        db.add(location)
        await db.flush()
        
        # Добавляем мобов
        if "mobs" in data:
            for mob_data in data["mobs"]:
                mob_spawn = MobSpawn(
                    location_id=location.id,
                    mob_template_id=uuid.UUID(mob_data["mob_template_id"]),
                    spawn_chance=mob_data["spawn_chance"],
                    min_level=mob_data.get("min_level", 1),
                    max_level=mob_data.get("max_level", 100),
                    max_count=mob_data.get("max_count", 10)
                )
                db.add(mob_spawn)
        
        # Добавляем ресурсы
        if "resources" in data:
            for resource_data in data["resources"]:
                resource_spawn = ResourceSpawn(
                    location_id=location.id,
                    resource_template_id=uuid.UUID(resource_data["resource_template_id"]),
                    spawn_chance=resource_data["spawn_chance"],
                    respawn_time=resource_data.get("respawn_time", 600),
                    max_count=resource_data.get("max_count", 100)
                )
                db.add(resource_spawn)
        
        await db.commit()
        
        return location
    
    async def create_travel_route(self, db: AsyncSession, data: Dict[str, Any]) -> TravelRoute:
        """Создать маршрут путешествия (админ)"""
        route = TravelRoute(
            from_location_id=uuid.UUID(data["from_location_id"]),
            to_location_id=uuid.UUID(data["to_location_id"]),
            travel_time=data["travel_time"],
            min_level=data.get("min_level", 1),
            gold_cost=data.get("gold_cost", 0)
        )
        
        db.add(route)
        await db.commit()
        
        return route
    
    async def create_resource(self, db: AsyncSession, data: Dict[str, Any]) -> ResourceTemplate:
        """Создать новый ресурс (админ)"""
        resource = ResourceTemplate(
            name=data["name"],
            description=data.get("description", ""),
            icon=data.get("icon", "⛏️"),
            resource_type=data["resource_type"],
            level=data.get("level", 1),
            gather_chance=data["gather_chance"],
            min_quantity=data.get("min_quantity", 1),
            max_quantity=data.get("max_quantity", 1),
            gather_time=data.get("gather_time", 60),
            required_strength=data.get("required_strength", 0),
            required_profession_level=data.get("required_profession_level", 1),
            weight=data.get("weight", 0.1),
            base_price=data.get("base_price", 10)
        )
        
        db.add(resource)
        await db.commit()
        
        return resource
    
    async def create_event(self, db: AsyncSession, data: Dict[str, Any]) -> GameEvent:
        """Создать новое событие (админ)"""
        event = GameEvent(
            name=data["name"],
            description=data.get("description", ""),
            icon=data.get("icon", "🎭"),
            event_type=data["event_type"],
            activation_type=data.get("activation_type", EventActivationType.CHANCE),
            base_chance=data.get("base_chance", 0.2),
            min_player_level=data.get("min_player_level", 1),
            max_player_level=data.get("max_player_level", 100),
            start_time=data.get("start_time"),
            end_time=data.get("end_time"),
            duration=data.get("duration", 3600),
            mob_power_modifier=data.get("mob_power_modifier", 1.0),
            resource_spawn_modifier=data.get("resource_spawn_modifier", 1.0),
            reward_gold_min=data.get("reward_gold_min", 0),
            reward_gold_max=data.get("reward_gold_max", 0),
            reward_xp=data.get("reward_xp", 0),
            is_active=data.get("is_active", False),
            is_repeatable=data.get("is_repeatable", True)
        )
        
        db.add(event)
        await db.flush()
        
        # Добавляем триггеры локаций
        if "locations" in data:
            for location_id in data["locations"]:
                trigger = EventTrigger(
                    event_id=event.id,
                    location_id=uuid.UUID(location_id),
                    trigger_chance=data.get("trigger_chance", 1.0)
                )
                db.add(trigger)
        
        # Добавляем награды
        if "rewards" in data:
            for reward_data in data["rewards"]:
                reward = EventReward(
                    event_id=event.id,
                    item_template_id=uuid.UUID(reward_data["item_template_id"]),
                    drop_chance=reward_data["drop_chance"],
                    min_quantity=reward_data.get("min_quantity", 1),
                    max_quantity=reward_data.get("max_quantity", 1)
                )
                db.add(reward)
        
        await db.commit()
        
        return event

# ============ ХЭНДЛЕРЫ ДЛЯ АДМИН-ПАНЕЛИ ============

@locations_router.callback_query(F.data.startswith("locations_admin_"))
async def handle_admin_locations(callback: CallbackQuery, state: FSMContext):
    """Обработчик админ-панели локаций"""
    action = callback.data.replace("locations_admin_", "")
    
    if action == "menu":
        await show_admin_locations_menu(callback)
    
    elif action == "create_location":
        await state.set_state(LocationStates.admin_create_location_name)
        await callback.message.edit_text(
            "📍 СОЗДАНИЕ НОВОЙ ЛОКАЦИИ\n\n"
            "Введите название локации:",
            reply_markup=create_cancel_keyboard()
        )
    
    elif action == "create_resource":
        await state.set_state(LocationStates.admin_create_resource_name)
        await callback.message.edit_text(
            "⛏️ СОЗДАНИЕ НОВОГО РЕСУРСА\n\n"
            "Введите название ресурса:",
            reply_markup=create_cancel_keyboard()
        )
    
    elif action == "create_route":
        await state.set_state(LocationStates.admin_create_travel_route)
        await callback.message.edit_text(
            "🛤️ СОЗДАНИЕ МАРШРУТА\n\n"
            "Введите данные в формате:\n"
            "ID_откуда:ID_куда:Время_сек:Уровень:Цена\n\n"
            "Пример:\n"
            "1234-5678-...:8765-4321-...:300:5:50",
            reply_markup=create_cancel_keyboard()
        )
    
    elif action == "create_event":
        await state.set_state(LocationStates.admin_create_event_basic)
        await callback.message.edit_text(
            "🎭 СОЗДАНИЕ СОБЫТИЯ\n\n"
            "Введите название события:",
            reply_markup=create_cancel_keyboard()
        )
    
    elif action == "list_locations":
        await show_locations_list(callback)
    
    elif action == "list_resources":
        await show_resources_list(callback)
    
    elif action == "list_events":
        await show_events_list(callback)

async def show_admin_locations_menu(callback: CallbackQuery):
    """Показать меню админ-панели локаций"""
    from database import get_db_session
    
    async with get_db_session() as db:
        # Получаем статистику
        locations_count = await db.execute(select(func.count(Location.id)))
        locations_count = locations_count.scalar()
        
        resources_count = await db.execute(select(func.count(ResourceTemplate.id)))
        resources_count = resources_count.scalar()
        
        events_count = await db.execute(select(func.count(GameEvent.id)))
        events_count = events_count.scalar()
        
        active_events = await db.execute(
            select(func.count(GameEvent.id)).where(GameEvent.is_active == True)
        )
        active_events = active_events.scalar()
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📍 Создать локацию", callback_data="locations_admin_create_location")],
        [InlineKeyboardButton(text="📍 Список локаций", callback_data="locations_admin_list_locations")],
        [InlineKeyboardButton(text="⛏️ Создать ресурс", callback_data="locations_admin_create_resource")],
        [InlineKeyboardButton(text="⛏️ Список ресурсов", callback_data="locations_admin_list_resources")],
        [InlineKeyboardButton(text="🛤️ Создать маршрут", callback_data="locations_admin_create_route")],
        [InlineKeyboardButton(text="🎭 Создать событие", callback_data="locations_admin_create_event")],
        [InlineKeyboardButton(text="🎭 Список событий", callback_data="locations_admin_list_events")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="locations_admin_stats")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")]
    ])
    
    await callback.message.edit_text(
        f"🗺️ АДМИН-ПАНЕЛЬ ЛОКАЦИЙ\n\n"
        f"📍 Локаций: {locations_count}\n"
        f"⛏️ Ресурсов: {resources_count}\n"
        f"🎭 Событий: {events_count} (активных: {active_events})\n\n"
        "Выберите действие:",
        reply_markup=keyboard
    )

async def show_locations_list(callback: CallbackQuery):
    """Показать список локаций"""
    from database import get_db_session
    
    async with get_db_session() as db:
        locations = await db.execute(
            select(Location).order_by(Location.min_level)
        )
        locations = locations.scalars().all()
        
        text = "📍 СПИСОК ЛОКАЦИЙ\n\n"
        
        keyboard_buttons = []
        for location in locations:
            text += f"• {location.icon} {location.name}\n"
            text += f"  Уровень: {location.min_level}-{location.max_level}\n"
            text += f"  Тип: {location.location_type.value}\n\n"
            
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"✏️ {location.name[:15]}...",
                    callback_data=f"location_edit_{location.id}"
                )
            ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="locations_admin_menu")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard)

async def show_resources_list(callback: CallbackQuery):
    """Показать список ресурсов"""
    from database import get_db_session
    
    async with get_db_session() as db:
        resources = await db.execute(
            select(ResourceTemplate).order_by(ResourceTemplate.level)
        )
        resources = resources.scalars().all()
        
        text = "⛏️ СПИСОК РЕСУРСОВ\n\n"
        
        keyboard_buttons = []
        for resource in resources:
            text += f"• {resource.icon} {resource.name}\n"
            text += f"  Уровень: {resource.level} | Тип: {resource.resource_type.value}\n"
            text += f"  Шанс: {resource.gather_chance*100:.1f}% | Количество: {resource.min_quantity}-{resource.max_quantity}\n\n"
            
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"✏️ {resource.name[:15]}...",
                    callback_data=f"resource_edit_{resource.id}"
                )
            ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="locations_admin_menu")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard)

async def show_events_list(callback: CallbackQuery):
    """Показать список событий"""
    from database import get_db_session
    
    async with get_db_session() as db:
        events = await db.execute(
            select(GameEvent).order_by(GameEvent.name)
        )
        events = events.scalars().all()
        
        text = "🎭 СПИСОК СОБЫТИЙ\n\n"
        
        keyboard_buttons = []
        for event in events:
            status = "✅" if event.is_active else "❌"
            text += f"• {event.icon} {event.name} {status}\n"
            text += f"  Тип: {event.event_type.value} | Шанс: {event.base_chance*100:.1f}%\n\n"
            
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"{status} {event.name[:15]}...",
                    callback_data=f"event_toggle_{event.id}"
                ),
                InlineKeyboardButton(
                    text="✏️",
                    callback_data=f"event_edit_{event.id}"
                )
            ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="locations_admin_menu")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard)

# ============ ХЭНДЛЕРЫ ДЛЯ ИГРОКОВ ============

@locations_router.callback_query(F.data.startswith("locations_"))
async def handle_player_locations(callback: CallbackQuery, state: FSMContext):
    """Обработчик локаций для игроков"""
    action = callback.data.replace("locations_", "")
    
    if action == "menu":
        await show_location_menu(callback)
    
    elif action == "explore":
        await explore_location_handler(callback)
    
    elif action == "travel":
        await state.set_state(LocationStates.location_selection)
        await show_travel_locations(callback)
    
    elif action == "mine":
        await mine_location_handler(callback)
    
    elif action == "gather_wood":
        await gather_wood_handler(callback)
    
    elif action == "gather_herbs":
        await gather_herbs_handler(callback)

async def show_location_menu(callback: CallbackQuery):
    """Показать меню локации"""
    from database import get_db_session
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        location = await db.get(Location, user.current_location_id)
        
        if not location:
            await callback.answer("Локация не найдена")
            return
        
        # Проверяем активные действия
        active_travel = await db.execute(
            select(ActiveAction).where(
                and_(
                    ActiveAction.user_id == user.id,
                    ActiveAction.action_type == ActionType.TRAVEL,
                    ActiveAction.is_completed == False
                )
            )
        )
        active_travel = active_travel.scalar_one_or_none()
        
        active_gathering = await db.execute(
            select(ActiveAction).where(
                and_(
                    ActiveAction.user_id == user.id,
                    ActiveAction.action_type.in_([
                        ActionType.MINING, 
                        ActionType.WOODCUTTING, 
                        ActionType.HERBALISM
                    ]),
                    ActiveAction.is_completed == False
                )
            )
        )
        active_gathering = active_gathering.scalar_one_or_none()
        
        text = f"{location.icon} {location.name}\n\n"
        text += f"{location.description or 'Нет описания'}\n\n"
        text += f"📊 Уровень: {location.min_level}-{location.max_level}\n"
        text += f"⚔️ Тип: {location.location_type.value}\n\n"
        
        if active_travel:
            remaining = (active_travel.end_time - datetime.utcnow()).seconds
            text += f"🛤️ В пути: {remaining // 60}:{remaining % 60:02d}\n"
        
        if active_gathering:
            remaining = (active_gathering.end_time - datetime.utcnow()).seconds
            action_name = {
                ActionType.MINING: "⛏️ Добыча руды",
                ActionType.WOODCUTTING: "🌳 Рубка дерева",
                ActionType.HERBALISM: "🌿 Сбор трав"
            }.get(active_gathering.action_type, "Действие")
            text += f"{action_name}: {remaining // 60}:{remaining % 60:02d}\n"
        
        keyboard_buttons = [
            [InlineKeyboardButton(text="👀 Осмотреться", callback_data="locations_explore")],
            [InlineKeyboardButton(text="🗺️ Путешествовать", callback_data="locations_travel")]
        ]
        
        if location.has_mine:
            keyboard_buttons.append([
                InlineKeyboardButton(text="⛏️ Шахта", callback_data="locations_mine")
            ])
        
        if location.has_forest:
            keyboard_buttons.append([
                InlineKeyboardButton(text="🌳 Рубка дерева", callback_data="locations_gather_wood")
            ])
        
        if location.has_herbs:
            keyboard_buttons.append([
                InlineKeyboardButton(text="🌿 Сбор трав", callback_data="locations_gather_herbs")
            ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard)

async def explore_location_handler(callback: CallbackQuery):
    """Обработчик осмотра локации"""
    from database import get_db_session
    from main import location_manager
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        exploration = await location_manager.explore_location(db, user.id)
        
        if "error" in exploration:
            await callback.answer(exploration["error"])
            return
        
        location = exploration["location"]
        mobs = exploration["mobs"]
        resources = exploration["resources"]
        events = exploration["events"]
        
        text = f"👀 {location['name']}\n\n"
        
        if mobs:
            text += "👹 ВРАГИ:\n"
            for mob in mobs:
                text += f"[{mob['icon']}] {mob['name']} ×{mob['count']}\n"
                text += f"• Уровень: {mob['level']}\n"
                text += f"• Здоровье: {mob['health']}/{mob['health']}\n\n"
        
        if resources:
            text += "🌿 РЕСУРСЫ:\n"
            for resource in resources:
                text += f"[{resource['icon']}] {resource['name']}\n"
                text += f"• Шанс: {resource['chance']*100:.0f}%\n"
                text += f"• Количество: {resource['min_quantity']}-{resource['max_quantity']}\n\n"
        
        if events:
            text += "🎭 СОБЫТИЯ:\n"
            for event in events:
                text += f"[{event['icon']}] {event['name']}\n"
                text += f"• Тип: {event['type']}\n"
                text += f"• {event['description']}\n\n"
        
        keyboard_buttons = []
        
        # Кнопки для боя с мобами
        if mobs:
            for mob in mobs[:3]:  # Ограничиваем 3 мобами
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"⚔️ Сразиться с {mob['name']}",
                        callback_data=f"battle_mob_{mob['id']}"
                    )
                ])
        
        # Кнопки для сбора ресурсов
        if resources:
            for resource in resources[:3]:
                if resource['type'] == "ore":
                    action = "mine"
                elif resource['type'] == "wood":
                    action = "gather_wood"
                elif resource['type'] == "herb":
                    action = "gather_herbs"
                else:
                    continue
                
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"{resource['icon']} Собрать {resource['name']}",
                        callback_data=f"locations_{action}_{resource['id']}"
                    )
                ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="locations_menu")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard)

async def show_travel_locations(callback: CallbackQuery):
    """Показать доступные для путешествия локации"""
    from database import get_db_session
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        # Получаем доступные маршруты
        routes = await db.execute(
            select(TravelRoute).where(
                TravelRoute.from_location_id == user.current_location_id
            ).options(
                selectinload(TravelRoute.to_location)
            )
        )
        routes = routes.scalars().all()
        
        if not routes:
            await callback.message.edit_text(
                "🛤️ Нет доступных маршрутов из этой локации.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="locations_menu")]
                ])
            )
            return
        
        text = "🗺️ КУДА ОТПРАВИТЬСЯ?\n\n"
        
        keyboard_buttons = []
        for route in routes:
            if route.to_location:
                text += f"{route.to_location.icon} {route.to_location.name}\n"
                text += f"• Время: {route.travel_time // 60}:{route.travel_time % 60:02d}\n"
                text += f"• Уровень: {route.min_level}+ | Цена: {route.gold_cost} золота\n\n"
                
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"{route.to_location.icon} {route.to_location.name}",
                        callback_data=f"travel_to_{route.to_location_id}"
                    )
                ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="locations_menu")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard)

async def mine_location_handler(callback: CallbackQuery):
    """Обработчик шахты"""
    from database import get_db_session
    from main import location_manager
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        location = await db.get(Location, user.current_location_id)
        
        if not location or not location.has_mine:
            await callback.answer("В этой локации нет шахты")
            return
        
        # Получаем доступные руды для этого уровня шахты
        resources = await db.execute(
            select(ResourceTemplate).where(
                and_(
                    ResourceTemplate.resource_type == ResourceType.ORE,
                    ResourceTemplate.level <= location.mine_level
                )
            ).order_by(ResourceTemplate.level)
        )
        resources = resources.scalars().all()
        
        text = f"⛏️ ШАХТА УРОВНЯ {location.mine_level}\n\n"
        text += "Доступные руды:\n\n"
        
        keyboard_buttons = []
        for resource in resources:
            text += f"[{resource.icon}] {resource.name}\n"
            text += f"• Уровень: {resource.level}\n"
            text += f"• Шанс: {resource.gather_chance*100:.0f}%\n"
            text += f"• Количество: {resource.min_quantity}-{resource.max_quantity}\n"
            text += f"• Время: {resource.gather_time // 60}:{resource.gather_time % 60:02d}\n\n"
            
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"⛏️ Добывать {resource.name}",
                    callback_data=f"locations_mine_resource_{resource.id}"
                )
            ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="locations_menu")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard)

# ============ УТИЛИТЫ ============

def create_cancel_keyboard() -> InlineKeyboardMarkup:
    """Создать клавиатуру с кнопкой отмены"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])

def create_location_keyboard() -> InlineKeyboardMarkup:
    """Создать клавиатуру для локации"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👀 Осмотреться", callback_data="locations_explore"),
            InlineKeyboardButton(text="🗺️ Путешествовать", callback_data="locations_travel")
        ],
        [
            InlineKeyboardButton(text="⛏️ Шахта", callback_data="locations_mine"),
            InlineKeyboardButton(text="🌳 Лес", callback_data="locations_forest")
        ],
        [
            InlineKeyboardButton(text="🌿 Травы", callback_data="locations_herbs"),
            InlineKeyboardButton(text="⚔️ Охота", callback_data="locations_hunt")
        ],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    ])

# ============ ИНИЦИАЛИЗАЦИЯ ============

async def init_locations_module(redis_client, db_session_factory):
    """Инициализировать модуль локаций"""
    location_manager = LocationManager(redis_client, db_session_factory)
    await location_manager.restore_state()
    return location_manager

# ============ ХЭНДЛЕРЫ КОМАНД ============

@locations_router.callback_query(F.data == "locations_menu")
async def handle_locations_menu(callback: CallbackQuery):
    """Обработчик меню локаций"""
    await show_location_menu(callback)

@locations_router.callback_query(F.data.startswith("travel_to_"))
async def handle_travel_to(callback: CallbackQuery):
    """Обработчик путешествия"""
    from database import get_db_session
    from main import location_manager
    
    location_id = uuid.UUID(callback.data.replace("travel_to_", ""))
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        result = await location_manager.travel_to_location(db, user.id, location_id)
        
        if "error" in result:
            await callback.answer(result["error"])
            return
        
        travel_time = result["travel_time"]
        minutes = travel_time // 60
        seconds = travel_time % 60
        
        await callback.message.edit_text(
            f"🛤️ ВЫ ОТПРАВИЛИСЬ В ПУТЬ!\n\n"
            f"Время в пути: {minutes}:{seconds:02d}\n"
            f"Прибытие: <code>{result['end_time'].strftime('%H:%M:%S')}</code>\n\n"
            f"Вы получите уведомление по прибытии.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗺️ Текущая локация", callback_data="locations_menu")]
            ])
        )

@locations_router.callback_query(F.data.startswith("locations_mine_resource_"))
async def handle_mine_resource(callback: CallbackQuery):
    """Обработчик добычи ресурса"""
    from database import get_db_session
    from main import location_manager
    
    resource_id = uuid.UUID(callback.data.replace("locations_mine_resource_", ""))
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        result = await location_manager.gather_resource(db, user.id, resource_id, ActionType.MINING)
        
        if "error" in result:
            await callback.answer(result["error"])
            return
        
        gather_time = result["gather_time"]
        minutes = gather_time // 60
        seconds = gather_time % 60
        
        await callback.message.edit_text(
            f"⛏️ ВЫ НАЧАЛИ ДОБЫВАТЬ РУДУ!\n\n"
            f"Ресурс: {result['resource_name']}\n"
            f"Время: {minutes}:{seconds:02d}\n"
            f"Завершение: <code>{result['end_time'].strftime('%H:%M:%S')}</code>\n\n"
            f"Вы получите уведомление по завершении.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⛏️ Шахта", callback_data="locations_mine")]
            ])
        )

# Экспортируемые объекты
__all__ = [
    'locations_router',
    'LocationManager',
    'init_locations_module',
    'LocationStates',
    'TravelStatus'
]