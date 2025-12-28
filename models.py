from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from enum import Enum
from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, Float, 
    DateTime, ForeignKey, Text, JSON, BigInteger, Numeric,
    Table, Index, CheckConstraint, UniqueConstraint, Enum as SQLEnum
)
from sqlalchemy.orm import declarative_base, relationship, Session, sessionmaker
from sqlalchemy.dialects.postgresql import UUID, ARRAY
import json
import uuid

Base = declarative_base()

# ============ ПЕРЕЧИСЛЕНИЯ ============

class UserRole(str, Enum):
    PLAYER = "player"
    ADMIN = "admin"
    MODERATOR = "moderator"

class ItemType(str, Enum):
    WEAPON = "weapon"
    ARMOR = "armor"
    POTION = "potion"
    RESOURCE = "resource"
    MATERIAL = "material"
    FOOD = "food"
    KEY = "key"
    OTHER = "other"

class ItemRarity(str, Enum):
    COMMON = "common"
    UNCOMMON = "uncommon"
    RARE = "rare"
    EPIC = "epic"
    LEGENDARY = "legendary"
    MYTHIC = "mythic"

class MobType(str, Enum):
    BEAST = "beast"
    HUMANOID = "humanoid"
    UNDEAD = "undead"
    DEMON = "demon"
    ELEMENTAL = "elemental"
    DRAGON = "dragon"

class LocationType(str, Enum):
    SAFE = "safe"
    DANGEROUS = "dangerous"
    VERY_DANGEROUS = "very_dangerous"
    DUNGEON = "dungeon"

class EventType(str, Enum):
    HOSTILE = "hostile"
    NEUTRAL = "neutral"
    FRIENDLY = "friendly"
    QUEST = "quest"
    WEATHER = "weather"

class ResourceType(str, Enum):
    ORE = "ore"
    WOOD = "wood"
    HERB = "herb"
    FISH = "fish"
    CLOTH = "cloth"

class ProfessionType(str, Enum):
    MINING = "mining"
    WOODCUTTING = "woodcutting"
    HERBALISM = "herbalism"
    BLACKSMITHING = "blacksmithing"
    ALCHEMY = "alchemy"
    ENCHANTING = "enchanting"
    TAILORING = "tailoring"
    JEWELRY = "jewelry"
    COOKING = "cooking"

class ActionType(str, Enum):
    TRAVEL = "travel"
    BATTLE = "battle"
    MINING = "mining"
    WOODCUTTING = "woodcutting"
    HERBALISM = "herbalism"
    CRAFTING = "crafting"
    PVP = "pvp"
    EVENT = "event"

class BattleStatus(str, Enum):
    ACTIVE = "active"
    PLAYER_WON = "player_won"
    PLAYER_LOST = "player_lost"
    FLED = "fled"

class EventActivationType(str, Enum):
    CHANCE = "chance"
    TIME = "time"
    MANUAL = "manual"
    SCHEDULED = "scheduled"

# ============ МОДЕЛИ ПОЛЬЗОВАТЕЛЕЙ ============

