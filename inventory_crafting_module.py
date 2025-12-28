# inventory_crafting_module.py
"""
Полный модуль инвентаря и крафтинга с восстановлением состояния.
Включает управление инвентарем, экипировку, торговлю и систему крафта.
"""

import asyncio
import json
import random
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any, Union
from enum import Enum
import uuid
from dataclasses import dataclass, field

from aiogram import Router, F, types, html
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    CallbackQuery, Message
)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, update, and_, or_, desc, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload

from models import (
    User, Item, ItemTemplate, ItemType, ItemRarity, Inventory,
    Recipe, RecipeIngredient, ProfessionType, ActiveAction, ActionType,
    StateSnapshot, AuditLog, SystemSettings, Location, ResourceType,
    ActiveEffect
)

# ============ КОНСТАНТЫ ============

class InventoryAction(str, Enum):
    VIEW = "view"
    EQUIP = "equip"
    UNEQUIP = "unequip"
    USE = "use"
    DROP = "drop"
    SELL = "sell"
    TRADE = "trade"
    SORT = "sort"
    SEARCH = "search"

class CraftingStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class SortType(str, Enum):
    NAME = "name"
    LEVEL = "level"
    RARITY = "rarity"
    TYPE = "type"
    DATE = "date"
    VALUE = "value"

# ============ РОУТЕР И СОСТОЯНИЯ ============

inventory_router = Router()

class InventoryStates(StatesGroup):
    # Основные состояния
    main_menu = State()
    inventory_view = State()
    inventory_sort = State()
    inventory_search = State()
    
    # Управление предметами
    item_details = State()
    item_equip = State()
    item_unequip = State()
    item_use = State()
    item_drop = State()
    item_sell = State()
    item_sell_confirm = State()
    
    # Крафт
    crafting_menu = State()
    crafting_profession = State()
    crafting_recipes = State()
    crafting_recipe_details = State()
    crafting_start = State()
    crafting_in_progress = State()
    
    # Торговля
    trading_menu = State()
    trading_sell = State()
    trading_buy = State()
    trading_auction = State()
    trading_offer = State()
    
    # Аукцион
    auction_menu = State()
    auction_browse = State()
    auction_create = State()
    auction_bid = State()
    
    # Хранилище
    storage_menu = State()
    storage_deposit = State()
    storage_withdraw = State()
    
    # Ремонт
    repair_menu = State()
    repair_select = State()
    repair_confirm = State()

# ============ МОДЕЛИ ДАННЫХ ============

@dataclass
class ItemSlot:
    """Слот для предмета"""
    name: str
    icon: str
    item_type: ItemType
    can_equip: bool
    
@dataclass
class CraftingResult:
    """Результат крафта"""
    success: bool
    item: Optional[Item] = None
    quantity: int = 1
    quality: float = 1.0
    experience: int = 0
    message: str = ""

@dataclass
class AuctionItem:
    """Предмет на аукционе"""
    id: uuid.UUID
    seller_id: uuid.UUID
    item: Item
    start_price: int
    current_bid: int
    buyout_price: Optional[int] = None
    bids_count: int = 0
    end_time: datetime = field(default_factory=datetime.utcnow)
    highest_bidder: Optional[uuid.UUID] = None

# ============ МЕНЕДЖЕР ИНВЕНТАРЯ ============