class User(Base):
    __tablename__ = 'users'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(100), nullable=True)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    role = Column(SQLEnum(UserRole), default=UserRole.PLAYER)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_active = Column(DateTime, default=datetime.utcnow)
    
    # Статистика игрока
    level = Column(Integer, default=1)
    experience = Column(BigInteger, default=0)
    gold = Column(BigInteger, default=100)
    crystals = Column(BigInteger, default=0)
    
    # Основные характеристики
    strength = Column(Integer, default=10)
    agility = Column(Integer, default=10)
    intelligence = Column(Integer, default=10)
    constitution = Column(Integer, default=10)
    free_points = Column(Integer, default=0)
    
    # Профессии
    mining_level = Column(Integer, default=1)
    mining_exp = Column(Integer, default=0)
    woodcutting_level = Column(Integer, default=1)
    woodcutting_exp = Column(Integer, default=0)
    herbalism_level = Column(Integer, default=1)
    herbalism_exp = Column(Integer, default=0)
    blacksmithing_level = Column(Integer, default=1)
    blacksmithing_exp = Column(Integer, default=0)
    alchemy_level = Column(Integer, default=1)
    alchemy_exp = Column(Integer, default=0)
    
    # Текущее состояние
    current_hp = Column(Integer, default=100)
    max_hp = Column(Integer, default=100)
    current_mp = Column(Integer, default=50)
    max_mp = Column(Integer, default=50)
    stamina = Column(Integer, default=100)
    
    # Экипировка
    weapon_id = Column(UUID(as_uuid=True), ForeignKey('items.id'), nullable=True)
    armor_id = Column(UUID(as_uuid=True), ForeignKey('items.id'), nullable=True)
    helmet_id = Column(UUID(as_uuid=True), ForeignKey('items.id'), nullable=True)
    gloves_id = Column(UUID(as_uuid=True), ForeignKey('items.id'), nullable=True)
    boots_id = Column(UUID(as_uuid=True), ForeignKey('items.id'), nullable=True)
    
    # Локация
    current_location_id = Column(UUID(as_uuid=True), ForeignKey('locations.id'), nullable=True)
    
    # Статистика
    mobs_killed = Column(Integer, default=0)
    players_killed = Column(Integer, default=0)
    deaths = Column(Integer, default=0)
    total_gold_earned = Column(BigInteger, default=0)
    total_gold_spent = Column(BigInteger, default=0)
    total_damage_dealt = Column(BigInteger, default=0)
    total_damage_taken = Column(BigInteger, default=0)
    
    # Настройки
    notifications_enabled = Column(Boolean, default=True)
    language = Column(String(10), default='ru')
    
    # Связи
    current_location = relationship("Location", foreign_keys=[current_location_id])
    weapon = relationship("Item", foreign_keys=[weapon_id])
    armor = relationship("Item", foreign_keys=[armor_id])
    helmet = relationship("Item", foreign_keys=[helmet_id])
    gloves = relationship("Item", foreign_keys=[gloves_id])
    boots = relationship("Item", foreign_keys=[boots_id])
    
    # Индексы
    __table_args__ = (
        Index('idx_user_telegram_id', 'telegram_id'),
        Index('idx_user_role', 'role'),
        Index('idx_user_level', 'level'),
    )

# ============ МОДЕЛИ ПРЕДМЕТОВ ============

class ItemTemplate(Base):
    __tablename__ = 'item_templates'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    icon = Column(String(50), nullable=False)
    item_type = Column(SQLEnum(ItemType), nullable=False)
    rarity = Column(SQLEnum(ItemRarity), default=ItemRarity.COMMON)
    level_requirement = Column(Integer, default=1)
    
    # Статистика (для оружия/брони)
    damage_min = Column(Integer, default=0)
    damage_max = Column(Integer, default=0)
    defense = Column(Integer, default=0)
    health_bonus = Column(Integer, default=0)
    mana_bonus = Column(Integer, default=0)
    strength_bonus = Column(Integer, default=0)
    agility_bonus = Column(Integer, default=0)
    intelligence_bonus = Column(Integer, default=0)
    constitution_bonus = Column(Integer, default=0)
    
    # Для зелий
    potion_effect = Column(JSON, nullable=True)  # {"heal": 50, "duration": 300}
    
    # Для ресурсов
    resource_type = Column(SQLEnum(ResourceType), nullable=True)
    weight = Column(Float, default=0.1)
    
    # Экономика
    base_price = Column(Integer, nullable=False)
    sell_price = Column(Integer, nullable=False)
    stack_size = Column(Integer, default=1)
    
    # Флаги
    is_tradable = Column(Boolean, default=True)
    is_droppable = Column(Boolean, default=True)
    is_consumable = Column(Boolean, default=False)
    is_equippable = Column(Boolean, default=False)
    
    # Для крафта
    craftable = Column(Boolean, default=False)
    craft_profession = Column(SQLEnum(ProfessionType), nullable=True)
    craft_level = Column(Integer, default=1)
    craft_time = Column(Integer, default=60)  # в секундах
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_item_template_type', 'item_type'),
        Index('idx_item_template_rarity', 'rarity'),
        Index('idx_item_template_level', 'level_requirement'),
    )

class Item(Base):
    __tablename__ = 'items'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id = Column(UUID(as_uuid=True), ForeignKey('item_templates.id'), nullable=False)
    owner_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    
    # Модификации
    current_durability = Column(Integer, nullable=True)
    max_durability = Column(Integer, nullable=True)
    enchantments = Column(JSON, nullable=True)  # [{"type": "fire", "value": 10}]
    
    # Для ресурсов
    quantity = Column(Integer, default=1)
    
    # Позиция в инвентаре
    slot = Column(Integer, nullable=True)
    
    # Флаги
    is_equipped = Column(Boolean, default=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Связи
    template = relationship("ItemTemplate")
    owner = relationship("User", foreign_keys=[owner_id])
    
    __table_args__ = (
        Index('idx_item_owner', 'owner_id'),
        Index('idx_item_template', 'template_id'),
    )

# ============ МОДЕЛИ ИНВЕНТАРЯ ============

class Inventory(Base):
    __tablename__ = 'inventories'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), unique=True, nullable=False)
    capacity = Column(Integer, default=50)
    max_capacity = Column(Integer, default=100)
    
    # Связи
    user = relationship("User", foreign_keys=[user_id], backref="inventory")
    items = relationship("Item", backref="inventory_ref")

# ============ МОДЕЛИ ЛОКАЦИЙ ============