class InventoryManager:
    """Менеджер для управления инвентарем и крафтом"""
    
    def __init__(self, redis_client, db_session_factory):
        self.redis = redis_client
        self.db_session_factory = db_session_factory
        self.active_crafts = {}  # {user_id: crafting_data}
        self.auction_items = {}  # {auction_id: auction_data}
        self.item_slots = self._init_item_slots()
        
    def _init_item_slots(self) -> Dict[str, ItemSlot]:
        """Инициализировать слоты для экипировки"""
        return {
            "weapon": ItemSlot("Оружие", "⚔️", ItemType.WEAPON, True),
            "armor": ItemSlot("Броня", "🛡️", ItemType.ARMOR, True),
            "helmet": ItemSlot("Шлем", "⛑️", ItemType.ARMOR, True),
            "gloves": ItemSlot("Перчатки", "🧤", ItemType.ARMOR, True),
            "boots": ItemSlot("Ботинки", "👢", ItemType.ARMOR, True),
            "ring1": ItemSlot("Кольцо 1", "💍", ItemType.OTHER, True),
            "ring2": ItemSlot("Кольцо 2", "💍", ItemType.OTHER, True),
            "amulet": ItemSlot("Амулет", "📿", ItemType.OTHER, True),
            "artifact": ItemSlot("Артефакт", "✨", ItemType.OTHER, True)
        }
    
    async def restore_state(self):
        """Восстановить все активные состояния"""
        async with self.db_session_factory() as db:
            try:
                # 1. Восстановить активные крафты
                result = await db.execute(
                    select(ActiveAction).where(
                        and_(
                            ActiveAction.action_type == ActionType.CRAFTING,
                            ActiveAction.is_completed == False
                        )
                    ).options(selectinload(ActiveAction.user))
                )
                crafts = result.scalars().all()
                
                for craft in crafts:
                    if craft.end_time < datetime.utcnow():
                        # Крафт завершен
                        await self.complete_crafting(db, craft)
                    else:
                        craft_key = f"crafting:{craft.user_id}"
                        craft_data = {
                            "action_id": str(craft.id),
                            "user_id": str(craft.user_id),
                            "recipe_id": str(craft.target_id) if craft.target_id else None,
                            "start_time": craft.start_time.isoformat(),
                            "end_time": craft.end_time.isoformat(),
                            "progress": craft.progress,
                            "data": craft.data or {}
                        }
                        
                        remaining_time = (craft.end_time - datetime.utcnow()).seconds
                        await self.redis.setex(
                            craft_key,
                            remaining_time,
                            json.dumps(craft_data)
                        )
                        self.active_crafts[str(craft.user_id)] = craft_data
                
                # 2. Восстановить снапшоты
                result = await db.execute(
                    select(StateSnapshot).where(
                        and_(
                            StateSnapshot.is_restored == False,
                            StateSnapshot.expires_at > datetime.utcnow(),
                            StateSnapshot.snapshot_type.in_(["crafting", "auction", "trade"])
                        )
                    )
                )
                snapshots = result.scalars().all()
                
                for snapshot in snapshots:
                    await self.restore_from_snapshot(db, snapshot)
                
                await db.commit()
                print(f"✅ Восстановлено {len(crafts)} активных крафтов")
                
            except Exception as e:
                print(f"❌ Ошибка восстановления инвентаря: {e}")
                await db.rollback()
    
    async def restore_from_snapshot(self, db: AsyncSession, snapshot: StateSnapshot):
        """Восстановить из снапшота"""
        try:
            snapshot_data = snapshot.snapshot_data
            snapshot_type = snapshot.snapshot_type
            
            if snapshot_type == "crafting":
                await self.restore_crafting(db, snapshot)
            elif snapshot_type == "auction":
                await self.restore_auction(db, snapshot)
            
            snapshot.is_restored = True
            
        except Exception as e:
            print(f"❌ Ошибка восстановления из снапшота: {e}")
    
    async def restore_crafting(self, db: AsyncSession, snapshot: StateSnapshot):
        """Восстановить крафт"""
        snapshot_data = snapshot.snapshot_data
        user_id = snapshot.user_id
        
        # Проверяем не завершился ли крафт
        end_time = datetime.fromisoformat(snapshot_data.get("end_time"))
        if end_time < datetime.utcnow():
            return
        
        # Создаем новое активное действие
        craft = ActiveAction(
            id=uuid.uuid4(),
            user_id=user_id,
            action_type=ActionType.CRAFTING,
            target_id=uuid.UUID(snapshot_data.get("recipe_id")),
            start_time=datetime.fromisoformat(snapshot_data.get("start_time")),
            end_time=end_time,
            progress=snapshot_data.get("progress", 0),
            data=snapshot_data.get("craft_data", {})
        )
        
        db.add(craft)
        
        # Сохраняем в Redis
        craft_key = f"crafting:{user_id}"
        craft_data = {
            "action_id": str(craft.id),
            "user_id": str(user_id),
            "recipe_id": str(craft.target_id),
            "start_time": craft.start_time.isoformat(),
            "end_time": craft.end_time.isoformat(),
            "progress": craft.progress,
            "data": craft.data or {}
        }
        
        remaining_time = (craft.end_time - datetime.utcnow()).seconds
        await self.redis.setex(
            craft_key,
            remaining_time,
            json.dumps(craft_data)
        )
        self.active_crafts[str(user_id)] = craft_data
    
    # ============ ИНВЕНТАРЬ ============
    
    async def get_inventory(self, db: AsyncSession, user_id: uuid.UUID) -> Dict[str, Any]:
        """Получить инвентарь игрока"""
        # Получаем инвентарь
        result = await db.execute(
            select(Inventory)
            .where(Inventory.user_id == user_id)
            .options(
                selectinload(Inventory.items)
                .selectinload(Item.template)
            )
        )
        inventory = result.scalar_one_or_none()
        
        if not inventory:
            inventory = Inventory(user_id=user_id)
            db.add(inventory)
            await db.commit()
        
        # Получаем пользователя для экипировки
        user = await db.get(User, user_id)
        
        # Получаем экипированные предметы
        equipped_items = await self.get_equipped_items(db, user)
        
        # Рассчитываем статистику инвентаря
        total_items = len(inventory.items) if inventory.items else 0
        total_weight = sum(
            (item.template.weight * item.quantity) 
            for item in inventory.items 
            if item.template
        ) if inventory.items else 0
        
        # Рассчитываем стоимость
        total_value = sum(
            (item.template.base_price * item.quantity)
            for item in inventory.items
            if item.template
        ) if inventory.items else 0
        
        return {
            "inventory": inventory,
            "equipped_items": equipped_items,
            "stats": {
                "total_items": total_items,
                "capacity": inventory.capacity,
                "max_capacity": inventory.max_capacity,
                "total_weight": total_weight,
                "total_value": total_value,
                "used_slots": total_items,
                "free_slots": inventory.capacity - total_items
            }
        }
    
    async def get_equipped_items(self, db: AsyncSession, user: User) -> Dict[str, Optional[Item]]:
        """Получить экипированные предметы"""
        equipped = {}
        
        # Получаем все экипированные предметы
        item_ids = [
            user.weapon_id,
            user.armor_id,
            user.helmet_id,
            user.gloves_id,
            user.boots_id
        ]
        
        for slot_name, item_id in zip(self.item_slots.keys(), item_ids):
            if item_id:
                item = await db.get(Item, item_id)
                if item:
                    equipped[slot_name] = item
        
        return equipped
    
    async def get_inventory_items(self, db: AsyncSession, user_id: uuid.UUID, 
                                 page: int = 1, page_size: int = 20,
                                 sort_by: SortType = SortType.NAME,
                                 filter_type: Optional[ItemType] = None,
                                 search_query: Optional[str] = None) -> Tuple[List[Item], int]:
        """Получить предметы из инвентаря с пагинацией и фильтрацией"""
        offset = (page - 1) * page_size
        
        # Базовый запрос
        query = select(Item).where(
            and_(
                Item.owner_id == user_id,
                Item.is_equipped == False
            )
        ).options(selectinload(Item.template))
        
        # Применяем фильтры
        if filter_type:
            query = query.where(Item.template.has(ItemTemplate.item_type == filter_type))
        
        if search_query:
            query = query.where(
                Item.template.has(
                    ItemTemplate.name.ilike(f"%{search_query}%")
                )
            )
        
        # Применяем сортировку
        if sort_by == SortType.NAME:
            query = query.order_by(Item.template.has(ItemTemplate.name))
        elif sort_by == SortType.LEVEL:
            query = query.order_by(desc(Item.template.has(ItemTemplate.level_requirement)))
        elif sort_by == SortType.RARITY:
            # Конвертируем редкость в числовое значение для сортировки
            rarity_order = {
                ItemRarity.COMMON: 1,
                ItemRarity.UNCOMMON: 2,
                ItemRarity.RARE: 3,
                ItemRarity.EPIC: 4,
                ItemRarity.LEGENDARY: 5,
                ItemRarity.MYTHIC: 6
            }
            # Сложная сортировка по редкости
            query = query.order_by(
                desc(
                    func.coalesce(
                        func.case(
                            *[(Item.template.has(ItemTemplate.rarity == rarity), value) 
                              for rarity, value in rarity_order.items()],
                            else_=0
                        ),
                        0
                    )
                )
            )
        elif sort_by == SortType.TYPE:
            query = query.order_by(Item.template.has(ItemTemplate.item_type))
        elif sort_by == SortType.DATE:
            query = query.order_by(desc(Item.created_at))
        elif sort_by == SortType.VALUE:
            query = query.order_by(desc(Item.template.has(ItemTemplate.base_price)))
        
        # Получаем предметы
        result = await db.execute(
            query.offset(offset).limit(page_size)
        )
        items = result.scalars().all()
        
        # Считаем общее количество
        count_query = select(func.count(Item.id)).where(
            and_(
                Item.owner_id == user_id,
                Item.is_equipped == False
            )
        )
        
        if filter_type:
            count_query = count_query.where(
                Item.template.has(ItemTemplate.item_type == filter_type)
            )
        
        if search_query:
            count_query = count_query.where(
                Item.template.has(
                    ItemTemplate.name.ilike(f"%{search_query}%")
                )
            )
        
        total_result = await db.execute(count_query)
        total = total_result.scalar()
        
        return items, total
    
    async def get_item_details(self, db: AsyncSession, item_id: uuid.UUID) -> Dict[str, Any]:
        """Получить детали предмета"""
        item = await db.get(Item, item_id)
        if not item:
            return {}
        
        template = item.template
        if not template:
            return {}
        
        # Базовые детали
        details = {
            "item": item,
            "template": template,
            "basic_info": {
                "name": template.name,
                "icon": template.icon,
                "type": template.item_type.value,
                "rarity": template.rarity.value,
                "level_requirement": template.level_requirement,
                "description": template.description or "Нет описания"
            }
        }
        
        # Статистика в зависимости от типа
        if template.item_type == ItemType.WEAPON:
            details["stats"] = {
                "damage": f"{template.damage_min}-{template.damage_max}",
                "attack_speed": "Стандартная",
                "durability": f"{item.current_durability}/{item.max_durability}" 
                    if item.current_durability else "Неограниченная"
            }
        elif template.item_type == ItemType.ARMOR:
            details["stats"] = {
                "defense": template.defense or 0,
                "health_bonus": template.health_bonus or 0,
                "durability": f"{item.current_durability}/{item.max_durability}" 
                    if item.current_durability else "Неограниченная"
            }
        elif template.item_type == ItemType.POTION:
            details["stats"] = {
                "effect": template.potion_effect or {},
                "consumable": True
            }
        elif template.item_type == ItemType.RESOURCE:
            details["stats"] = {
                "quantity": item.quantity,
                "weight": template.weight,
                "stack_size": template.stack_size,
                "resource_type": template.resource_type.value if template.resource_type else "Обычный"
            }
        
        # Бонусы характеристик
        bonuses = []
        if template.strength_bonus:
            bonuses.append(f"Сила: +{template.strength_bonus}")
        if template.agility_bonus:
            bonuses.append(f"Ловкость: +{template.agility_bonus}")
        if template.intelligence_bonus:
            bonuses.append(f"Интеллект: +{template.intelligence_bonus}")
        if template.constitution_bonus:
            bonuses.append(f"Телосложение: +{template.constitution_bonus}")
        
        if bonuses:
            details["bonuses"] = bonuses
        
        # Экономика
        details["economy"] = {
            "base_price": template.base_price,
            "sell_price": template.sell_price,
            "market_value": template.base_price * (1 + (["common", "uncommon", "rare", "epic", "legendary", "mythic"].index(template.rarity.value) * 0.5)),
            "tradable": template.is_tradable,
            "droppable": template.is_droppable
        }
        
        # Зачарования
        if item.enchantments:
            details["enchantments"] = item.enchantments
        
        # Состояние предмета
        details["state"] = {
            "equipped": item.is_equipped,
            "owner_id": item.owner_id,
            "quantity": item.quantity,
            "created_at": item.created_at
        }
        
        return details
    
    async def equip_item(self, db: AsyncSession, user_id: uuid.UUID, item_id: uuid.UUID) -> Tuple[bool, str, Optional[Item]]:
        """Экипировать предмет"""
        user = await db.get(User, user_id)
        item = await db.get(Item, item_id)
        
        if not user or not item:
            return False, "Предмет или игрок не найден", None
        
        if item.owner_id != user_id:
            return False, "Этот предмет не принадлежит вам", None
        
        if item.is_equipped:
            return False, "Предмет уже экипирован", None
        
        template = item.template
        if not template:
            return False, "Шаблон предмета не найден", None
        
        if not template.is_equippable:
            return False, "Этот предмет нельзя экипировать", None
        
        # Проверяем уровень
        if user.level < template.level_requirement:
            return False, f"Требуется уровень {template.level_requirement}", None
        
        # Определяем слот
        slot = self._get_item_slot(template.item_type)
        if not slot:
            return False, "Неизвестный тип предмета", None
        
        # Проверяем свободен ли слот
        current_item_id = getattr(user, f"{slot}_id", None)
        unequipped_item = None
        
        if current_item_id:
            # Снимаем текущий предмет
            current_item = await db.get(Item, current_item_id)
            if current_item:
                current_item.is_equipped = False
                unequipped_item = current_item
        
        # Экипируем новый предмет
        item.is_equipped = True
        setattr(user, f"{slot}_id", item.id)
        
        # Обновляем характеристики игрока
        await self._update_player_stats_from_equipment(db, user)
        
        # Логируем действие
        audit_log = AuditLog(
            user_id=user_id,
            action="item_equipped",
            details={
                "item_id": str(item_id),
                "item_name": template.name,
                "slot": slot
            }
        )
        db.add(audit_log)
        
        await db.commit()
        
        return True, f"Предмет '{template.name}' экипирован в слот '{self.item_slots[slot].name}'", unequipped_item
    
    async def unequip_item(self, db: AsyncSession, user_id: uuid.UUID, slot: str) -> Tuple[bool, str, Optional[Item]]:
        """Снять предмет"""
        user = await db.get(User, user_id)
        
        if not user:
            return False, "Игрок не найден", None
        
        # Проверяем существует ли слот
        if slot not in self.item_slots:
            return False, "Неизвестный слот", None
        
        # Получаем ID предмета в слоте
        item_id = getattr(user, f"{slot}_id", None)
        if not item_id:
            return False, "В этом слоте нет предмета", None
        
        # Получаем предмет
        item = await db.get(Item, item_id)
        if not item:
            return False, "Предмет не найден", None
        
        # Снимаем предмет
        item.is_equipped = False
        setattr(user, f"{slot}_id", None)
        
        # Обновляем характеристики игрока
        await self._update_player_stats_from_equipment(db, user)
        
        # Логируем действие
        audit_log = AuditLog(
            user_id=user_id,
            action="item_unequipped",
            details={
                "item_id": str(item_id),
                "item_name": item.template.name if item.template else "Unknown",
                "slot": slot
            }
        )
        db.add(audit_log)
        
        await db.commit()
        
        return True, f"Предмет снят", item
    
    def _get_item_slot(self, item_type: ItemType) -> Optional[str]:
        """Получить слот для типа предмета"""
        if item_type == ItemType.WEAPON:
            return "weapon"
        elif item_type == ItemType.ARMOR:
            # Для брони нужно определять точный слот по названию или другим атрибутам
            # В упрощенной версии используем "armor" для всей брони
            return "armor"
        return None
    
    async def _update_player_stats_from_equipment(self, db: AsyncSession, user: User):
        """Обновить характеристики игрока на основе экипировки"""
        # Сбрасываем бонусы
        equipment_stats = {
            "strength": 0,
            "agility": 0,
            "intelligence": 0,
            "constitution": 0,
            "health_bonus": 0,
            "mana_bonus": 0,
            "defense": 0
        }
        
        # Собираем экипированные предметы
        item_ids = [
            user.weapon_id,
            user.armor_id,
            user.helmet_id,
            user.gloves_id,
            user.boots_id
        ]
        
        for item_id in item_ids:
            if item_id:
                item = await db.get(Item, item_id)
                if item and item.template:
                    template = item.template
                    equipment_stats["strength"] += template.strength_bonus or 0
                    equipment_stats["agility"] += template.agility_bonus or 0
                    equipment_stats["intelligence"] += template.intelligence_bonus or 0
                    equipment_stats["constitution"] += template.constitution_bonus or 0
                    equipment_stats["health_bonus"] += template.health_bonus or 0
                    equipment_stats["mana_bonus"] += template.mana_bonus or 0
                    equipment_stats["defense"] += template.defense or 0
        
        # Сохраняем бонусы в виде JSON в дополнительном поле пользователя
        # или пересчитываем максимальные HP/MP
        user.max_hp = await self._calculate_max_hp(db, user, equipment_stats["health_bonus"])
        user.max_mp = await self._calculate_max_mp(db, user, equipment_stats["mana_bonus"])
        
        # Ограничиваем текущие HP/MP
        user.current_hp = min(user.current_hp, user.max_hp)
        user.current_mp = min(user.current_mp, user.max_mp)
    
    async def _calculate_max_hp(self, db: AsyncSession, user: User, equipment_bonus: int = 0) -> int:
        """Рассчитать максимальное HP"""
        base_hp = 100
        constitution_bonus = user.constitution * 5
        level_bonus = user.level * 10
        
        max_hp = base_hp + constitution_bonus + level_bonus + equipment_bonus
        return max(100, int(max_hp))
    
    async def _calculate_max_mp(self, db: AsyncSession, user: User, equipment_bonus: int = 0) -> int:
        """Рассчитать максимальную MP"""
        base_mp = 50
        intelligence_bonus = user.intelligence * 3
        level_bonus = user.level * 5
        
        max_mp = base_mp + intelligence_bonus + level_bonus + equipment_bonus
        return max(50, int(max_mp))
    
    async def use_item(self, db: AsyncSession, user_id: uuid.UUID, item_id: uuid.UUID) -> Tuple[bool, str, Dict[str, Any]]:
        """Использовать предмет"""
        user = await db.get(User, user_id)
        item = await db.get(Item, item_id)
        
        if not user or not item:
            return False, "Предмет или игрок не найден", {}
        
        if item.owner_id != user_id:
            return False, "Этот предмет не принадлежит вам", {}
        
        template = item.template
        if not template:
            return False, "Шаблон предмета не найден", {}
        
        if not template.is_consumable:
            return False, "Этот предмет нельзя использовать", {}
        
        result = {
            "effects": {},
            "heal": 0,
            "mana": 0,
            "buffs": []
        }
        
        # Обрабатываем в зависимости от типа
        if template.item_type == ItemType.POTION:
            if template.potion_effect:
                effects = template.potion_effect
                
                if effects.get("type") == "heal":
                    heal_amount = effects.get("value", 0)
                    max_heal = user.max_hp - user.current_hp
                    actual_heal = min(heal_amount, max_heal)
                    
                    user.current_hp += actual_heal
                    result["heal"] = actual_heal
                    result["message"] = f"Восстановлено {actual_heal} HP"
                
                elif effects.get("type") == "mana":
                    mana_amount = effects.get("value", 0)
                    max_mana = user.max_mp - user.current_mp
                    actual_mana = min(mana_amount, max_mana)
                    
                    user.current_mp += actual_mana
                    result["mana"] = actual_mana
                    result["message"] = f"Восстановлено {actual_mana} MP"
                
                elif effects.get("type") == "buff":
                    buff_type = effects.get("buff_type", "")
                    buff_value = effects.get("value", 0)
                    duration = effects.get("duration", 300)  # 5 минут по умолчанию
                    
                    # Создаем эффект
                    effect = ActiveEffect(
                        user_id=user_id,
                        effect_type=buff_type,
                        effect_power=buff_value,
                        start_time=datetime.utcnow(),
                        end_time=datetime.utcnow() + timedelta(seconds=duration),
                        source_type="potion",
                        source_id=item_id
                    )
                    db.add(effect)
                    
                    result["buffs"].append({
                        "type": buff_type,
                        "value": buff_value,
                        "duration": duration
                    })
                    result["message"] = f"Получен бафф {buff_type}: +{buff_value*100}%"
        
        # Уменьшаем количество или удаляем предмет
        if item.quantity > 1:
            item.quantity -= 1
        else:
            await db.delete(item)
        
        # Логируем действие
        audit_log = AuditLog(
            user_id=user_id,
            action="item_used",
            details={
                "item_id": str(item_id),
                "item_name": template.name,
                "item_type": template.item_type.value,
                "result": result
            }
        )
        db.add(audit_log)
        
        await db.commit()
        
        return True, result.get("message", "Предмет использован"), result
    
    async def drop_item(self, db: AsyncSession, user_id: uuid.UUID, item_id: uuid.UUID, quantity: Optional[int] = None) -> Tuple[bool, str]:
        """Выбросить предмет"""
        user = await db.get(User, user_id)
        item = await db.get(Item, item_id)
        
        if not user or not item:
            return False, "Предмет или игрок не найден"
        
        if item.owner_id != user_id:
            return False, "Этот предмет не принадлежит вам"
        
        template = item.template
        if not template:
            return False, "Шаблон предмета не найден"
        
        if not template.is_droppable:
            return False, "Этот предмет нельзя выбросить"
        
        if item.is_equipped:
            return False, "Нельзя выбросить экипированный предмет"
        
        # Определяем количество для удаления
        if quantity is None:
            quantity = item.quantity
        else:
            quantity = min(quantity, item.quantity)
        
        # Если выбрасываем не все, уменьшаем количество
        if quantity < item.quantity:
            item.quantity -= quantity
            dropped_quantity = quantity
        else:
            # Удаляем предмет полностью
            dropped_quantity = item.quantity
            await db.delete(item)
        
        # Логируем действие
        audit_log = AuditLog(
            user_id=user_id,
            action="item_dropped",
            details={
                "item_id": str(item_id),
                "item_name": template.name,
                "quantity": dropped_quantity
            }
        )
        db.add(audit_log)
        
        await db.commit()
        
        return True, f"Выброшено {dropped_quantity} {template.name}"
    
    async def sell_item(self, db: AsyncSession, user_id: uuid.UUID, item_id: uuid.UUID, quantity: Optional[int] = None) -> Tuple[bool, str, int]:
        """Продать предмет"""
        user = await db.get(User, user_id)
        item = await db.get(Item, item_id)
        
        if not user or not item:
            return False, "Предмет или игрок не найден", 0
        
        if item.owner_id != user_id:
            return False, "Этот предмет не принадлежит вам", 0
        
        template = item.template
        if not template:
            return False, "Шаблон предмета не найден", 0
        
        if not template.is_tradable:
            return False, "Этот предмет нельзя продать", 0
        
        if item.is_equipped:
            return False, "Нельзя продать экипированный предмет", 0
        
        # Определяем количество для продажи
        if quantity is None:
            quantity = item.quantity
        else:
            quantity = min(quantity, item.quantity)
        
        # Рассчитываем стоимость
        sell_price = template.sell_price * quantity
        
        # Если продаем не все, уменьшаем количество
        if quantity < item.quantity:
            item.quantity -= quantity
            sold_quantity = quantity
        else:
            # Удаляем предмет полностью
            sold_quantity = item.quantity
            await db.delete(item)
        
        # Добавляем золото игроку
        user.gold += sell_price
        user.total_gold_earned += sell_price
        
        # Логируем действие
        audit_log = AuditLog(
            user_id=user_id,
            action="item_sold",
            details={
                "item_id": str(item_id),
                "item_name": template.name,
                "quantity": sold_quantity,
                "price": sell_price,
                "new_balance": user.gold
            }
        )
        db.add(audit_log)
        
        await db.commit()
        
        return True, f"Продано {sold_quantity} {template.name} за {sell_price} золота", sell_price
    
    async def sort_inventory(self, db: AsyncSession, user_id: uuid.UUID, sort_by: SortType) -> bool:
        """Отсортировать инвентарь"""
        # В реальной реализации здесь была бы логика физической сортировки
        # В нашем случае просто сохраняем предпочтение сортировки
        
        sort_key = f"inventory_sort:{user_id}"
        await self.redis.setex(
            sort_key,
            86400,  # 24 часа
            sort_by.value
        )
        
        # Логируем действие
        audit_log = AuditLog(
            user_id=user_id,
            action="inventory_sorted",
            details={
                "sort_by": sort_by.value
            }
        )
        db.add(audit_log)
        
        await db.commit()
        
        return True
    
    # ============ КРАФТ ============
    
    async def get_available_recipes(self, db: AsyncSession, user_id: uuid.UUID, 
                                   profession: Optional[ProfessionType] = None) -> List[Recipe]:
        """Получить доступные рецепты для игрока"""
        user = await db.get(User, user_id)
        if not user:
            return []
        
        # Базовый запрос
        query = select(Recipe).where(Recipe.is_discovered == True)
        
        if profession:
            query = query.where(Recipe.profession_type == profession)
        
        # Фильтруем по уровню профессии
        profession_levels = {
            ProfessionType.MINING: user.mining_level,
            ProfessionType.WOODCUTTING: user.woodcutting_level,
            ProfessionType.HERBALISM: user.herbalism_level,
            ProfessionType.BLACKSMITHING: user.blacksmithing_level,
            ProfessionType.ALCHEMY: user.alchemy_level,
            ProfessionType.ENCHANTING: 1,  # TODO: добавить уровень
            ProfessionType.TAILORING: 1,   # TODO: добавить уровень
            ProfessionType.JEWELRY: 1,     # TODO: добавить уровень
            ProfessionType.COOKING: 1      # TODO: добавить уровень
        }
        
        # Добавляем фильтр по уровню профессии
        if profession and profession in profession_levels:
            user_level = profession_levels[profession]
            query = query.where(Recipe.profession_level <= user_level)
        
        # Выполняем запрос
        result = await db.execute(
            query.options(
                selectinload(Recipe.result_item),
                selectinload(Recipe.ingredients).selectinload(RecipeIngredient.item_template)
            ).order_by(Recipe.profession_level)
        )
        
        return result.scalars().all()
    
    async def get_recipe_details(self, db: AsyncSession, recipe_id: uuid.UUID) -> Dict[str, Any]:
        """Получить детали рецепта"""
        recipe = await db.get(Recipe, recipe_id)
        if not recipe:
            return {}
        
        # Загружаем связанные данные
        await db.refresh(recipe, ['result_item', 'ingredients'])
        
        # Получаем ингредиенты с деталями
        ingredients_details = []
        for ingredient in recipe.ingredients:
            template = ingredient.item_template
            if template:
                ingredients_details.append({
                    "item_template_id": str(template.id),
                    "name": template.name,
                    "icon": template.icon,
                    "quantity": ingredient.quantity,
                    "rarity": template.rarity.value,
                    "description": template.description
                })
        
        # Получаем результат
        result_item = recipe.result_item
        result_details = None
        if result_item:
            result_details = {
                "item_template_id": str(result_item.id),
                "name": result_item.name,
                "icon": result_item.icon,
                "quantity": recipe.result_quantity,
                "rarity": result_item.rarity.value,
                "description": result_item.description
            }
        
        return {
            "recipe": recipe,
            "ingredients": ingredients_details,
            "result": result_details,
            "requirements": {
                "profession_type": recipe.profession_type.value,
                "profession_level": recipe.profession_level,
                "craft_time": recipe.craft_time,
                "gold_cost": recipe.gold_cost,
                "discovered": recipe.is_discovered
            }
        }
    
    async def can_craft_recipe(self, db: AsyncSession, user_id: uuid.UUID, recipe_id: uuid.UUID) -> Tuple[bool, List[str]]:
        """Проверить возможность крафта рецепта"""
        user = await db.get(User, user_id)
        recipe = await db.get(Recipe, recipe_id)
        
        if not user or not recipe:
            return False, ["Рецепт или игрок не найден"]
        
        errors = []
        
        # Проверяем уровень профессии
        profession_level = self._get_user_profession_level(user, recipe.profession_type)
        if profession_level < recipe.profession_level:
            errors.append(f"Требуется {recipe.profession_type.value} {recipe.profession_level}")
        
        # Проверяем наличие ингредиентов
        for ingredient in recipe.ingredients:
            # Проверяем наличие предмета в инвентаре
            result = await db.execute(
                select(Item).where(
                    and_(
                        Item.owner_id == user_id,
                        Item.template_id == ingredient.item_template_id
                    )
                )
            )
            items = result.scalars().all()
            
            total_quantity = sum(item.quantity for item in items)
            if total_quantity < ingredient.quantity:
                item_template = await db.get(ItemTemplate, ingredient.item_template_id)
                item_name = item_template.name if item_template else "Неизвестный предмет"
                errors.append(f"Не хватает {item_name}: {total_quantity}/{ingredient.quantity}")
        
        # Проверяем достаточно ли золота
        if user.gold < recipe.gold_cost:
            errors.append(f"Недостаточно золота: {user.gold}/{recipe.gold_cost}")
        
        # Проверяем есть ли активный крафт
        active_craft = await self.get_active_craft(db, user_id)
        if active_craft:
            errors.append("У вас уже есть активный крафт")
        
        return len(errors) == 0, errors
    
    def _get_user_profession_level(self, user: User, profession: ProfessionType) -> int:
        """Получить уровень профессии игрока"""
        if profession == ProfessionType.MINING:
            return user.mining_level
        elif profession == ProfessionType.WOODCUTTING:
            return user.woodcutting_level
        elif profession == ProfessionType.HERBALISM:
            return user.herbalism_level
        elif profession == ProfessionType.BLACKSMITHING:
            return user.blacksmithing_level
        elif profession == ProfessionType.ALCHEMY:
            return user.alchemy_level
        else:
            return 1  # Базовая профессия
    
    async def start_crafting(self, db: AsyncSession, user_id: uuid.UUID, recipe_id: uuid.UUID) -> Tuple[bool, str, Optional[ActiveAction]]:
        """Начать крафт предмета"""
        user = await db.get(User, user_id)
        recipe = await db.get(Recipe, recipe_id)
        
        if not user or not recipe:
            return False, "Рецепт или игрок не найден", None
        
        # Проверяем возможность крафта
        can_craft, errors = await self.can_craft_recipe(db, user_id, recipe_id)
        if not can_craft:
            return False, "; ".join(errors), None
        
        # Списываем ингредиенты
        for ingredient in recipe.ingredients:
            await self._consume_ingredient(db, user_id, ingredient)
        
        # Списываем золото
        user.gold -= recipe.gold_cost
        
        # Создаем активное действие крафта
        start_time = datetime.utcnow()
        end_time = start_time + timedelta(seconds=recipe.craft_time)
        
        craft_action = ActiveAction(
            user_id=user_id,
            action_type=ActionType.CRAFTING,
            target_id=recipe_id,
            start_time=start_time,
            end_time=end_time,
            progress=0.0,
            data={
                "recipe_id": str(recipe_id),
                "recipe_name": recipe.name,
                "craft_time": recipe.craft_time,
                "gold_cost": recipe.gold_cost
            }
        )
        db.add(craft_action)
        
        # Создаем снапшот для восстановления
        snapshot = StateSnapshot(
            snapshot_type="crafting",
            user_id=user_id,
            entity_id=craft_action.id,
            entity_type="active_action",
            snapshot_data={
                "recipe_id": str(recipe_id),
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "progress": 0.0,
                "craft_data": craft_action.data
            },
            expires_at=end_time + timedelta(hours=1)
        )
        db.add(snapshot)
        
        # Логируем действие
        audit_log = AuditLog(
            user_id=user_id,
            action="crafting_started",
            details={
                "recipe_id": str(recipe_id),
                "recipe_name": recipe.name,
                "craft_time": recipe.craft_time,
                "gold_cost": recipe.gold_cost,
                "end_time": end_time.isoformat()
            }
        )
        db.add(audit_log)
        
        await db.commit()
        
        # Сохраняем в Redis
        craft_key = f"crafting:{user_id}"
        craft_data = {
            "action_id": str(craft_action.id),
            "user_id": str(user_id),
            "recipe_id": str(recipe_id),
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "progress": 0.0,
            "data": craft_action.data
        }
        
        await self.redis.setex(
            craft_key,
            recipe.craft_time,
            json.dumps(craft_data)
        )
        self.active_crafts[str(user_id)] = craft_data
        
        # Запускаем таймер
        asyncio.create_task(self._monitor_crafting(craft_action.id, recipe.craft_time))
        
        return True, f"Крафт начат. Завершится в {end_time.strftime('%H:%M:%S')}", craft_action
    
    async def _consume_ingredient(self, db: AsyncSession, user_id: uuid.UUID, ingredient: RecipeIngredient):
        """Потребить ингредиент"""
        # Ищем предметы в инвентаре
        result = await db.execute(
            select(Item).where(
                and_(
                    Item.owner_id == user_id,
                    Item.template_id == ingredient.item_template_id
                )
            ).order_by(Item.quantity.desc())
        )
        items = result.scalars().all()
        
        remaining = ingredient.quantity
        
        for item in items:
            if remaining <= 0:
                break
            
            if item.quantity > remaining:
                item.quantity -= remaining
                remaining = 0
            else:
                remaining -= item.quantity
                await db.delete(item)
    
    async def _monitor_crafting(self, action_id: uuid.UUID, craft_time: int):
        """Мониторинг крафта"""
        await asyncio.sleep(craft_time)
        
        async with self.db_session_factory() as db:
            action = await db.get(ActiveAction, action_id)
            if action and not action.is_completed:
                await self.complete_crafting(db, action)
    
    async def complete_crafting(self, db: AsyncSession, craft_action: ActiveAction) -> CraftingResult:
        """Завершить крафт"""
        craft_action.is_completed = True
        craft_action.progress = 1.0
        
        user = await db.get(User, craft_action.user_id)
        recipe = await db.get(Recipe, craft_action.target_id)
        
        if not user or not recipe:
            return CraftingResult(success=False, message="Ошибка данных")
        
        # Рассчитываем шанс успеха
        success_chance = await self._calculate_craft_chance(db, user, recipe)
        success = random.random() < success_chance
        
        if success:
            # Создаем предмет
            item = Item(
                template_id=recipe.result_item_id,
                owner_id=user.id,
                quantity=recipe.result_quantity
            )
            db.add(item)
            
            # Добавляем опыт в профессию
            experience_gained = await self._calculate_craft_experience(db, user, recipe)
            await self._add_profession_experience(db, user, recipe.profession_type, experience_gained)
            
            result = CraftingResult(
                success=True,
                item=item,
                quantity=recipe.result_quantity,
                experience=experience_gained,
                message=f"Крафт успешен! Получено {recipe.result_quantity} {recipe.result_item.name}"
            )
        else:
            # Крафт провалился
            result = CraftingResult(
                success=False,
                message="Крафт провалился! Ингредиенты потеряны."
            )
        
        # Обновляем статистику
        await self._update_crafting_stats(db, user.id, success)
        
        # Логируем результат
        audit_log = AuditLog(
            user_id=user.id,
            action="crafting_completed",
            details={
                "recipe_id": str(recipe.id),
                "recipe_name": recipe.name,
                "success": success,
                "experience": result.experience if success else 0,
                "result_item": recipe.result_item.name if success else None
            }
        )
        db.add(audit_log)
        
        await db.commit()
        
        # Удаляем из Redis
        await self.redis.delete(f"crafting:{craft_action.user_id}")
        if str(craft_action.user_id) in self.active_crafts:
            del self.active_crafts[str(craft_action.user_id)]
        
        return result
    
    async def _calculate_craft_chance(self, db: AsyncSession, user: User, recipe: Recipe) -> float:
        """Рассчитать шанс успеха крафта"""
        base_chance = 0.8  # 80% базовый шанс
        
        # Бонус от уровня профессии
        profession_level = self._get_user_profession_level(user, recipe.profession_type)
        level_bonus = min(0.2, (profession_level - recipe.profession_level) * 0.02)
        
        # Бонус от интеллекта
        intelligence_bonus = user.intelligence * 0.001
        
        # Общий шанс
        total_chance = base_chance + level_bonus + intelligence_bonus
        
        return min(max(total_chance, 0.1), 0.95)  # Ограничиваем 10-95%
    
    async def _calculate_craft_experience(self, db: AsyncSession, user: User, recipe: Recipe) -> int:
        """Рассчитать опыт за крафт"""
        base_exp = recipe.profession_level * 10
        
        # Модификатор за сложность
        profession_level = self._get_user_profession_level(user, recipe.profession_type)
        difficulty_modifier = max(0.5, 2.0 - (profession_level / recipe.profession_level))
        
        return int(base_exp * difficulty_modifier)
    
    async def _add_profession_experience(self, db: AsyncSession, user: User, profession: ProfessionType, experience: int):
        """Добавить опыт в профессию"""
        if profession == ProfessionType.MINING:
            user.mining_exp += experience
            # Проверяем повышение уровня
            await self._check_profession_level_up(db, user, "mining")
        elif profession == ProfessionType.WOODCUTTING:
            user.woodcutting_exp += experience
            await self._check_profession_level_up(db, user, "woodcutting")
        elif profession == ProfessionType.HERBALISM:
            user.herbalism_exp += experience
            await self._check_profession_level_up(db, user, "herbalism")
        elif profession == ProfessionType.BLACKSMITHING:
            user.blacksmithing_exp += experience
            await self._check_profession_level_up(db, user, "blacksmithing")
        elif profession == ProfessionType.ALCHEMY:
            user.alchemy_exp += experience
            await self._check_profession_level_up(db, user, "alchemy")
    
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
        elif profession == "blacksmithing":
            current_level = user.blacksmithing_level
            current_exp = user.blacksmithing_exp
        elif profession == "alchemy":
            current_level = user.alchemy_level
            current_exp = user.alchemy_exp
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
            elif profession == "blacksmithing":
                user.blacksmithing_level += 1
                user.blacksmithing_exp -= exp_needed
            elif profession == "alchemy":
                user.alchemy_level += 1
                user.alchemy_exp -= exp_needed
            
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
    
    async def _update_crafting_stats(self, db: AsyncSession, user_id: uuid.UUID, success: bool):
        """Обновить статистику крафта"""
        # Здесь можно обновлять общую статистику крафтов
        pass
    
    async def get_active_craft(self, db: AsyncSession, user_id: uuid.UUID) -> Optional[ActiveAction]:
        """Получить активный крафт игрока"""
        result = await db.execute(
            select(ActiveAction).where(
                and_(
                    ActiveAction.user_id == user_id,
                    ActiveAction.action_type == ActionType.CRAFTING,
                    ActiveAction.is_completed == False
                )
            )
        )
        return result.scalar_one_or_none()
    
    async def cancel_crafting(self, db: AsyncSession, user_id: uuid.UUID) -> Tuple[bool, str]:
        """Отменить крафт"""
        active_craft = await self.get_active_craft(db, user_id)
        if not active_craft:
            return False, "Активный крафт не найден"
        
        # Помечаем как отмененный
        active_craft.is_completed = True
        active_craft.progress = 0.0
        active_craft.data = (active_craft.data or {}) | {"cancelled": True, "cancelled_at": datetime.utcnow().isoformat()}
        
        # Возвращаем часть ингредиентов (50%)
        recipe = await db.get(Recipe, active_craft.target_id)
        if recipe:
            # Здесь можно реализовать возврат части ингредиентов
            pass
        
        # Логируем
        audit_log = AuditLog(
            user_id=user_id,
            action="crafting_cancelled",
            details={
                "recipe_id": str(active_craft.target_id),
                "progress": active_craft.progress
            }
        )
        db.add(audit_log)
        
        await db.commit()
        
        # Удаляем из Redis
        await self.redis.delete(f"crafting:{user_id}")
        if str(user_id) in self.active_crafts:
            del self.active_crafts[str(user_id)]
        
        return True, "Крафт отменен. Часть ингредиентов возвращена."
    
    # ============ ТОРГОВЛЯ И АУКЦИОН ============
    
    async def create_auction_item(self, db: AsyncSession, user_id: uuid.UUID, item_id: uuid.UUID, 
                                 start_price: int, buyout_price: Optional[int] = None, 
                                 duration_hours: int = 24) -> Tuple[bool, str, Optional[uuid.UUID]]:
        """Создать предмет на аукционе"""
        user = await db.get(User, user_id)
        item = await db.get(Item, item_id)
        
        if not user or not item:
            return False, "Предмет или игрок не найден", None
        
        if item.owner_id != user_id:
            return False, "Этот предмет не принадлежит вам", None
        
        if item.is_equipped:
            return False, "Нельзя продать экипированный предмет", None
        
        template = item.template
        if not template:
            return False, "Шаблон предмета не найден", None
        
        if not template.is_tradable:
            return False, "Этот предмет нельзя продать", None
        
        # Проверяем цену
        min_price = template.sell_price
        if start_price < min_price:
            return False, f"Минимальная цена: {min_price} золота", None
        
        if buyout_price and buyout_price < start_price:
            return False, "Цена выкупа должна быть больше стартовой", None
        
        # Создаем аукцион
        auction_id = uuid.uuid4()
        end_time = datetime.utcnow() + timedelta(hours=duration_hours)
        
        auction_item = AuctionItem(
            id=auction_id,
            seller_id=user_id,
            item=item,
            start_price=start_price,
            current_bid=start_price,
            buyout_price=buyout_price,
            end_time=end_time
        )
        
        # Сохраняем в Redis
        auction_key = f"auction:{auction_id}"
        auction_data = {
            "id": str(auction_id),
            "seller_id": str(user_id),
            "item_id": str(item_id),
            "item_data": {
                "name": template.name,
                "icon": template.icon,
                "rarity": template.rarity.value,
                "level": template.level_requirement
            },
            "start_price": start_price,
            "current_bid": start_price,
            "buyout_price": buyout_price,
            "bids_count": 0,
            "end_time": end_time.isoformat(),
            "highest_bidder": None,
            "created_at": datetime.utcnow().isoformat()
        }
        
        await self.redis.setex(
            auction_key,
            duration_hours * 3600,
            json.dumps(auction_data)
        )
        
        # Сохраняем в памяти
        self.auction_items[str(auction_id)] = auction_item
        
        # Убираем предмет из инвентаря
        item.owner_id = None  # Временно без владельца
        
        # Логируем
        audit_log = AuditLog(
            user_id=user_id,
            action="auction_created",
            details={
                "auction_id": str(auction_id),
                "item_id": str(item_id),
                "item_name": template.name,
                "start_price": start_price,
                "buyout_price": buyout_price,
                "end_time": end_time.isoformat()
            }
        )
        db.add(audit_log)
        
        await db.commit()
        
        return True, f"Предмет выставлен на аукцион за {start_price} золота", auction_id
    
    async def place_bid(self, db: AsyncSession, user_id: uuid.UUID, auction_id: uuid.UUID, bid_amount: int) -> Tuple[bool, str]:
        """Сделать ставку на аукционе"""
        user = await db.get(User, user_id)
        
        if not user:
            return False, "Игрок не найден"
        
        if user.gold < bid_amount:
            return False, f"Недостаточно золота: {user.gold}/{bid_amount}"
        
        # Получаем данные аукциона
        auction_key = f"auction:{auction_id}"
        auction_data_json = await self.redis.get(auction_key)
        
        if not auction_data_json:
            return False, "Аукцион не найден или завершен"
        
        auction_data = json.loads(auction_data_json)
        
        # Проверяем не истек ли аукцион
        end_time = datetime.fromisoformat(auction_data["end_time"])
        if datetime.utcnow() > end_time:
            return False, "Аукцион завершен"
        
        # Проверяем ставку
        current_bid = auction_data["current_bid"]
        min_bid = current_bid * 1.1  # Минимальная ставка на 10% выше
        
        if bid_amount < min_bid:
            return False, f"Минимальная ставка: {int(min_bid)} золота"
        
        # Проверяем цену выкупа
        if auction_data["buyout_price"] and bid_amount >= auction_data["buyout_price"]:
            # Выкуп предмета
            return await self._buyout_auction(db, user_id, auction_id, auction_data)
        
        # Возвращаем предыдущую ставку предыдущему участнику
        previous_bidder = auction_data.get("highest_bidder")
        if previous_bidder:
            previous_user = await db.get(User, uuid.UUID(previous_bidder))
            if previous_user:
                previous_user.gold += auction_data["current_bid"]
        
        # Списываем золото у нового участника
        user.gold -= bid_amount
        
        # Обновляем аукцион
        auction_data["current_bid"] = bid_amount
        auction_data["bids_count"] += 1
        auction_data["highest_bidder"] = str(user_id)
        
        # Продлеваем аукцион если нужно (правило snipe protection)
        time_left = (end_time - datetime.utcnow()).seconds
        if time_left < 300:  # Меньше 5 минут
            new_end_time = datetime.utcnow() + timedelta(minutes=5)
            auction_data["end_time"] = new_end_time.isoformat()
            await self.redis.expire(auction_key, 300)  # Продлеваем на 5 минут
        
        await self.redis.set(auction_key, json.dumps(auction_data))
        
        # Логируем
        audit_log = AuditLog(
            user_id=user_id,
            action="auction_bid",
            details={
                "auction_id": str(auction_id),
                "bid_amount": bid_amount,
                "new_current_bid": bid_amount,
                "previous_bidder": previous_bidder
            }
        )
        db.add(audit_log)
        
        await db.commit()
        
        return True, f"Ставка принята: {bid_amount} золота"
    
    async def _buyout_auction(self, db: AsyncSession, user_id: uuid.UUID, auction_id: uuid.UUID, auction_data: Dict[str, Any]) -> Tuple[bool, str]:
        """Выкупить предмет на аукционе"""
        user = await db.get(User, user_id)
        buyout_price = auction_data["buyout_price"]
        
        if user.gold < buyout_price:
            return False, f"Недостаточно золота для выкупа: {user.gold}/{buyout_price}"
        
        # Списываем золото
        user.gold -= buyout_price
        
        # Получаем предмет
        item = await db.get(Item, uuid.UUID(auction_data["item_id"]))
        if not item:
            return False, "Предмет не найден"
        
        # Передаем предмет покупателю
        item.owner_id = user_id
        
        # Возвращаем предыдущую ставку если была
        previous_bidder = auction_data.get("highest_bidder")
        if previous_bidder:
            previous_user = await db.get(User, uuid.UUID(previous_bidder))
            if previous_user:
                previous_user.gold += auction_data["current_bid"]
        
        # Переводим золото продавцу
        seller = await db.get(User, uuid.UUID(auction_data["seller_id"]))
        if seller:
            commission = buyout_price * 0.05  # 5% комиссия
            seller_gold = buyout_price - commission
            seller.gold += seller_gold
        
        # Завершаем аукцион
        await self.redis.delete(f"auction:{auction_id}")
        if str(auction_id) in self.auction_items:
            del self.auction_items[str(auction_id)]
        
        # Логируем
        audit_log = AuditLog(
            user_id=user_id,
            action="auction_buyout",
            details={
                "auction_id": str(auction_id),
                "buyout_price": buyout_price,
                "seller_id": auction_data["seller_id"],
                "item_id": auction_data["item_id"]
            }
        )
        db.add(audit_log)
        
        await db.commit()
        
        return True, f"Предмет выкуплен за {buyout_price} золота"
    
    async def get_auction_items(self, page: int = 1, page_size: int = 20, 
                               filters: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], int]:
        """Получить предметы с аукциона"""
        # Получаем все ключи аукционов
        auction_keys = await self.redis.keys("auction:*")
        
        if not auction_keys:
            return [], 0
        
        # Применяем пагинацию
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        
        paginated_keys = auction_keys[start_idx:end_idx]
        
        # Получаем данные
        auction_items = []
        for key in paginated_keys:
            data_json = await self.redis.get(key)
            if data_json:
                auction_data = json.loads(data_json)
                auction_items.append(auction_data)
        
        # Применяем фильтры если есть
        if filters:
            filtered_items = []
            for item in auction_items:
                include = True
                
                if "min_price" in filters and item["current_bid"] < filters["min_price"]:
                    include = False
                if "max_price" in filters and item["current_bid"] > filters["max_price"]:
                    include = False
                if "rarity" in filters and item["item_data"]["rarity"] != filters["rarity"]:
                    include = False
                if "search" in filters and filters["search"].lower() not in item["item_data"]["name"].lower():
                    include = False
                
                if include:
                    filtered_items.append(item)
            
            auction_items = filtered_items
        
        total = len(auction_keys)
        
        return auction_items, total
    
    async def cancel_auction(self, db: AsyncSession, user_id: uuid.UUID, auction_id: uuid.UUID) -> Tuple[bool, str]:
        """Отменить аукцион"""
        # Получаем данные аукциона
        auction_key = f"auction:{auction_id}"
        auction_data_json = await self.redis.get(auction_key)
        
        if not auction_data_json:
            return False, "Аукцион не найден"
        
        auction_data = json.loads(auction_data_json)
        
        # Проверяем права
        if auction_data["seller_id"] != str(user_id):
            return False, "Вы не являетесь продавцом"
        
        # Проверяем были ли ставки
        if auction_data.get("highest_bidder"):
            return False, "Нельзя отменить аукцион со ставками"
        
        # Возвращаем предмет
        item = await db.get(Item, uuid.UUID(auction_data["item_id"]))
        if item:
            item.owner_id = user_id
        
        # Удаляем аукцион
        await self.redis.delete(auction_key)
        if str(auction_id) in self.auction_items:
            del self.auction_items[str(auction_id)]
        
        # Логируем
        audit_log = AuditLog(
            user_id=user_id,
            action="auction_cancelled",
            details={
                "auction_id": str(auction_id),
                "item_id": auction_data["item_id"]
            }
        )
        db.add(audit_log)
        
        await db.commit()
        
        return True, "Аукцион отменен"
    
    # ============ РЕМОНТ ПРЕДМЕТОВ ============
    
    async def repair_item(self, db: AsyncSession, user_id: uuid.UUID, item_id: uuid.UUID) -> Tuple[bool, str, int]:
        """Починить предмет"""
        user = await db.get(User, user_id)
        item = await db.get(Item, item_id)
        
        if not user or not item:
            return False, "Предмет или игрок не найден", 0
        
        if item.owner_id != user_id:
            return False, "Этот предмет не принадлежит вам", 0
        
        template = item.template
        if not template:
            return False, "Шаблон предмета не найден", 0
        
        if not item.current_durability or not item.max_durability:
            return False, "Этот предмет не требует ремонта", 0
        
        if item.current_durability >= item.max_durability:
            return False, "Предмет не поврежден", 0
        
        # Рассчитываем стоимость ремонта
        damage_percentage = 1 - (item.current_durability / item.max_durability)
        repair_cost = int(template.base_price * damage_percentage * 0.3)  # 30% от стоимости урона
        
        if user.gold < repair_cost:
            return False, f"Недостаточно золота: {user.gold}/{repair_cost}", repair_cost
        
        # Списываем золото
        user.gold -= repair_cost
        
        # Восстанавливаем прочность
        item.current_durability = item.max_durability
        
        # Логируем
        audit_log = AuditLog(
            user_id=user_id,
            action="item_repaired",
            details={
                "item_id": str(item_id),
                "item_name": template.name,
                "repair_cost": repair_cost,
                "durability_restored": item.max_durability - item.current_durability
            }
        )
        db.add(audit_log)
        
        await db.commit()
        
        return True, f"Предмет отремонтирован за {repair_cost} золота", repair_cost
    
    async def get_repairable_items(self, db: AsyncSession, user_id: uuid.UUID) -> List[Item]:
        """Получить предметы требующие ремонта"""
        result = await db.execute(
            select(Item).where(
                and_(
                    Item.owner_id == user_id,
                    Item.current_durability.isnot(None),
                    Item.max_durability.isnot(None),
                    Item.current_durability < Item.max_durability
                )
            ).options(selectinload(Item.template))
        )
        return result.scalars().all()
    
    # ============ ХРАНИЛИЩЕ ============
    
    async def get_storage_capacity(self, db: AsyncSession, user_id: uuid.UUID) -> Dict[str, int]:
        """Получить информацию о хранилище"""
        # В реальной реализации здесь была бы отдельная таблица хранилища
        # В упрощенной версии используем Redis
        storage_key = f"storage:{user_id}:capacity"
        capacity_data = await self.redis.get(storage_key)
        
        if capacity_data:
            return json.loads(capacity_data)
        else:
            # Стандартные значения
            default_capacity = {
                "max_slots": 100,
                "used_slots": 0,
                "free_slots": 100,
                "upgrade_level": 1,
                "next_upgrade_cost": 1000
            }
            await self.redis.set(storage_key, json.dumps(default_capacity))
            return default_capacity
    
    async def deposit_to_storage(self, db: AsyncSession, user_id: uuid.UUID, item_id: uuid.UUID, quantity: int) -> Tuple[bool, str]:
        """Положить предмет в хранилище"""
        # Проверяем есть ли место в хранилище
        storage_capacity = await self.get_storage_capacity(db, user_id)
        if storage_capacity["used_slots"] >= storage_capacity["max_slots"]:
            return False, "Хранилище переполнено"
        
        item = await db.get(Item, item_id)
        if not item or item.owner_id != user_id:
            return False, "Предмет не найден или не принадлежит вам"
        
        if quantity > item.quantity:
            return False, f"Недостаточно предметов: {item.quantity}/{quantity}"
        
        # Сохраняем в Redis хранилище
        storage_key = f"storage:{user_id}:items"
        storage_items = await self.redis.get(storage_key)
        
        if storage_items:
            items_list = json.loads(storage_items)
        else:
            items_list = []
        
        # Добавляем предмет
        items_list.append({
            "item_id": str(item_id),
            "template_id": str(item.template_id),
            "quantity": quantity,
            "deposited_at": datetime.utcnow().isoformat()
        })
        
        await self.redis.set(storage_key, json.dumps(items_list))
        
        # Обновляем количество в инвентаре
        if quantity == item.quantity:
            await db.delete(item)
        else:
            item.quantity -= quantity
        
        # Обновляем статистику хранилища
        storage_capacity["used_slots"] += 1
        await self.redis.set(f"storage:{user_id}:capacity", json.dumps(storage_capacity))
        
        # Логируем
        audit_log = AuditLog(
            user_id=user_id,
            action="storage_deposit",
            details={
                "item_id": str(item_id),
                "quantity": quantity,
                "storage_slots_used": storage_capacity["used_slots"]
            }
        )
        db.add(audit_log)
        
        await db.commit()
        
        return True, f"Предмет помещен в хранилище. Использовано слотов: {storage_capacity['used_slots']}/{storage_capacity['max_slots']}"
    
    async def withdraw_from_storage(self, db: AsyncSession, user_id: uuid.UUID, storage_index: int) -> Tuple[bool, str]:
        """Забрать предмет из хранилища"""
        # Получаем предмет из хранилища
        storage_key = f"storage:{user_id}:items"
        storage_items = await self.redis.get(storage_key)
        
        if not storage_items:
            return False, "Хранилище пусто"
        
        items_list = json.loads(storage_items)
        
        if storage_index >= len(items_list):
            return False, "Предмет не найден в хранилище"
        
        storage_item = items_list.pop(storage_index)
        
        # Проверяем есть ли место в инвентаре
        inventory_data = await self.get_inventory(db, user_id)
        if inventory_data["stats"]["used_slots"] >= inventory_data["stats"]["capacity"]:
            return False, "Инвентарь переполнен"
        
        # Создаем предмет в инвентаре
        item = Item(
            template_id=uuid.UUID(storage_item["template_id"]),
            owner_id=user_id,
            quantity=storage_item["quantity"]
        )
        db.add(item)
        
        # Сохраняем обновленный список хранилища
        if items_list:
            await self.redis.set(storage_key, json.dumps(items_list))
        else:
            await self.redis.delete(storage_key)
        
        # Обновляем статистику хранилища
        storage_capacity = await self.get_storage_capacity(db, user_id)
        storage_capacity["used_slots"] = max(0, storage_capacity["used_slots"] - 1)
        await self.redis.set(f"storage:{user_id}:capacity", json.dumps(storage_capacity))
        
        # Логируем
        audit_log = AuditLog(
            user_id=user_id,
            action="storage_withdraw",
            details={
                "template_id": storage_item["template_id"],
                "quantity": storage_item["quantity"],
                "storage_slots_used": storage_capacity["used_slots"]
            }
        )
        db.add(audit_log)
        
        await db.commit()
        
        return True, "Предмет извлечен из хранилища"
    
    async def get_storage_items(self, db: AsyncSession, user_id: uuid.UUID) -> List[Dict[str, Any]]:
        """Получить предметы из хранилища"""
        storage_key = f"storage:{user_id}:items"
        storage_items = await self.redis.get(storage_key)
        
        if not storage_items:
            return []
        
        items_list = json.loads(storage_items)
        
        # Получаем детали предметов
        detailed_items = []
        for i, item_data in enumerate(items_list):
            template = await db.get(ItemTemplate, uuid.UUID(item_data["template_id"]))
            if template:
                detailed_items.append({
                    "index": i,
                    "template": template,
                    "quantity": item_data["quantity"],
                    "deposited_at": item_data["deposited_at"]
                })
        
        return detailed_items
    
    async def upgrade_storage(self, db: AsyncSession, user_id: uuid.UUID) -> Tuple[bool, str]:
        """Улучшить хранилище"""
        user = await db.get(User, user_id)
        storage_capacity = await self.get_storage_capacity(db, user_id)
        
        upgrade_cost = storage_capacity["next_upgrade_cost"]
        
        if user.gold < upgrade_cost:
            return False, f"Недостаточно золота: {user.gold}/{upgrade_cost}"
        
        # Списываем золото
        user.gold -= upgrade_cost
        
        # Улучшаем хранилище
        storage_capacity["max_slots"] += 50
        storage_capacity["free_slots"] = storage_capacity["max_slots"] - storage_capacity["used_slots"]
        storage_capacity["upgrade_level"] += 1
        storage_capacity["next_upgrade_cost"] = int(upgrade_cost * 1.5)  # Увеличиваем стоимость следующего улучшения
        
        await self.redis.set(f"storage:{user_id}:capacity", json.dumps(storage_capacity))
        
        # Логируем
        audit_log = AuditLog(
            user_id=user_id,
            action="storage_upgraded",
            details={
                "new_capacity": storage_capacity["max_slots"],
                "upgrade_cost": upgrade_cost,
                "upgrade_level": storage_capacity["upgrade_level"]
            }
        )
        db.add(audit_log)
        
        await db.commit()
        
        return True, f"Хранилище улучшено до {storage_capacity['max_slots']} слотов"

# ============ ХЭНДЛЕРЫ ДЛЯ ИГРОКОВ ============

@inventory_router.callback_query(F.data == "inventory")
async def handle_inventory_menu(callback: CallbackQuery, state: FSMContext):
    """Обработчик меню инвентаря"""
    from database import get_db_session
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        inventory_manager = InventoryManager(None, get_db_session)
        inventory_data = await inventory_manager.get_inventory(db, user.id)
        
        stats = inventory_data["stats"]
        equipped_items = inventory_data.get("equipped_items", {})
        
        text = html.bold("🎒 ИНВЕНТАРЬ\n\n")
        
        text += html.bold("📊 СТАТИСТИКА:\n")
        text += f"📦 Слотов: {stats['used_slots']}/{stats['capacity']}\n"
        text += f"⚖️ Вес: {stats['total_weight']:.1f} кг\n"
        text += f"💰 Стоимость: {format_number(stats['total_value'])} золота\n\n"
        
        text += html.bold("🛡️ ЭКИПИРОВКА:\n")
        if equipped_items:
            for slot_name, item in equipped_items.items():
                if item and item.template:
                    slot = inventory_manager.item_slots.get(slot_name)
                    if slot:
                        text += f"{slot.icon} {slot.name}: {item.template.icon} {item.template.name}\n"
        else:
            text += "Нет экипированных предметов\n"
        
        text += "\n"
        text += html.bold("⚔️ ХАРАКТЕРИСТИКИ С ЭКИПИРОВКОЙ:\n")
        text += f"❤️ HP: {user.current_hp}/{user.max_hp}\n"
        text += f"🔷 MP: {user.current_mp}/{user.max_mp}\n"
        text += f"💪 Сила: {user.strength}\n"
        text += f"🏃 Ловкость: {user.agility}\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📦 Просмотреть инвентарь", callback_data="inventory_view")],
            [InlineKeyboardButton(text="🛡️ Экипировка", callback_data="inventory_equipment")],
            [InlineKeyboardButton(text="🔨 Крафт", callback_data="inventory_crafting")],
            [InlineKeyboardButton(text="💰 Торговля", callback_data="inventory_trading")],
            [InlineKeyboardButton(text="🏦 Аукцион", callback_data="inventory_auction")],
            [InlineKeyboardButton(text="📦 Хранилище", callback_data="inventory_storage")],
            [InlineKeyboardButton(text="🔧 Ремонт", callback_data="inventory_repair")],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(InventoryStates.main_menu)