class Location(Base):
    __tablename__ = 'locations'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    icon = Column(String(50), nullable=False)
    location_type = Column(SQLEnum(LocationType), nullable=False)
    min_level = Column(Integer, default=1)
    max_level = Column(Integer, default=100)
    base_xp_reward = Column(Integer, default=10)
    
    # Флаги
    has_mine = Column(Boolean, default=False)
    mine_level = Column(Integer, default=0)
    has_forest = Column(Boolean, default=False)
    has_herbs = Column(Boolean, default=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Связи
    travel_routes = relationship("TravelRoute", foreign_keys="TravelRoute.from_location_id")
    mob_spawns = relationship("MobSpawn", back_populates="location")
    resource_spawns = relationship("ResourceSpawn", back_populates="location")
    event_triggers = relationship("EventTrigger", back_populates="location")

class TravelRoute(Base):
    __tablename__ = 'travel_routes'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    from_location_id = Column(UUID(as_uuid=True), ForeignKey('locations.id'), nullable=False)
    to_location_id = Column(UUID(as_uuid=True), ForeignKey('locations.id'), nullable=False)
    travel_time = Column(Integer, nullable=False)  # в секундах
    min_level = Column(Integer, default=1)
    gold_cost = Column(Integer, default=0)
    
    # Связи
    from_location = relationship("Location", foreign_keys=[from_location_id])
    to_location = relationship("Location", foreign_keys=[to_location_id])
    
    __table_args__ = (
        UniqueConstraint('from_location_id', 'to_location_id', name='unique_route'),
        Index('idx_travel_from', 'from_location_id'),
        Index('idx_travel_to', 'to_location_id'),
    )

# ============ МОДЕЛИ МОБОВ ============

class MobTemplate(Base):
    __tablename__ = 'mob_templates'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    icon = Column(String(50), nullable=False)
    mob_type = Column(SQLEnum(MobType), nullable=False)
    level = Column(Integer, default=1)
    
    # Характеристики
    health = Column(Integer, nullable=False)
    damage_min = Column(Integer, nullable=False)
    damage_max = Column(Integer, nullable=False)
    defense = Column(Integer, default=0)
    attack_speed = Column(Float, default=1.0)
    
    # Шансы
    crit_chance = Column(Float, default=0.05)
    dodge_chance = Column(Float, default=0.05)
    
    # Награды
    base_xp = Column(Integer, nullable=False)
    gold_min = Column(Integer, default=0)
    gold_max = Column(Integer, default=0)
    
    # Флаги
    is_boss = Column(Boolean, default=False)
    respawn_time = Column(Integer, default=300)  # в секундах
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Связи
    drops = relationship("MobDrop", back_populates="mob_template")
    
    __table_args__ = (
        Index('idx_mob_template_level', 'level'),
        Index('idx_mob_template_type', 'mob_type'),
    )

class MobDrop(Base):
    __tablename__ = 'mob_drops'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mob_template_id = Column(UUID(as_uuid=True), ForeignKey('mob_templates.id'), nullable=False)
    item_template_id = Column(UUID(as_uuid=True), ForeignKey('item_templates.id'), nullable=False)
    
    drop_chance = Column(Float, nullable=False)  # 0.0 - 1.0
    min_quantity = Column(Integer, default=1)
    max_quantity = Column(Integer, default=1)
    
    # Связи
    mob_template = relationship("MobTemplate", back_populates="drops")
    item_template = relationship("ItemTemplate")

class MobSpawn(Base):
    __tablename__ = 'mob_spawns'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    location_id = Column(UUID(as_uuid=True), ForeignKey('locations.id'), nullable=False)
    mob_template_id = Column(UUID(as_uuid=True), ForeignKey('mob_templates.id'), nullable=False)
    
    spawn_chance = Column(Float, nullable=False)  # 0.0 - 1.0
    min_level = Column(Integer, default=1)
    max_level = Column(Integer, default=100)
    max_count = Column(Integer, default=10)
    
    # Связи
    location = relationship("Location", back_populates="mob_spawns")
    mob_template = relationship("MobTemplate")

# ============ МОДЕЛИ РЕСУРСОВ ============

class ResourceTemplate(Base):
    __tablename__ = 'resource_templates'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    icon = Column(String(50), nullable=False)
    resource_type = Column(SQLEnum(ResourceType), nullable=False)
    level = Column(Integer, default=1)
    
    # Параметры сбора
    gather_chance = Column(Float, nullable=False)  # 0.0 - 1.0
    min_quantity = Column(Integer, default=1)
    max_quantity = Column(Integer, default=1)
    gather_time = Column(Integer, default=60)  # в секундах
    
    # Требования
    required_strength = Column(Integer, default=0)
    required_profession_level = Column(Integer, default=1)
    
    # Свойства
    weight = Column(Float, default=0.1)
    base_price = Column(Integer, nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_resource_type', 'resource_type'),
        Index('idx_resource_level', 'level'),
    )

class ResourceSpawn(Base):
    __tablename__ = 'resource_spawns'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    location_id = Column(UUID(as_uuid=True), ForeignKey('locations.id'), nullable=False)
    resource_template_id = Column(UUID(as_uuid=True), ForeignKey('resource_templates.id'), nullable=False)
    
    spawn_chance = Column(Float, nullable=False)  # 0.0 - 1.0
    respawn_time = Column(Integer, default=600)  # в секундах
    max_count = Column(Integer, default=100)
    
    # Связи
    location = relationship("Location", back_populates="resource_spawns")
    resource_template = relationship("ResourceTemplate")

# ============ МОДЕЛИ СОБЫТИЙ ============

class GameEvent(Base):
    __tablename__ = 'game_events'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    icon = Column(String(50), nullable=False)
    event_type = Column(SQLEnum(EventType), nullable=False)
    
    # Активация
    activation_type = Column(SQLEnum(EventActivationType), default=EventActivationType.CHANCE)
    base_chance = Column(Float, default=0.2)  # 20%
    min_player_level = Column(Integer, default=1)
    max_player_level = Column(Integer, default=100)
    
    # Время активации
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    duration = Column(Integer, default=3600)  # в секундах
    
    # Модификаторы
    mob_power_modifier = Column(Float, default=1.0)
    resource_spawn_modifier = Column(Float, default=1.0)
    
    # Награды
    reward_gold_min = Column(Integer, default=0)
    reward_gold_max = Column(Integer, default=0)
    reward_xp = Column(Integer, default=0)
    
    # Флаги
    is_active = Column(Boolean, default=False)
    is_repeatable = Column(Boolean, default=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Связи
    triggers = relationship("EventTrigger", back_populates="game_event")
    rewards = relationship("EventReward", back_populates="game_event")
    
    __table_args__ = (
        Index('idx_event_active', 'is_active'),
        Index('idx_event_type', 'event_type'),
    )

class EventTrigger(Base):
    __tablename__ = 'event_triggers'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey('game_events.id'), nullable=False)
    location_id = Column(UUID(as_uuid=True), ForeignKey('locations.id'), nullable=True)
    
    trigger_chance = Column(Float, default=1.0)
    
    # Связи
    game_event = relationship("GameEvent", back_populates="triggers")
    location = relationship("Location", back_populates="event_triggers")

class EventReward(Base):
    __tablename__ = 'event_rewards'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(UUID(as_uuid=True), ForeignKey('game_events.id'), nullable=False)
    item_template_id = Column(UUID(as_uuid=True), ForeignKey('item_templates.id'), nullable=False)
    
    drop_chance = Column(Float, nullable=False)  # 0.0 - 1.0
    min_quantity = Column(Integer, default=1)
    max_quantity = Column(Integer, default=1)
    
    # Связи
    game_event = relationship("GameEvent", back_populates="rewards")
    item_template = relationship("ItemTemplate")

# ============ МОДЕЛИ СУНДУКОВ ============

class ChestTemplate(Base):
    __tablename__ = 'chest_templates'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    icon = Column(String(50), nullable=False)
    rarity = Column(SQLEnum(ItemRarity), nullable=False)
    level = Column(Integer, default=1)
    
    # Распределение
    spawn_chance = Column(Float, default=0.05)  # 5%
    min_player_level = Column(Integer, default=1)
    max_player_level = Column(Integer, default=100)
    
    # Опасности
    trap_chance = Column(Float, default=0.0)
    trap_type = Column(String(50), nullable=True)
    trap_damage = Column(Integer, default=0)
    
    # Требования для открытия
    required_key_id = Column(UUID(as_uuid=True), ForeignKey('item_templates.id'), nullable=True)
    required_lockpicking = Column(Integer, default=0)
    required_strength = Column(Integer, default=0)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Связи
    required_key = relationship("ItemTemplate", foreign_keys=[required_key_id])
    rewards = relationship("ChestReward", back_populates="chest_template")
    
    __table_args__ = (
        Index('idx_chest_rarity', 'rarity'),
        Index('idx_chest_level', 'level'),
    )

class ChestReward(Base):
    __tablename__ = 'chest_rewards'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chest_template_id = Column(UUID(as_uuid=True), ForeignKey('chest_templates.id'), nullable=False)
    item_template_id = Column(UUID(as_uuid=True), ForeignKey('item_templates.id'), nullable=False)
    
    drop_chance = Column(Float, nullable=False)  # 0.0 - 1.0
    min_quantity = Column(Integer, default=1)
    max_quantity = Column(Integer, default=1)
    is_guaranteed = Column(Boolean, default=False)
    
    # Связи
    chest_template = relationship("ChestTemplate", back_populates="rewards")
    item_template = relationship("ItemTemplate")

# ============ МОДЕЛИ КРАФТА ============

class Recipe(Base):
    __tablename__ = 'recipes'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    
    # Результирующий предмет
    result_item_id = Column(UUID(as_uuid=True), ForeignKey('item_templates.id'), nullable=False)
    result_quantity = Column(Integer, default=1)
    
    # Требования
    profession_type = Column(SQLEnum(ProfessionType), nullable=False)
    profession_level = Column(Integer, default=1)
    craft_time = Column(Integer, default=60)  # в секундах
    gold_cost = Column(Integer, default=0)
    
    # Флаги
    is_discovered = Column(Boolean, default=False)
    discover_chance = Column(Float, default=0.0)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Связи
    result_item = relationship("ItemTemplate", foreign_keys=[result_item_id])
    ingredients = relationship("RecipeIngredient", back_populates="recipe")
    
    __table_args__ = (
        Index('idx_recipe_profession', 'profession_type'),
        Index('idx_recipe_level', 'profession_level'),
    )

class RecipeIngredient(Base):
    __tablename__ = 'recipe_ingredients'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recipe_id = Column(UUID(as_uuid=True), ForeignKey('recipes.id'), nullable=False)
    item_template_id = Column(UUID(as_uuid=True), ForeignKey('item_templates.id'), nullable=False)
    
    quantity = Column(Integer, nullable=False)
    
    # Связи
    recipe = relationship("Recipe", back_populates="ingredients")
    item_template = relationship("ItemTemplate")

# ============ МОДЕЛИ АКТИВНЫХ ДЕЙСТВИЙ ============

class ActiveAction(Base):
    __tablename__ = 'active_actions'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    action_type = Column(SQLEnum(ActionType), nullable=False)
    
    # Параметры действия
    target_id = Column(UUID(as_uuid=True), nullable=True)  # mob_id, location_id, etc
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    
    # Прогресс
    progress = Column(Float, default=0.0)  # 0.0 - 1.0
    is_completed = Column(Boolean, default=False)
    
    # Дополнительные данные
    data = Column(JSON, nullable=True)  # {"mob_hp": 100, "resources_gathered": []}
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Связи
    user = relationship("User", foreign_keys=[user_id])
    
    __table_args__ = (
        Index('idx_active_action_user', 'user_id'),
        Index('idx_active_action_type', 'action_type'),
        Index('idx_active_action_end', 'end_time'),
    )

class ActiveBattle(Base):
    __tablename__ = 'active_battles'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    mob_template_id = Column(UUID(as_uuid=True), ForeignKey('mob_templates.id'), nullable=True)
    pvp_target_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    
    # Статус битвы
    status = Column(SQLEnum(BattleStatus), default=BattleStatus.ACTIVE)
    
    # Здоровье участников
    player_hp = Column(Integer, nullable=False)
    player_max_hp = Column(Integer, nullable=False)
    target_hp = Column(Integer, nullable=False)
    target_max_hp = Column(Integer, nullable=False)
    
    # Ставки (для PvP)
    bet_amount = Column(BigInteger, default=0)
    
    # Время
    started_at = Column(DateTime, default=datetime.utcnow)
    last_action_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    
    # Дополнительные данные
    battle_log = Column(JSON, nullable=True)  # Массив действий в битве
    
    # Связи
    user = relationship("User", foreign_keys=[user_id])
    mob_template = relationship("MobTemplate")
    pvp_target = relationship("User", foreign_keys=[pvp_target_id])
    
    __table_args__ = (
        Index('idx_battle_user', 'user_id'),
        Index('idx_battle_status', 'status'),
    )

# ============ МОДЕЛИ ЭФФЕКТОВ ============

class ActiveEffect(Base):
    __tablename__ = 'active_effects'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    
    # Эффект
    effect_type = Column(String(100), nullable=False)  # "heal_over_time", "damage_buff", "poison"
    effect_power = Column(Float, nullable=False)
    
    # Время действия
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)
    
    # Источник эффекта
    source_type = Column(String(50), nullable=True)  # "potion", "enchantment", "skill"
    source_id = Column(UUID(as_uuid=True), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Связи
    user = relationship("User", foreign_keys=[user_id])
    
    __table_args__ = (
        Index('idx_effect_user', 'user_id'),
        Index('idx_effect_end', 'end_time'),
    )

# ============ МОДЕЛИ PvP ============

class PvPChallenge(Base):
    __tablename__ = 'pvp_challenges'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    challenger_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    target_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    
    # Ставка
    bet_amount = Column(BigInteger, nullable=False)
    
    # Статус
    status = Column(String(50), default='pending')  # pending, accepted, declined, cancelled
    
    # Время
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    
    # Связи
    challenger = relationship("User", foreign_keys=[challenger_id])
    target = relationship("User", foreign_keys=[target_id])
    
    __table_args__ = (
        Index('idx_pvp_challenger', 'challenger_id'),
        Index('idx_pvp_target', 'target_id'),
        Index('idx_pvp_status', 'status'),
    )

class PvPMatch(Base):
    __tablename__ = 'pvp_matches'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player1_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    player2_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    
    # Ставка
    bet_amount = Column(BigInteger, nullable=False)
    
    # Результат
    winner_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    loser_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    
    # Статистика
    player1_hp_lost = Column(Integer, default=0)
    player2_hp_lost = Column(Integer, default=0)
    rounds_count = Column(Integer, default=0)
    
    # Время
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    
    # Лог боя
    battle_log = Column(JSON, nullable=True)
    
    # Связи
    player1 = relationship("User", foreign_keys=[player1_id])
    player2 = relationship("User", foreign_keys=[player2_id])
    winner = relationship("User", foreign_keys=[winner_id])
    loser = relationship("User", foreign_keys=[loser_id])
    
    __table_args__ = (
        Index('idx_pvp_players', 'player1_id', 'player2_id'),
        Index('idx_pvp_winner', 'winner_id'),
        Index('idx_pvp_date', 'started_at'),
    )

# ============ МОДЕЛИ СИСТЕМЫ ============

class SystemSettings(Base):
    __tablename__ = 'system_settings'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(JSON, nullable=False)
    description = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_settings_key', 'key'),
    )

class AuditLog(Base):
    __tablename__ = 'audit_logs'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    action = Column(String(200), nullable=False)
    details = Column(JSON, nullable=True)
    ip_address = Column(String(45), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Связи
    user = relationship("User", foreign_keys=[user_id])
    
    __table_args__ = (
        Index('idx_audit_user', 'user_id'),
        Index('idx_audit_action', 'action'),
        Index('idx_audit_date', 'created_at'),
    )

class BackupLog(Base):
    __tablename__ = 'backup_logs'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename = Column(String(200), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    success = Column(Boolean, default=True)
    error_message = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_backup_date', 'created_at'),
    )

# ============ ВСПОМОГАТЕЛЬНЫЕ МОДЕЛИ ============

class PlayerStat(Base):
    __tablename__ = 'player_stats'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), unique=True, nullable=False)
    
    # Ежедневная статистика
    daily_mobs_killed = Column(Integer, default=0)
    daily_players_killed = Column(Integer, default=0)
    daily_gold_earned = Column(BigInteger, default=0)
    daily_items_found = Column(Integer, default=0)
    
    # Сессии
    current_session_start = Column(DateTime, nullable=True)
    total_play_time = Column(Integer, default=0)  # в секундах
    
    # Последние активности
    last_battle_time = Column(DateTime, nullable=True)
    last_travel_time = Column(DateTime, nullable=True)
    last_craft_time = Column(DateTime, nullable=True)
    last_pvp_time = Column(DateTime, nullable=True)
    
    # Связи
    user = relationship("User", foreign_keys=[user_id])
    
    __table_args__ = (
        Index('idx_stats_user', 'user_id'),
    )