@inventory_router.callback_query(F.data == "inventory_view")
async def handle_inventory_view(callback: CallbackQuery, state: FSMContext):
    """Просмотр инвентаря"""
    from database import get_db_session
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        inventory_manager = InventoryManager(None, get_db_session)
        items, total = await inventory_manager.get_inventory_items(
            db, user.id, page=1, page_size=10
        )
        
        text = html.bold("📦 ИНВЕНТАРЬ\n\n")
        
        if items:
            text += html.bold(f"Предметы (1/{max(1, (total + 9) // 10)}):\n\n")
            
            for i, item in enumerate(items, 1):
                template = item.template
                if template:
                    text += f"{i}. {template.icon} {template.name}"
                    if item.quantity > 1:
                        text += f" ×{item.quantity}"
                    text += "\n"
                    
                    if template.item_type == ItemType.WEAPON:
                        text += f"   Урон: {template.damage_min}-{template.damage_max}"
                    elif template.item_type == ItemType.ARMOR:
                        text += f"   Защита: {template.defense}"
                    
                    text += f" | Цена: {template.base_price} золота\n\n"
        else:
            text += "Инвентарь пуст.\n\n"
        
        text += html.bold("ДЕЙСТВИЯ:")
        
        keyboard_buttons = []
        
        if items:
            for i, item in enumerate(items[:5], 1):
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"{i}. {item.template.name[:15]}..." if item.template else f"{i}. Предмет",
                        callback_data=f"inventory_item_{item.id}"
                    )
                ])
        
        keyboard_buttons.extend([
            [
                InlineKeyboardButton(text="🔍 Поиск", callback_data="inventory_search"),
                InlineKeyboardButton(text="📊 Сортировка", callback_data="inventory_sort")
            ],
            [
                InlineKeyboardButton(text="➡️ Следующая страница", callback_data="inventory_view_page_2") 
                if total > 10 else None
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="inventory")]
        ])
        
        # Убираем None кнопки
        keyboard_buttons = [row for row in keyboard_buttons if any(row)]
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(InventoryStates.inventory_view)

@inventory_router.callback_query(F.data.startswith("inventory_item_"))
async def handle_inventory_item(callback: CallbackQuery, state: FSMContext):
    """Просмотр деталей предмета"""
    from database import get_db_session
    
    item_id = uuid.UUID(callback.data.replace("inventory_item_", ""))
    
    async with get_db_session() as db:
        inventory_manager = InventoryManager(None, get_db_session)
        item_details = await inventory_manager.get_item_details(db, item_id)
        
        if not item_details:
            await callback.answer("Предмет не найден")
            return
        
        item = item_details["item"]
        template = item_details["template"]
        basic_info = item_details["basic_info"]
        
        text = html.bold(f"{template.icon} {basic_info['name']}\n\n")
        
        text += html.bold("📝 ОПИСАНИЕ:\n")
        text += f"{basic_info['description']}\n\n"
        
        text += html.bold("📊 ХАРАКТЕРИСТИКИ:\n")
        text += f"📦 Тип: {basic_info['type']}\n"
        text += f"🎨 Редкость: {basic_info['rarity']}\n"
        text += f"📈 Уровень: {basic_info['level_requirement']}+\n\n"
        
        if "stats" in item_details:
            text += html.bold("⚔️ СТАТИСТИКИ:\n")
            for stat_name, stat_value in item_details["stats"].items():
                text += f"• {stat_name}: {stat_value}\n"
            text += "\n"
        
        if "bonuses" in item_details:
            text += html.bold("✨ БОНУСЫ:\n")
            for bonus in item_details["bonuses"]:
                text += f"• {bonus}\n"
            text += "\n"
        
        text += html.bold("💰 ЭКОНОМИКА:\n")
        economy = item_details["economy"]
        text += f"Цена покупки: {economy['base_price']} золота\n"
        text += f"Цена продажи: {economy['sell_price']} золота\n"
        text += f"Рыночная стоимость: ~{economy['market_value']} золота\n\n"
        
        if item.quantity > 1:
            text += f"📦 Количество: {item.quantity}\n\n"
        
        keyboard_buttons = []
        
        # Проверяем можно ли экипировать
        if template.is_equippable and not item.is_equipped:
            keyboard_buttons.append([
                InlineKeyboardButton(text="🛡️ Экипировать", callback_data=f"item_equip_{item.id}")
            ])
        elif item.is_equipped:
            keyboard_buttons.append([
                InlineKeyboardButton(text="📦 Снять", callback_data=f"item_unequip_{item.id}")
            ])
        
        # Проверяем можно ли использовать
        if template.is_consumable:
            keyboard_buttons.append([
                InlineKeyboardButton(text="🧪 Использовать", callback_data=f"item_use_{item.id}")
            ])
        
        # Всегда доступные действия
        keyboard_buttons.append([
            InlineKeyboardButton(text="💰 Продать", callback_data=f"item_sell_{item.id}"),
            InlineKeyboardButton(text="🗑️ Выбросить", callback_data=f"item_drop_{item.id}")
        ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="inventory_view")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(InventoryStates.item_details)

@inventory_router.callback_query(F.data.startswith("item_equip_"))
async def handle_item_equip(callback: CallbackQuery, state: FSMContext):
    """Экипировать предмет"""
    from database import get_db_session
    
    item_id = uuid.UUID(callback.data.replace("item_equip_", ""))
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        inventory_manager = InventoryManager(None, get_db_session)
        success, message, unequipped_item = await inventory_manager.equip_item(db, user.id, item_id)
        
        if success:
            text = html.bold("✅ ПРЕДМЕТ ЭКИПИРОВАН\n\n")
            text += f"{message}\n\n"
            
            if unequipped_item and unequipped_item.template:
                text += f"📦 Снят предмет: {unequipped_item.template.name}\n"
            
            # Показываем обновленные характеристики
            await db.refresh(user)
            text += f"\n❤️ HP: {user.current_hp}/{user.max_hp}\n"
            text += f"🔷 MP: {user.current_mp}/{user.max_mp}\n"
        else:
            text = html.bold("❌ ОШИБКА\n\n")
            text += f"{message}"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"inventory_item_{item_id}")],
            [InlineKeyboardButton(text="🎒 В инвентарь", callback_data="inventory_view")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(InventoryStates.item_details)

@inventory_router.callback_query(F.data.startswith("item_use_"))
async def handle_item_use(callback: CallbackQuery, state: FSMContext):
    """Использовать предмет"""
    from database import get_db_session
    
    item_id = uuid.UUID(callback.data.replace("item_use_", ""))
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        inventory_manager = InventoryManager(None, get_db_session)
        success, message, result = await inventory_manager.use_item(db, user.id, item_id)
        
        if success:
            text = html.bold("✅ ПРЕДМЕТ ИСПОЛЬЗОВАН\n\n")
            text += f"{message}\n\n"
            
            if result.get("heal", 0) > 0:
                text += f"❤️ Восстановлено HP: {result['heal']}\n"
            if result.get("mana", 0) > 0:
                text += f"🔷 Восстановлено MP: {result['mana']}\n"
            
            if result.get("buffs"):
                text += "\n✨ Полученные баффы:\n"
                for buff in result["buffs"]:
                    text += f"• {buff['type']}: +{buff['value']*100}%\n"
            
            # Показываем текущее состояние
            await db.refresh(user)
            text += f"\n❤️ HP: {user.current_hp}/{user.max_hp}\n"
            text += f"🔷 MP: {user.current_mp}/{user.max_mp}\n"
        else:
            text = html.bold("❌ ОШИБКА\n\n")
            text += f"{message}"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"inventory_item_{item_id}")],
            [InlineKeyboardButton(text="🎒 В инвентарь", callback_data="inventory_view")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(InventoryStates.item_details)

@inventory_router.callback_query(F.data.startswith("item_sell_"))
async def handle_item_sell(callback: CallbackQuery, state: FSMContext):
    """Продать предмет"""
    from database import get_db_session
    
    item_id = uuid.UUID(callback.data.replace("item_sell_", ""))
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        item = await db.get(Item, item_id)
        if not item or item.owner_id != user.id:
            await callback.answer("Предмет не найден")
            return
        
        template = item.template
        if not template:
            await callback.answer("Шаблон предмета не найден")
            return
        
        text = html.bold("💰 ПРОДАЖА ПРЕДМЕТА\n\n")
        text += f"{template.icon} {template.name}\n\n"
        
        if item.quantity > 1:
            text += f"📦 Количество: {item.quantity}\n"
            text += f"💰 Цена за штуку: {template.sell_price} золота\n"
            text += f"💰 Общая стоимость: {template.sell_price * item.quantity} золота\n\n"
            text += "Введите количество для продажи:"
            
            await callback.message.edit_text(text, parse_mode="HTML")
            await state.update_data(selling_item_id=item_id)
            await state.set_state(InventoryStates.item_sell)
            
        else:
            text += f"💰 Стоимость: {template.sell_price} золота\n\n"
            text += "Вы уверены, что хотите продать этот предмет?"
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Да, продать", 
                                       callback_data=f"item_sell_confirm_{item_id}_1"),
                    InlineKeyboardButton(text="❌ Нет", 
                                       callback_data=f"inventory_item_{item_id}")
                ]
            ])
            
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            await state.set_state(InventoryStates.item_sell_confirm)