class Discovery(Base):
    __tablename__ = 'discoveries'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    
    # Что открыто
    discovered_locations = Column(ARRAY(UUID), default=[])
    discovered_recipes = Column(ARRAY(UUID), default=[])
    discovered_events = Column(ARRAY(UUID), default=[])
    
    # Статистика открытий
    total_discoveries = Column(Integer, default=0)
    
    # Связи
    user = relationship("User", foreign_keys=[user_id])
    
    __table_args__ = (
        Index('idx_discovery_user', 'user_id'),
    )

# ============ СИСТЕМНЫЕ ТАБЛИЦЫ ДЛЯ ВОССТАНОВЛЕНИЯ ============

class StateSnapshot(Base):
    __tablename__ = 'state_snapshots'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_type = Column(String(50), nullable=False)  # 'active_action', 'battle', 'effect'
    
    # Данные для восстановления
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    entity_id = Column(UUID(as_uuid=True), nullable=False)
    entity_type = Column(String(100), nullable=False)
    snapshot_data = Column(JSON, nullable=False)
    
    # Время
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)  # Автоматическое удаление после восстановления
    
    # Флаги
    is_restored = Column(Boolean, default=False)
    
    # Связи
    user = relationship("User", foreign_keys=[user_id])
    
    __table_args__ = (
        Index('idx_snapshot_user', 'user_id'),
        Index('idx_snapshot_type', 'snapshot_type'),
        Index('idx_snapshot_expires', 'expires_at'),
    )