@inventory_router.message(InventoryStates.item_sell)
async def handle_item_sell_quantity(message: Message, state: FSMContext):
    """Обработчик количества для продажи"""
    from database import get_db_session
    
    try:
        quantity = int(message.text.strip())
        if quantity <= 0:
            await message.answer("Введите положительное число.")
            return
    except ValueError:
        await message.answer("Пожалуйста, введите число.")
        return
    
    data = await state.get_data()
    item_id = data.get('selling_item_id')
    
    if not item_id:
        await message.answer("Ошибка: предмет не найден.")
        return
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await message.answer("Игрок не найден.")
            return
        
        item = await db.get(Item, item_id)
        if not item or item.owner_id != user.id:
            await message.answer("Предмет не найден.")
            return
        
        template = item.template
        if not template:
            await message.answer("Шаблон предмета не найден.")
            return
        
        if quantity > item.quantity:
            await message.answer(f"У вас только {item.quantity} штук.")
            return
        
        total_price = template.sell_price * quantity
        
        text = html.bold("💰 ПОДТВЕРЖДЕНИЕ ПРОДАЖИ\n\n")
        text += f"{template.icon} {template.name} ×{quantity}\n"
        text += f"💰 Общая стоимость: {total_price} золота\n\n"
        text += "Вы уверены, что хотите продать?"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, продать", 
                                   callback_data=f"item_sell_confirm_{item_id}_{quantity}"),
                InlineKeyboardButton(text="❌ Нет", 
                                   callback_data=f"inventory_item_{item_id}")
            ]
        ])
        
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(InventoryStates.item_sell_confirm)

@inventory_router.callback_query(F.data.startswith("item_sell_confirm_"))
async def handle_item_sell_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение продажи"""
    from database import get_db_session
    
    # Парсим данные: item_sell_confirm_{item_id}_{quantity}
    parts = callback.data.replace("item_sell_confirm_", "").split("_")
    if len(parts) < 2:
        await callback.answer("Ошибка данных")
        return
    
    item_id = uuid.UUID(parts[0])
    quantity = int(parts[1])
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        inventory_manager = InventoryManager(None, get_db_session)
        success, message, price = await inventory_manager.sell_item(db, user.id, item_id, quantity)
        
        if success:
            text = html.bold("✅ ПРЕДМЕТ ПРОДАН\n\n")
            text += f"{message}\n\n"
            text += f"💰 Новый баланс: {format_number(user.gold)} золота"
        else:
            text = html.bold("❌ ОШИБКА ПРОДАЖИ\n\n")
            text += f"{message}"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎒 В инвентарь", callback_data="inventory_view")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(InventoryStates.inventory_view)

@inventory_router.callback_query(F.data == "inventory_crafting")
async def handle_crafting_menu(callback: CallbackQuery, state: FSMContext):
    """Меню крафта"""
    from database import get_db_session
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        inventory_manager = InventoryManager(None, get_db_session)
        active_craft = await inventory_manager.get_active_craft(db, user.id)
        
        text = html.bold("🔨 КРАФТ\n\n")
        
        if active_craft:
            remaining = (active_craft.end_time - datetime.utcnow()).seconds
            minutes = remaining // 60
            seconds = remaining % 60
            
            text += html.bold("⏳ АКТИВНЫЙ КРАФТ:\n")
            text += f"Предмет: {active_craft.data.get('recipe_name', 'Неизвестно')}\n"
            text += f"Осталось: {minutes}:{seconds:02d}\n\n"
            
            keyboard_buttons = [
                [InlineKeyboardButton(text="⏳ Просмотреть прогресс", callback_data="crafting_progress")],
                [InlineKeyboardButton(text="❌ Отменить крафт", callback_data="crafting_cancel")]
            ]
        else:
            text += html.bold("🎓 ПРОФЕССИИ:\n")
            text += f"⛏️ Горное дело: {user.mining_level}\n"
            text += f"🌳 Рубка дерева: {user.woodcutting_level}\n"
            text += f"🌿 Травничество: {user.herbalism_level}\n"
            text += f"⚒️ Кузнечное дело: {user.blacksmithing_level}\n"
            text += f"🧪 Алхимия: {user.alchemy_level}\n\n"
            
            text += html.bold("📚 ДОСТУПНЫЕ ПРОФЕССИИ:")
            
            keyboard_buttons = [
                [InlineKeyboardButton(text="⚒️ Кузнечное дело", callback_data="crafting_blacksmithing")],
                [InlineKeyboardButton(text="🧪 Алхимия", callback_data="crafting_alchemy")],
                [InlineKeyboardButton(text="🧵 Портняжное дело", callback_data="crafting_tailoring")],
                [InlineKeyboardButton(text="💎 Ювелирное дело", callback_data="crafting_jewelry")],
                [InlineKeyboardButton(text="🍳 Кулинария", callback_data="crafting_cooking")],
                [InlineKeyboardButton(text="✨ Зачарование", callback_data="crafting_enchanting")]
            ]
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="inventory")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(InventoryStates.crafting_menu)

@inventory_router.callback_query(F.data.startswith("crafting_"))
async def handle_crafting_profession(callback: CallbackQuery, state: FSMContext):
    """Выбор профессии для крафта"""
    profession_map = {
        "crafting_blacksmithing": ProfessionType.BLACKSMITHING,
        "crafting_alchemy": ProfessionType.ALCHEMY,
        "crafting_tailoring": ProfessionType.TAILORING,
        "crafting_jewelry": ProfessionType.JEWELRY,
        "crafting_cooking": ProfessionType.COOKING,
        "crafting_enchanting": ProfessionType.ENCHANTING
    }
    
    profession = profession_map.get(callback.data)
    if not profession:
        await callback.answer("Профессия не найдена")
        return
    
    await state.update_data(crafting_profession=profession)
    
    from database import get_db_session
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        inventory_manager = InventoryManager(None, get_db_session)
        recipes = await inventory_manager.get_available_recipes(db, user.id, profession)
        
        text = html.bold(f"🔨 {profession.value.upper()}\n\n")
        
        if recipes:
            text += html.bold("📚 ДОСТУПНЫЕ РЕЦЕПТЫ:\n\n")
            
            for i, recipe in enumerate(recipes[:5], 1):
                result_item = recipe.result_item
                if result_item:
                    text += f"{i}. {result_item.icon} {result_item.name}\n"
                    text += f"   Уровень: {recipe.profession_level} | Время: {recipe.craft_time//60}:{recipe.craft_time%60:02d}\n\n"
        else:
            text += "Нет доступных рецептов для этой профессии.\n\n"
            text += "Рецепты открываются с повышением уровня профессии."
        
        keyboard_buttons = []
        
        if recipes:
            for i, recipe in enumerate(recipes[:5], 1):
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"{i}. {recipe.result_item.name[:20] if recipe.result_item else 'Рецепт'}",
                        callback_data=f"recipe_view_{recipe.id}"
                    )
                ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="inventory_crafting")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(InventoryStates.crafting_recipes)

@inventory_router.callback_query(F.data.startswith("recipe_view_"))
async def handle_recipe_view(callback: CallbackQuery, state: FSMContext):
    """Просмотр рецепта"""
    from database import get_db_session
    
    recipe_id = uuid.UUID(callback.data.replace("recipe_view_", ""))
    
    async with get_db_session() as db:
        inventory_manager = InventoryManager(None, get_db_session)
        recipe_details = await inventory_manager.get_recipe_details(db, recipe_id)
        
        if not recipe_details:
            await callback.answer("Рецепт не найден")
            return
        
        recipe = recipe_details["recipe"]
        ingredients = recipe_details["ingredients"]
        result = recipe_details["result"]
        requirements = recipe_details["requirements"]
        
        text = html.bold(f"📖 РЕЦЕПТ: {recipe.name}\n\n")
        
        text += html.bold("🎯 РЕЗУЛЬТАТ:\n")
        if result:
            text += f"{result['icon']} {result['name']} ×{result['quantity']}\n\n"
        
        text += html.bold("📦 ИНГРЕДИЕНТЫ:\n")
        for ingredient in ingredients:
            text += f"{ingredient['icon']} {ingredient['name']} ×{ingredient['quantity']}\n"
        text += "\n"
        
        text += html.bold("📋 ТРЕБОВАНИЯ:\n")
        text += f"🎓 Профессия: {requirements['profession_type']} {requirements['profession_level']}\n"
        text += f"⏱️ Время: {requirements['craft_time']//60}:{requirements['craft_time']%60:02d}\n"
        text += f"💰 Стоимость: {requirements['gold_cost']} золота\n\n"
        
        # Проверяем возможность крафта
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if user:
            can_craft, errors = await inventory_manager.can_craft_recipe(db, user.id, recipe_id)
            
            if can_craft:
                text += html.bold("✅ МОЖНО СКРАФТИТЬ\n")
            else:
                text += html.bold("❌ НЕЛЬЗЯ СКРАФТИТЬ:\n")
                for error in errors:
                    text += f"• {error}\n"
        
        keyboard_buttons = []
        
        if user and can_craft:
            keyboard_buttons.append([
                InlineKeyboardButton(text="🔨 Начать крафт", callback_data=f"recipe_craft_{recipe_id}")
            ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"crafting_{recipe.profession_type.value}")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(InventoryStates.crafting_recipe_details)

@inventory_router.callback_query(F.data.startswith("recipe_craft_"))
async def handle_recipe_craft(callback: CallbackQuery, state: FSMContext):
    """Начать крафт по рецепту"""
    from database import get_db_session
    
    recipe_id = uuid.UUID(callback.data.replace("recipe_craft_", ""))
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        inventory_manager = InventoryManager(None, get_db_session)
        success, message, craft_action = await inventory_manager.start_crafting(db, user.id, recipe_id)
        
        if success:
            text = html.bold("🔨 КРАФТ НАЧАТ\n\n")
            text += f"{message}\n\n"
            text += "Вы получите уведомление по завершении."
        else:
            text = html.bold("❌ ОШИБКА КРАФТА\n\n")
            text += f"{message}"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏳ Активные крафты", callback_data="inventory_crafting")],
            [InlineKeyboardButton(text="🎒 В инвентарь", callback_data="inventory")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(InventoryStates.crafting_in_progress)

@inventory_router.callback_query(F.data == "inventory_auction")
async def handle_auction_menu(callback: CallbackQuery, state: FSMContext):
    """Меню аукциона"""
    text = html.bold("🏦 АУКЦИОН\n\n")
    text += html.bold("ДОСТУПНЫЕ ДЕЙСТВИЯ:\n\n")
    text += "• Просмотр лотов - поиск предметов на аукционе\n"
    text += "• Создать лот - выставить предмет на продажу\n"
    text += "• Мои лоты - управление выставленными предметами\n"
    text += "• Мои ставки - просмотр активных ставок\n"
    text += "• История торгов - завершенные сделки\n\n"
    text += html.bold("📊 КОМИССИЯ: 5% от суммы продажи")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Просмотр лотов", callback_data="auction_browse")],
        [InlineKeyboardButton(text="➕ Создать лот", callback_data="auction_create")],
        [InlineKeyboardButton(text="📋 Мои лоты", callback_data="auction_my")],
        [InlineKeyboardButton(text="💰 Мои ставки", callback_data="auction_bids")],
        [InlineKeyboardButton(text="📜 История", callback_data="auction_history")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="inventory")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await state.set_state(InventoryStates.auction_menu)

@inventory_router.callback_query(F.data == "inventory_storage")
async def handle_storage_menu(callback: CallbackQuery, state: FSMContext):
    """Меню хранилища"""
    from database import get_db_session
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        inventory_manager = InventoryManager(None, get_db_session)
        storage_capacity = await inventory_manager.get_storage_capacity(db, user.id)
        storage_items = await inventory_manager.get_storage_items(db, user.id)
        
        text = html.bold("📦 ХРАНИЛИЩЕ\n\n")
        
        text += html.bold("📊 ИНФОРМАЦИЯ:\n")
        text += f"📦 Слотов: {storage_capacity['used_slots']}/{storage_capacity['max_slots']}\n"
        text += f"📈 Уровень: {storage_capacity['upgrade_level']}\n"
        text += f"💰 Следующее улучшение: {storage_capacity['next_upgrade_cost']} золота\n\n"
        
        text += html.bold("📦 ПРЕДМЕТЫ В ХРАНИЛИЩЕ:\n")
        if storage_items:
            for item in storage_items[:5]:
                template = item["template"]
                text += f"{template.icon} {template.name} ×{item['quantity']}\n"
        else:
            text += "Хранилище пусто.\n"
        
        keyboard_buttons = [
            [InlineKeyboardButton(text="📥 Положить предмет", callback_data="storage_deposit")],
            [InlineKeyboardButton(text="📤 Забрать предмет", callback_data="storage_withdraw")]
        ]
        
        if storage_capacity["upgrade_level"] < 5:  # Максимум 5 уровней
            keyboard_buttons.append([
                InlineKeyboardButton(text="🔼 Улучшить хранилище", callback_data="storage_upgrade")
            ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="inventory")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(InventoryStates.storage_menu)

@inventory_router.callback_query(F.data == "inventory_repair")
async def handle_repair_menu(callback: CallbackQuery, state: FSMContext):
    """Меню ремонта"""
    from database import get_db_session
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        inventory_manager = InventoryManager(None, get_db_session)
        repairable_items = await inventory_manager.get_repairable_items(db, user.id)
        
        text = html.bold("🔧 РЕМОНТ ПРЕДМЕТОВ\n\n")
        
        if repairable_items:
            text += html.bold("📦 ПОВРЕЖДЕННЫЕ ПРЕДМЕТЫ:\n\n")
            
            for i, item in enumerate(repairable_items[:5], 1):
                template = item.template
                if template:
                    durability_percent = (item.current_durability / item.max_durability) * 100
                    repair_cost = int(template.base_price * (1 - (item.current_durability / item.max_durability)) * 0.3)
                    
                    text += f"{i}. {template.icon} {template.name}\n"
                    text += f"   Прочность: {item.current_durability}/{item.max_durability} ({durability_percent:.0f}%)\n"
                    text += f"   Стоимость ремонта: {repair_cost} золота\n\n"
        else:
            text += "Нет предметов требующих ремонта.\n\n"
        
        text += html.bold("💡 ПОДСКАЗКА:\n")
        text += "• Стоимость ремонта: 30% от стоимости утраченной прочности\n"
        text += "• Экипированные предметы можно ремонтировать\n"
        text += "• Предметы с прочностью 0% не ломаются, но перестают давать бонусы"
        
        keyboard_buttons = []
        
        if repairable_items:
            for i, item in enumerate(repairable_items[:5], 1):
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"{i}. Ремонт {item.template.name[:15]}...",
                        callback_data=f"repair_item_{item.id}"
                    )
                ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="🔧 Ремонт всей экипировки", callback_data="repair_all")
        ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="inventory")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    
    await state.set_state(InventoryStates.repair_menu)

# ============ ИНИЦИАЛИЗАЦИЯ ============

async def init_inventory_module(redis_client, db_session_factory):
    """Инициализировать модуль инвентаря"""
    inventory_manager = InventoryManager(redis_client, db_session_factory)
    await inventory_manager.restore_state()
    return inventory_manager

# Утилиты форматирования
def format_number(num: int) -> str:
    """Форматировать число с разделителями"""
    return f"{num:,}".replace(",", " ")

# Экспортируемые объекты
__all__ = [
    'inventory_router',
    'InventoryManager',
    'init_inventory_module',
    'InventoryStates',
    'InventoryAction',
    'CraftingStatus',
    'SortType',
    'format_number'
]