# ============ ФУНКЦИЯ СОЗДАНИЯ ВСЕХ ТАБЛИЦ ============

def create_all_tables(engine):
    """Создание всех таблиц в базе данных"""
    Base.metadata.create_all(engine)

# ============ УТИЛИТЫ ДЛЯ РАБОТЫ С БД ============

class DatabaseManager:
    def __init__(self, database_url: str):
        self.engine = create_engine(database_url, echo=False, pool_size=20, max_overflow=30)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        
    def get_session(self):
        """Получить сессию базы данных"""
        return self.SessionLocal()
    
    def create_tables(self):
        """Создать все таблицы"""
        create_all_tables(self.engine)
    
    def drop_tables(self):
        """Удалить все таблицы (для тестов)"""
        Base.metadata.drop_all(self.engine)
    
    def create_initial_data(self):
        """Создать начальные данные"""
        session = self.get_session()
        try:
            # Проверяем, есть ли уже начальные данные
            user_count = session.query(User).count()
            if user_count == 0:
                # Создаем админа
                admin_user = User(
                    telegram_id=123456789,
                    username="admin",
                    first_name="Admin",
                    role=UserRole.ADMIN,
                    level=100,
                    gold=999999,
                    strength=100,
                    agility=100,
                    intelligence=100,
                    constitution=100
                )
                session.add(admin_user)
                session.commit()
                print("✅ Создан пользователь-администратор")
        finally:
            session.close()

# Пример использования
if __name__ == "__main__":
    # Настройки подключения к PostgreSQL
    DATABASE_URL = "postgresql://user:password@localhost/rpg_bot"
    
    # Создание менеджера базы данных
    db_manager = DatabaseManager(DATABASE_URL)
    
    # Создание таблиц
    db_manager.create_tables()
    print("✅ Все таблицы созданы")
    
    # Создание начальных данных
    db_manager.create_initial_data()
    print("✅ Начальные данные созданы")