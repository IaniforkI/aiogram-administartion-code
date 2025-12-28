# battle_module.py
"""
Полная боевая система против мобов с восстановлением состояния при перезапуске.
Включает все типы боев, формулы расчета урона, систему способностей и наград.
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

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select, update, and_, or_, desc, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload

from models import (
    User, ActiveBattle, BattleStatus, MobTemplate, MobDrop,
    Item, ItemTemplate, ActiveAction, ActionType, StateSnapshot,
    AuditLog, PlayerStat, ActiveEffect, Inventory, Location,
    SystemSettings, Discovery, ItemRarity, ItemType, MobType
)

# ============ КОНСТАНТЫ И КОНФИГУРАЦИЯ ============

class BattleAction(str, Enum):
    ATTACK = "attack"
    DEFEND = "defend"
    DODGE = "dodge"
    USE_SKILL = "use_skill"
    USE_ITEM = "use_item"
    FLEE = "flee"

class SkillType(str, Enum):
    DAMAGE = "damage"
    HEAL = "heal"
    BUFF = "buff"
    DEBUFF = "debuff"
    CONTROL = "control"

# ============ РОУТЕР И СОСТОЯНИЯ ============

battle_router = Router()

class BattleStates(StatesGroup):
    # Игровые состояния
    in_battle = State()
    battle_action = State()
    use_item = State()
    use_skill = State()
    
    # Админ состояния
    admin_create_mob = State()
    admin_create_mob_name = State()
    admin_create_mob_stats = State()
    admin_create_mob_drops = State()
    admin_edit_mob = State()
    
    admin_create_boss = State()
    admin_create_elite = State()
    
    # Боевые выборы
    select_target = State()
    select_skill = State()

# ============ МОДЕЛИ ДАННЫХ ============

@dataclass
class PlayerSkill:
    """Структура навыка игрока"""
    id: str
    name: str
    description: str
    icon: str
    skill_type: SkillType
    damage: int = 0
    heal: int = 0
    mp_cost: int = 0
    cooldown: int = 0
    duration: int = 0
    effect_value: float = 0.0
    level_requirement: int = 1

@dataclass
class BattleEffect:
    """Эффект в бою"""
    effect_type: str
    value: float
    remaining_turns: int
    source: str
    target_id: uuid.UUID

# ============ МЕНЕДЖЕР ФОРМУЛ ============

class BattleFormulaManager:
    """Менеджер для работы с боевыми формулами"""
    
    @staticmethod
    async def get_formula(db: AsyncSession, formula_name: str) -> str:
        """Получить формулу из базы данных"""
        result = await db.execute(
            select(SystemSettings.value).where(SystemSettings.key == f"battle_formula_{formula_name}")
        )
        formula = result.scalar_one_or_none()
        if formula:
            return formula
        
        # Формулы по умолчанию
        default_formulas = {
            "player_damage": "weapon_damage * (1 + strength * 0.005) * (1 + agility * 0.001) * random(0.9, 1.1) * (2.0 if is_critical else 1)",
            "player_crit_chance": "0.05 + agility * 0.001 + level * 0.0001",
            "player_dodge_chance": "0.05 + agility * 0.0015 + level * 0.00005",
            "player_hit_chance": "0.9 + agility * 0.0005 - target_dodge_chance",
            
            "mob_damage": "mob_base_damage * (1 + mob_level * 0.01) * random(0.8, 1.2) * (1.5 if is_critical else 1)",
            "mob_crit_chance": "0.03 + mob_level * 0.0005",
            "mob_hit_chance": "0.85 + mob_level * 0.0003 - player_dodge_chance",
            
            "damage_reduction": "damage * (1 - min(0.75, armor / (armor + 50 * attacker_level)))",
            "xp_from_mob": "mob_level * 10 + (mob_rarity * 50) + (is_boss * 200) + (is_elite * 100)",
            "gold_from_mob": "mob_level * 5 + random(mob_level, mob_level * 3) + (is_boss * 500) + (is_elite * 200)",
            "flee_chance": "0.3 + agility * 0.002 - mob_level * 0.001 + (player_hp / player_max_hp * 0.1)",
            "skill_damage": "base_damage * (1 + intelligence * 0.002) * (1 + skill_level * 0.05)",
            "skill_heal": "base_heal * (1 + intelligence * 0.0015) * (1 + skill_level * 0.03)",
            "drop_chance": "base_chance * (1 + mob_rarity * 0.1) * (1 + luck * 0.001)",
            "item_effect_power": "base_power * (1 + item_quality * 0.1) * (1 + intelligence * 0.0005)"
        }
        
        return default_formulas.get(formula_name, "1")
    
    @staticmethod
    async def calculate_formula(db: AsyncSession, formula_name: str, variables: Dict[str, Any]) -> float:
        """Вычислить значение по формуле"""
        formula_str = await BattleFormulaManager.get_formula(db, formula_name)
        
        # Безопасное выполнение формулы
        formula_str = formula_str.replace("random", "__random__")
        
        # Подставляем переменные в локальное пространство имен
        local_vars = {
            **variables,
            "__random__": random.uniform,
            "min": min,
            "max": max,
            "abs": abs,
            "round": round,
            "math": math,
            "sqrt": math.sqrt,
            "log": math.log,
            "exp": math.exp,
            "pow": math.pow,
        }
        
        # Вычисляем формулу
        result = eval(formula_str, {"__builtins__": {}}, local_vars)
        return float(result)

# ============ МЕНЕДЖЕР БИТВ ============

class BattleManager:
    """Менеджер для управления боями с мобами"""
    
    def __init__(self, redis_client, db_session_factory):
        self.redis = redis_client
        self.db_session_factory = db_session_factory
        self.active_battles = {}  # {battle_id: battle_data}
        self.mob_cache = {}  # {mob_id: mob_data}
        self.skills = self._load_skills()
        self.battle_effects = {}  # {battle_id: [BattleEffect]}
    
    def _load_skills(self) -> Dict[str, PlayerSkill]:
        """Загрузить навыки игрока"""
        return {
            "fireball": PlayerSkill(
                id="fireball",
                name="Огненный шар",
                description="Наносит урон огнем",
                icon="🔥",
                skill_type=SkillType.DAMAGE,
                damage=30,
                mp_cost=15,
                cooldown=3,
                level_requirement=5
            ),
            "heal": PlayerSkill(
                id="heal",
                name="Лечение",
                description="Восстанавливает здоровье",
                icon="💚",
                skill_type=SkillType.HEAL,
                heal=40,
                mp_cost=10,
                cooldown=2,
                level_requirement=3
            ),
            "shield": PlayerSkill(
                id="shield",
                name="Магический щит",
                description="Снижает получаемый урон",
                icon="🛡️",
                skill_type=SkillType.BUFF,
                mp_cost=8,
                duration=2,
                effect_value=0.3,
                level_requirement=8
            ),
            "poison_arrow": PlayerSkill(
                id="poison_arrow",
                name="Отравленная стрела",
                description="Наносит урон с отравлением",
                icon="☠️",
                skill_type=SkillType.DEBUFF,
                damage=20,
                mp_cost=12,
                duration=3,
                effect_value=5,
                level_requirement=10
            ),
            "stun": PlayerSkill(
                id="stun",
                name="Оглушение",
                description="Оглушает противника на 1 ход",
                icon="💫",
                skill_type=SkillType.CONTROL,
                mp_cost=20,
                cooldown=5,
                duration=1,
                level_requirement=15
            )
        }
    
    async def restore_state(self):
        """Восстановить все активные битвы при запуске бота"""
        async with self.db_session_factory() as db:
            try:
                # 1. Восстановить активные битвы
                result = await db.execute(
                    select(ActiveBattle).where(
                        ActiveBattle.status == BattleStatus.ACTIVE
                    ).options(
                        selectinload(ActiveBattle.user),
                        selectinload(ActiveBattle.mob_template)
                    )
                )
                battles = result.scalars().all()
                
                restored_count = 0
                for battle in battles:
                    # Проверяем время последнего действия
                    if battle.last_action_at and (datetime.utcnow() - battle.last_action_at).seconds > 3600:
                        # Бой устарел, завершаем с поражением
                        await self._finish_expired_battle(db, battle)
                        continue
                    
                    battle_key = f"battle:{battle.id}"
                    battle_data = {
                        "id": str(battle.id),
                        "user_id": str(battle.user_id),
                        "mob_template_id": str(battle.mob_template_id),
                        "player_hp": battle.player_hp,
                        "player_max_hp": battle.player_max_hp,
                        "target_hp": battle.target_hp,
                        "target_max_hp": battle.target_max_hp,
                        "status": battle.status.value,
                        "started_at": battle.started_at.isoformat(),
                        "last_action_at": battle.last_action_at.isoformat(),
                        "battle_log": battle.battle_log or [],
                        "turn": len(battle.battle_log or []) + 1,
                        "effects": []
                    }
                    
                    # Сохраняем в Redis
                    await self.redis.setex(
                        battle_key,
                        7200,
                        json.dumps(battle_data)
                    )
                    self.active_battles[str(battle.id)] = battle_data
                    restored_count += 1
                
                # 2. Восстановить снапшоты состояний
                result = await db.execute(
                    select(StateSnapshot).where(
                        and_(
                            StateSnapshot.is_restored == False,
                            StateSnapshot.expires_at > datetime.utcnow(),
                            StateSnapshot.snapshot_type == "battle"
                        )
                    )
                )
                snapshots = result.scalars().all()
                
                for snapshot in snapshots:
                    await self.restore_battle_from_snapshot(db, snapshot)
                    restored_count += 1
                
                await db.commit()
                print(f"✅ Восстановлено {restored_count} активных битв")
                
            except Exception as e:
                print(f"❌ Ошибка при восстановлении боевого состояния: {e}")
                await db.rollback()
    
    async def restore_battle_from_snapshot(self, db: AsyncSession, snapshot: StateSnapshot):
        """Восстановить битву из снапшота"""
        snapshot_data = snapshot.snapshot_data
        
        # Создаем новую битву на основе снапшота
        battle = ActiveBattle(
            id=uuid.uuid4(),
            user_id=snapshot.user_id,
            mob_template_id=uuid.UUID(snapshot_data.get("mob_template_id")),
            status=BattleStatus.ACTIVE,
            player_hp=snapshot_data.get("player_hp", 100),
            player_max_hp=snapshot_data.get("player_max_hp", 100),
            target_hp=snapshot_data.get("target_hp", 100),
            target_max_hp=snapshot_data.get("target_max_hp", 100),
            started_at=datetime.fromisoformat(snapshot_data.get("started_at")),
            last_action_at=datetime.utcnow(),
            battle_log=snapshot_data.get("battle_log", [])
        )
        
        db.add(battle)
        
        # Помечаем снапшот как восстановленный
        snapshot.is_restored = True
        
        # Сохраняем в Redis
        battle_key = f"battle:{battle.id}"
        battle_data = {
            "id": str(battle.id),
            "user_id": str(battle.user_id),
            "mob_template_id": str(battle.mob_template_id),
            "player_hp": battle.player_hp,
            "player_max_hp": battle.player_max_hp,
            "target_hp": battle.target_hp,
            "target_max_hp": battle.target_max_hp,
            "status": battle.status.value,
            "started_at": battle.started_at.isoformat(),
            "last_action_at": battle.last_action_at.isoformat(),
            "battle_log": battle.battle_log or [],
            "turn": len(battle.battle_log or []) + 1,
            "effects": []
        }
        
        await self.redis.setex(
            battle_key,
            7200,
            json.dumps(battle_data)
        )
        self.active_battles[str(battle.id)] = battle_data
        
        print(f"✅ Восстановлена битва из снапшота {snapshot.id}")
    
    async def _finish_expired_battle(self, db: AsyncSession, battle: ActiveBattle):
        """Завершить просроченную битву"""
        battle.status = BattleStatus.PLAYER_LOST
        battle.ended_at = datetime.utcnow()
        
        user = await db.get(User, battle.user_id)
        if user:
            # Штраф за поражение по таймауту
            penalty = int(user.gold * 0.1)
            user.gold = max(0, user.gold - penalty)
            
            # Логируем
            audit_log = AuditLog(
                user_id=user.id,
                action="battle_timeout_loss",
                details={
                    "battle_id": str(battle.id),
                    "penalty": penalty,
                    "reason": "Бой просрочен"
                }
            )
            db.add(audit_log)
        
        await db.commit()
    
    # ============ ИНИЦИАЦИЯ БОЯ ============
    
    async def start_battle(self, db: AsyncSession, user_id: uuid.UUID, 
                          mob_template_id: uuid.UUID) -> Dict[str, Any]:
        """Начать бой с мобом"""
        user = await db.get(User, user_id)
        mob_template = await db.get(MobTemplate, mob_template_id)
        
        if not user or not mob_template:
            return {"error": "Игрок или моб не найден"}
        
        # Проверяем уровень
        if user.level < mob_template.level - 5:
            return {"error": f"Слишком низкий уровень. Моб: {mob_template.level}"}
        
        # Проверяем есть ли активный бой
        result = await db.execute(
            select(ActiveBattle).where(
                and_(
                    ActiveBattle.user_id == user_id,
                    ActiveBattle.status == BattleStatus.ACTIVE
                )
            )
        )
        active_battle = result.scalar_one_or_none()
        
        if active_battle:
            return {"error": "У вас уже есть активный бой"}
        
        # Рассчитываем характеристики
        player_max_hp = await self.calculate_player_max_hp(db, user)
        player_current_mp = user.current_mp
        
        mob_hp = mob_template.health
        if mob_template.is_boss:
            mob_hp *= 3
        elif mob_template.level > user.level + 10:
            mob_hp *= 2
        
        # Создаем битву
        battle = ActiveBattle(
            user_id=user_id,
            mob_template_id=mob_template_id,
            status=BattleStatus.ACTIVE,
            player_hp=player_max_hp,
            player_max_hp=player_max_hp,
            target_hp=mob_hp,
            target_max_hp=mob_hp,
            started_at=datetime.utcnow(),
            last_action_at=datetime.utcnow(),
            battle_log=[]
        )
        
        db.add(battle)
        
        # Создаем снапшот для восстановления
        snapshot = StateSnapshot(
            snapshot_type="battle",
            user_id=user_id,
            entity_id=battle.id,
            entity_type="active_battle",
            snapshot_data={
                "mob_template_id": str(mob_template_id),
                "player_hp": player_max_hp,
                "player_max_hp": player_max_hp,
                "target_hp": mob_hp,
                "target_max_hp": mob_hp,
                "started_at": battle.started_at.isoformat(),
                "battle_log": []
            },
            expires_at=datetime.utcnow() + timedelta(hours=2)
        )
        db.add(snapshot)
        
        # Логируем начало боя
        audit_log = AuditLog(
            user_id=user_id,
            action="battle_started",
            details={
                "mob_template_id": str(mob_template_id),
                "mob_name": mob_template.name,
                "mob_level": mob_template.level,
                "player_level": user.level
            }
        )
        db.add(audit_log)
        
        await db.commit()
        
        # Сохраняем в Redis
        battle_key = f"battle:{battle.id}"
        battle_data = {
            "id": str(battle.id),
            "user_id": str(user_id),
            "mob_template_id": str(mob_template_id),
            "player_hp": player_max_hp,
            "player_max_hp": player_max_hp,
            "player_mp": player_current_mp,
            "player_max_mp": user.max_mp,
            "target_hp": mob_hp,
            "target_max_hp": mob_hp,
            "status": BattleStatus.ACTIVE.value,
            "started_at": battle.started_at.isoformat(),
            "last_action_at": battle.last_action_at.isoformat(),
            "battle_type": "boss" if mob_template.is_boss else "elite" if mob_template.level > user.level + 5 else "mob",
            "battle_log": [],
            "turn": 1,
            "effects": [],
            "skill_cooldowns": {}
        }
        
        await self.redis.setex(
            battle_key,
            7200,
            json.dumps(battle_data)
        )
        self.active_battles[str(battle.id)] = battle_data
        
        return {
            "success": True,
            "battle_id": str(battle.id),
            "player_hp": player_max_hp,
            "player_max_hp": player_max_hp,
            "player_mp": player_current_mp,
            "player_max_mp": user.max_mp,
            "mob_hp": mob_hp,
            "mob_max_hp": mob_hp,
            "mob_name": mob_template.name,
            "mob_level": mob_template.level,
            "mob_icon": mob_template.icon,
            "is_boss": mob_template.is_boss
        }
    
    # ============ РАСЧЕТ ХАРАКТЕРИСТИК ============
    
    async def calculate_player_max_hp(self, db: AsyncSession, user: User) -> int:
        """Рассчитать максимальное HP игрока"""
        base_hp = 100
        constitution_bonus = user.constitution * 5
        level_bonus = user.level * 10
        
        # Бонус от экипировки
        equipment_bonus = 0
        if user.armor_id:
            armor = await db.get(Item, user.armor_id)
            if armor and armor.template:
                equipment_bonus += armor.template.health_bonus or 0
        
        max_hp = base_hp + constitution_bonus + level_bonus + equipment_bonus
        return max(100, int(max_hp))
    
    async def get_player_weapon_damage(self, db: AsyncSession, user: User) -> Dict[str, int]:
        """Получить урон от оружия игрока"""
        weapon_data = {"damage_min": 5, "damage_max": 10}
        
        if user.weapon_id:
            weapon = await db.get(Item, user.weapon_id)
            if weapon and weapon.template:
                weapon_data = {
                    "damage_min": weapon.template.damage_min or 5,
                    "damage_max": weapon.template.damage_max or 10
                }
        
        return weapon_data
    
    async def calculate_player_armor(self, db: AsyncSession, user: User) -> int:
        """Рассчитать броню игрока"""
        armor_value = 0
        
        # Броня от экипировки
        if user.armor_id:
            armor = await db.get(Item, user.armor_id)
            if armor and armor.template:
                armor_value += armor.template.defense or 0
        
        if user.helmet_id:
            helmet = await db.get(Item, user.helmet_id)
            if helmet and helmet.template:
                armor_value += helmet.template.defense or 0
        
        # Бонус от конституции
        constitution_bonus = user.constitution * 0.2
        
        return int(armor_value + constitution_bonus)
    
    async def calculate_player_damage(self, db: AsyncSession, user: User) -> Tuple[int, bool, float]:
        """Рассчитать урон игрока"""
        # Базовый урон от силы
        strength_damage = user.strength * 0.5
        
        # Урон от оружия
        weapon_data = await self.get_player_weapon_damage(db, user)
        weapon_damage = random.randint(weapon_data["damage_min"], weapon_data["damage_max"])
        
        # Общий базовый урон
        base_damage = strength_damage + weapon_damage
        
        # Рассчитываем шанс крита
        crit_chance = await BattleFormulaManager.calculate_formula(db, "player_crit_chance", {
            "agility": user.agility,
            "level": user.level
        })
        crit_chance = min(max(crit_chance, 0.01), 0.5)
        
        # Проверяем крит
        is_critical = random.random() < crit_chance
        crit_multiplier = 2.0
        
        # Рассчитываем итоговый урон
        final_damage = await BattleFormulaManager.calculate_formula(db, "player_damage", {
            "weapon_damage": base_damage,
            "strength": user.strength,
            "agility": user.agility,
            "is_critical": is_critical,
            "crit_multiplier": crit_multiplier
        })
        
        return int(final_damage), is_critical, crit_chance
    
    async def calculate_mob_damage(self, db: AsyncSession, mob_template: MobTemplate, 
                                  player_level: int) -> Tuple[int, bool, float]:
        """Рассчитать урон моба"""
        base_damage = random.randint(mob_template.damage_min, mob_template.damage_max)
        
        # Рассчитываем шанс крита моба
        crit_chance = await BattleFormulaManager.calculate_formula(db, "mob_crit_chance", {
            "mob_level": mob_template.level,
            "player_level": player_level
        })
        crit_chance = min(max(crit_chance, 0.01), 0.3)
        
        # Проверяем крит
        is_critical = random.random() < crit_chance
        
        # Рассчитываем итоговый урон
        final_damage = await BattleFormulaManager.calculate_formula(db, "mob_damage", {
            "mob_base_damage": base_damage,
            "mob_level": mob_template.level,
            "is_critical": is_critical
        })
        
        return int(final_damage), is_critical, crit_chance
    
    async def calculate_hit_chance(self, db: AsyncSession, attacker_level: int, 
                                  attacker_agility: int, defender_dodge: float) -> float:
        """Рассчитать шанс попадания"""
        base_chance = 0.9
        agility_bonus = attacker_agility * 0.0005
        level_bonus = attacker_level * 0.0001
        
        hit_chance = base_chance + agility_bonus + level_bonus - defender_dodge
        return min(max(hit_chance, 0.5), 0.95)
    
    async def calculate_player_dodge_chance(self, db: AsyncSession, user: User) -> float:
        """Рассчитать шанс уклонения игрока"""
        dodge_chance = await BattleFormulaManager.calculate_formula(db, "player_dodge_chance", {
            "agility": user.agility,
            "level": user.level
        })
        return min(max(dodge_chance, 0.01), 0.3)
    
    async def calculate_flee_chance(self, db: AsyncSession, user: User, 
                                   mob_template: MobTemplate, battle: ActiveBattle) -> float:
        """Рассчитать шанс побега"""
        flee_chance = await BattleFormulaManager.calculate_formula(db, "flee_chance", {
            "agility": user.agility,
            "mob_level": mob_template.level,
            "player_hp": battle.player_hp,
            "player_max_hp": battle.player_max_hp
        })
        return min(max(flee_chance, 0.1), 0.7)
    
    # ============ ОСНОВНЫЕ МЕТОДЫ БОЯ ============
    
    async def process_battle_action(self, battle_id: uuid.UUID, action: BattleAction, 
                                   item_id: Optional[uuid.UUID] = None, 
                                   skill_id: Optional[str] = None) -> Dict[str, Any]:
        """Обработать действие в битве"""
        async with self.db_session_factory() as db:
            battle = await db.get(ActiveBattle, battle_id)
            
            if not battle:
                return {"error": "Битва не найдена"}
            
            if battle.status != BattleStatus.ACTIVE:
                return {"error": "Битва уже завершена"}
            
            user = await db.get(User, battle.user_id)
            mob_template = await db.get(MobTemplate, battle.mob_template_id)
            
            if not user or not mob_template:
                return {"error": "Игрок или моб не найден"}
            
            # Получаем текущие данные боя из Redis
            battle_key = f"battle:{battle_id}"
            battle_data_json = await self.redis.get(battle_key)
            battle_data = json.loads(battle_data_json) if battle_data_json else {}
            
            # Обновляем кд навыков
            current_turn = battle_data.get("turn", 1)
            skill_cooldowns = battle_data.get("skill_cooldowns", {})
            
            for skill_id in list(skill_cooldowns.keys()):
                if skill_cooldowns[skill_id] <= current_turn:
                    del skill_cooldowns[skill_id]
            
            battle_log_entry = {
                "turn": current_turn,
                "timestamp": datetime.utcnow().isoformat(),
                "action": action.value,
                "player_hp_before": battle.player_hp,
                "mob_hp_before": battle.target_hp,
            }
            
            result = {"success": True, "turn": current_turn}
            
            # Обрабатываем действие игрока
            if action == BattleAction.ATTACK:
                player_result = await self._process_player_attack(db, user, mob_template, battle)
                battle_log_entry.update(player_result["log"])
                result.update(player_result)
                
                # Применяем урон мобу
                if player_result.get("hit", False):
                    damage = player_result["damage"]
                    battle.target_hp = max(0, battle.target_hp - damage)
                    
                    # Проверяем смерть моба
                    if battle.target_hp <= 0:
                        victory_result = await self._finish_battle(db, battle, user, mob_template, True)
                        result["battle_finished"] = True
                        result["victory"] = True
                        result.update(victory_result)
                
            elif action == BattleAction.DEFEND:
                defense_bonus = 0.3
                battle_log_entry.update({
                    "action": "defend",
                    "defense_bonus": defense_bonus,
                    "description": f"{user.username or 'Игрок'} занимает защитную стойку"
                })
                
                result.update({
                    "action": "defend",
                    "defense_bonus": defense_bonus,
                    "player_name": user.username or "Игрок"
                })
                
                # Добавляем эффект защиты
                await self._add_battle_effect(db, battle.id, user.id, "defense", defense_bonus, 2)
                
            elif action == BattleAction.DODGE:
                dodge_bonus = 0.25
                battle_log_entry.update({
                    "action": "dodge",
                    "dodge_bonus": dodge_bonus,
                    "description": f"{user.username or 'Игрок'} готовится уворачиваться"
                })
                
                result.update({
                    "action": "dodge",
                    "dodge_bonus": dodge_bonus,
                    "player_name": user.username or "Игрок"
                })
                
                # Добавляем эффект уклонения
                await self._add_battle_effect(db, battle.id, user.id, "dodge", dodge_bonus, 2)
                
            elif action == BattleAction.FLEE:
                flee_chance = await self.calculate_flee_chance(db, user, mob_template, battle)
                flee_success = random.random() < flee_chance
                
                if flee_success:
                    battle.status = BattleStatus.FLED
                    battle.ended_at = datetime.utcnow()
                    
                    battle_log_entry.update({
                        "action": "flee",
                        "success": True,
                        "flee_chance": flee_chance,
                        "description": f"{user.username or 'Игрок'} успешно сбежал"
                    })
                    
                    result.update({
                        "action": "flee",
                        "success": True,
                        "battle_finished": True,
                        "player_name": user.username or "Игрок"
                    })
                    
                    # Логируем побег
                    audit_log = AuditLog(
                        user_id=user.id,
                        action="battle_fled",
                        details={
                            "battle_id": str(battle.id),
                            "mob_name": mob_template.name,
                            "flee_chance": flee_chance
                        }
                    )
                    db.add(audit_log)
                else:
                    battle_log_entry.update({
                        "action": "flee",
                        "success": False,
                        "flee_chance": flee_chance,
                        "description": f"{user.username or 'Игрок'} не смог сбежать"
                    })
                    
                    result.update({
                        "action": "flee",
                        "success": False,
                        "player_name": user.username or "Игрок"
                    })
            
            elif action == BattleAction.USE_ITEM and item_id:
                use_item_result = await self._process_use_item(db, battle, user, item_id)
                battle_log_entry.update(use_item_result.get("log", {}))
                result.update(use_item_result)
            
            elif action == BattleAction.USE_SKILL and skill_id:
                if skill_id in skill_cooldowns:
                    result["error"] = f"Навык {self.skills[skill_id].name} перезаряжается"
                    return result
                
                use_skill_result = await self._process_use_skill(db, battle, user, skill_id, mob_template)
                battle_log_entry.update(use_skill_result.get("log", {}))
                result.update(use_skill_result)
                
                if use_skill_result.get("success", False):
                    # Устанавливаем кд навыка
                    skill_cooldowns[skill_id] = current_turn + self.skills[skill_id].cooldown
            
            # Если игрок не сбежал и моб еще жив - ход моба
            if battle.status == BattleStatus.ACTIVE and battle.target_hp > 0:
                mob_result = await self._process_mob_turn(db, battle, user, mob_template)
                
                if mob_result:
                    # Применяем урон игроку
                    if mob_result.get("hit", False):
                        damage = mob_result["damage"]
                        
                        # Применяем защиту
                        defense_bonus = await self._get_battle_effect(db, battle.id, user.id, "defense")
                        if defense_bonus > 0:
                            damage = int(damage * (1 - defense_bonus))
                            mob_result["log"]["defense_reduced"] = defense_bonus
                        
                        battle.player_hp = max(0, battle.player_hp - damage)
                        
                        # Проверяем смерть игрока
                        if battle.player_hp <= 0:
                            defeat_result = await self._finish_battle(db, battle, user, mob_template, False)
                            result["battle_finished"] = True
                            result["victory"] = False
                            result.update(defeat_result)
                    
                    # Добавляем лог хода моба
                    mob_log = {
                        "turn": battle_log_entry["turn"],
                        "timestamp": datetime.utcnow().isoformat(),
                        "action": "mob_attack",
                        "player_hp_before": battle.player_hp + damage if mob_result.get("hit", False) else battle.player_hp,
                        "mob_hp_before": battle.target_hp,
                    }
                    mob_log.update(mob_result.get("log", {}))
                    
                    current_log = battle.battle_log or []
                    current_log.append(mob_log)
                    battle.battle_log = current_log
                    
                    result["mob_turn"] = mob_result
            
            # Обновляем лог битвы
            current_log = battle.battle_log or []
            current_log.append(battle_log_entry)
            battle.battle_log = current_log
            battle.last_action_at = datetime.utcnow()
            
            # Обновляем кд навыков
            battle_data["skill_cooldowns"] = skill_cooldowns
            battle_data["turn"] = current_turn + 1
            
            await db.commit()
            
            # Обновляем в Redis
            await self._update_battle_in_redis(battle, battle_data)
            
            # Добавляем результат боя
            result.update({
                "battle_id": str(battle.id),
                "player_hp": battle.player_hp,
                "player_max_hp": battle.player_max_hp,
                "player_mp": user.current_mp,
                "player_max_mp": user.max_mp,
                "mob_hp": battle.target_hp,
                "mob_max_hp": battle.target_max_hp,
                "turn": current_turn,
                "battle_finished": battle.status != BattleStatus.ACTIVE
            })
            
            return result
    
    async def _process_player_attack(self, db: AsyncSession, user: User, 
                                    mob_template: MobTemplate, battle: ActiveBattle) -> Dict[str, Any]:
        """Обработать атаку игрока"""
        # Рассчитываем урон
        damage, is_critical, crit_chance = await self.calculate_player_damage(db, user)
        
        # Рассчитываем шанс попадания
        mob_dodge_chance = mob_template.dodge_chance
        hit_chance = await self.calculate_hit_chance(db, user.level, user.agility, mob_dodge_chance)
        hit_success = random.random() < hit_chance
        
        log_data = {
            "action": "attack",
            "hit": hit_success,
            "is_critical": is_critical if hit_success else False,
            "damage": damage if hit_success else 0,
            "hit_chance": hit_chance,
            "crit_chance": crit_chance,
            "description": ""
        }
        
        if not hit_success:
            log_data["description"] = f"{user.username or 'Игрок'} промахнулся!"
        elif is_critical:
            log_data["description"] = f"⚡ КРИТИЧЕСКИЙ УДАР! {user.username or 'Игрок'} наносит {damage} урона!"
        else:
            log_data["description"] = f"{user.username or 'Игрок'} наносит {damage} урона."
        
        return {
            "log": log_data,
            "hit": hit_success,
            "damage": damage if hit_success else 0,
            "critical": is_critical if hit_success else False
        }
    
    async def _process_mob_turn(self, db: AsyncSession, battle: ActiveBattle, 
                               user: User, mob_template: MobTemplate) -> Dict[str, Any]:
        """Обработать ход моба"""
        # Рассчитываем урон моба
        damage, is_critical, crit_chance = await self.calculate_mob_damage(db, mob_template, user.level)
        
        # Рассчитываем шанс попадания
        player_dodge_chance = await self.calculate_player_dodge_chance(db, user)
        dodge_bonus = await self._get_battle_effect(db, battle.id, user.id, "dodge")
        total_dodge_chance = min(player_dodge_chance + dodge_bonus, 0.5)
        
        hit_chance = await BattleFormulaManager.calculate_formula(db, "mob_hit_chance", {
            "mob_level": mob_template.level,
            "player_dodge_chance": total_dodge_chance
        })
        
        hit_success = random.random() < hit_chance
        
        log_data = {
            "action": "mob_attack",
            "attacker": mob_template.name,
            "hit": hit_success,
            "is_critical": is_critical if hit_success else False,
            "damage": damage if hit_success else 0,
            "hit_chance": hit_chance,
            "crit_chance": crit_chance,
            "description": ""
        }
        
        if not hit_success:
            log_data["description"] = f"{mob_template.name} промахивается!"
        elif is_critical:
            log_data["description"] = f"⚡ {mob_template.name} наносит критический удар {damage} урона!"
        else:
            log_data["description"] = f"{mob_template.name} наносит {damage} урона."
        
        return {
            "log": log_data,
            "hit": hit_success,
            "damage": damage if hit_success else 0,
            "critical": is_critical if hit_success else False
        }
    
    async def _process_use_item(self, db: AsyncSession, battle: ActiveBattle, 
                               user: User, item_id: uuid.UUID) -> Dict[str, Any]:
        """Обработать использование предмета в бою"""
        item = await db.get(Item, item_id)
        if not item or item.owner_id != user.id:
            return {"error": "Предмет не найден"}
        
        item_template = await db.get(ItemTemplate, item.template_id)
        if not item_template or not item_template.is_consumable:
            return {"error": "Этот предмет нельзя использовать"}
        
        log_data = {
            "action": "use_item",
            "item_name": item_template.name,
            "item_icon": item_template.icon,
            "description": f"{user.username or 'Игрок'} использует {item_template.name}"
        }
        
        result = {"log": log_data, "success": True}
        
        # Обрабатываем эффекты зелья
        if item_template.item_type == ItemType.POTION and item_template.potion_effect:
            effects = item_template.potion_effect
            
            if effects.get("type") == "heal":
                heal_amount = effects.get("value", 0)
                max_heal = battle.player_max_hp - battle.player_hp
                actual_heal = min(heal_amount, max_heal)
                
                battle.player_hp += actual_heal
                log_data["heal"] = actual_heal
                log_data["description"] += f" и восстанавливает {actual_heal} HP"
                
                result["heal"] = actual_heal
            
            elif effects.get("type") == "mana":
                mana_amount = effects.get("value", 0)
                max_mana = user.max_mp - user.current_mp
                actual_mana = min(mana_amount, max_mana)
                
                user.current_mp += actual_mana
                log_data["mana"] = actual_mana
                log_data["description"] += f" и восстанавливает {actual_mana} MP"
                
                result["mana"] = actual_mana
            
            elif effects.get("type") == "buff":
                buff_type = effects.get("buff_type", "")
                buff_value = effects.get("value", 0)
                duration = effects.get("duration", 1)
                
                await self._add_battle_effect(db, battle.id, user.id, buff_type, buff_value, duration)
                log_data["buff"] = f"{buff_type}: +{buff_value*100}%"
                log_data["description"] += f" и получает {buff_type} на {duration} хода"
                
                result["buff"] = {"type": buff_type, "value": buff_value, "duration": duration}
        
        # Уменьшаем количество предметов
        if item.quantity > 1:
            item.quantity -= 1
        else:
            await db.delete(item)
        
        return result
    
    async def _process_use_skill(self, db: AsyncSession, battle: ActiveBattle, 
                                user: User, skill_id: str, mob_template: MobTemplate) -> Dict[str, Any]:
        """Обработать использование навыка"""
        skill = self.skills.get(skill_id)
        if not skill:
            return {"error": "Навык не найден"}
        
        if user.level < skill.level_requirement:
            return {"error": f"Требуется уровень {skill.level_requirement}"}
        
        if user.current_mp < skill.mp_cost:
            return {"error": f"Недостаточно маны. Нужно: {skill.mp_cost}"}
        
        # Списываем ману
        user.current_mp -= skill.mp_cost
        
        log_data = {
            "action": "use_skill",
            "skill_name": skill.name,
            "skill_icon": skill.icon,
            "mp_cost": skill.mp_cost,
            "description": f"{user.username or 'Игрок'} использует {skill.name}"
        }
        
        result = {"log": log_data, "success": True}
        
        # Применяем эффекты навыка
        if skill.skill_type == SkillType.DAMAGE:
            base_damage = skill.damage
            skill_damage = await BattleFormulaManager.calculate_formula(db, "skill_damage", {
                "base_damage": base_damage,
                "intelligence": user.intelligence,
                "skill_level": user.level // 5
            })
            
            damage = int(skill_damage)
            battle.target_hp = max(0, battle.target_hp - damage)
            log_data["damage"] = damage
            log_data["description"] += f" и наносит {damage} урона"
            result["damage"] = damage
            
            # Проверяем смерть моба
            if battle.target_hp <= 0:
                victory_result = await self._finish_battle(db, battle, user, mob_template, True)
                result["battle_finished"] = True
                result["victory"] = True
                result.update(victory_result)
        
        elif skill.skill_type == SkillType.HEAL:
            base_heal = skill.heal
            skill_heal = await BattleFormulaManager.calculate_formula(db, "skill_heal", {
                "base_heal": base_heal,
                "intelligence": user.intelligence,
                "skill_level": user.level // 5
            })
            
            heal_amount = int(skill_heal)
            max_heal = battle.player_max_hp - battle.player_hp
            actual_heal = min(heal_amount, max_heal)
            
            battle.player_hp += actual_heal
            log_data["heal"] = actual_heal
            log_data["description"] += f" и восстанавливает {actual_heal} HP"
            result["heal"] = actual_heal
        
        elif skill.skill_type == SkillType.BUFF:
            await self._add_battle_effect(db, battle.id, user.id, skill.id, skill.effect_value, skill.duration)
            log_data["buff"] = f"{skill.name}: +{skill.effect_value*100}%"
            log_data["description"] += f" и получает {skill.name} на {skill.duration} хода"
            result["buff"] = {"type": skill.id, "value": skill.effect_value, "duration": skill.duration}
        
        elif skill.skill_type == SkillType.DEBUFF:
            await self._add_battle_effect(db, battle.id, mob_template.id, "poison", skill.effect_value, skill.duration)
            
            # Также наносим начальный урон
            damage = skill.damage
            battle.target_hp = max(0, battle.target_hp - damage)
            
            log_data["damage"] = damage
            log_data["debuff"] = f"отравление: {skill.effect_value} урона в ход"
            log_data["description"] += f", наносит {damage} урона и отравляет"
            result["damage"] = damage
            result["debuff"] = {"type": "poison", "value": skill.effect_value, "duration": skill.duration}
        
        elif skill.skill_type == SkillType.CONTROL:
            await self._add_battle_effect(db, battle.id, mob_template.id, "stun", 1.0, skill.duration)
            log_data["control"] = "оглушение"
            log_data["description"] += f" и оглушает {mob_template.name} на {skill.duration} ход"
            result["control"] = {"type": "stun", "duration": skill.duration}
        
        return result
    
    async def _add_battle_effect(self, db: AsyncSession, battle_id: uuid.UUID, 
                                target_id: uuid.UUID, effect_type: str, value: float, duration: int):
        """Добавить эффект в битву"""
        if battle_id not in self.battle_effects:
            self.battle_effects[battle_id] = []
        
        effect = BattleEffect(
            effect_type=effect_type,
            value=value,
            remaining_turns=duration,
            source="player",
            target_id=target_id
        )
        
        self.battle_effects[battle_id].append(effect)
        
        # Сохраняем в Redis
        battle_key = f"battle:{battle_id}"
        battle_data_json = await self.redis.get(battle_key)
        if battle_data_json:
            battle_data = json.loads(battle_data_json)
            effects = battle_data.get("effects", [])
            effects.append({
                "effect_type": effect_type,
                "value": value,
                "remaining_turns": duration,
                "target_id": str(target_id)
            })
            battle_data["effects"] = effects
            await self.redis.setex(battle_key, 7200, json.dumps(battle_data))
    
    async def _get_battle_effect(self, db: AsyncSession, battle_id: uuid.UUID, 
                                target_id: uuid.UUID, effect_type: str) -> float:
        """Получить значение эффекта в битве"""
        if battle_id not in self.battle_effects:
            return 0.0
        
        total_value = 0.0
        for effect in self.battle_effects[battle_id]:
            if effect.target_id == target_id and effect.effect_type == effect_type and effect.remaining_turns > 0:
                total_value += effect.value
        
        return total_value
    
    async def _update_battle_effects(self, db: AsyncSession, battle_id: uuid.UUID):
        """Обновить эффекты в битве (уменьшить длительность)"""
        if battle_id not in self.battle_effects:
            return
        
        # Уменьшаем длительность эффектов
        remaining_effects = []
        for effect in self.battle_effects[battle_id]:
            effect.remaining_turns -= 1
            if effect.remaining_turns > 0:
                remaining_effects.append(effect)
        
        self.battle_effects[battle_id] = remaining_effects
        
        # Обновляем в Redis
        battle_key = f"battle:{battle_id}"
        battle_data_json = await self.redis.get(battle_key)
        if battle_data_json:
            battle_data = json.loads(battle_data_json)
            effects = battle_data.get("effects", [])
            updated_effects = []
            for effect in effects:
                if effect["remaining_turns"] > 1:
                    effect["remaining_turns"] -= 1
                    updated_effects.append(effect)
            battle_data["effects"] = updated_effects
            await self.redis.setex(battle_key, 7200, json.dumps(battle_data))
    
    async def _finish_battle(self, db: AsyncSession, battle: ActiveBattle, 
                           user: User, mob_template: MobTemplate, victory: bool) -> Dict[str, Any]:
        """Завершить битву"""
        if victory:
            battle.status = BattleStatus.PLAYER_WON
            rewards = await self._calculate_battle_rewards(db, user, mob_template, battle)
        else:
            battle.status = BattleStatus.PLAYER_LOST
            rewards = await self._calculate_defeat_penalty(db, user, mob_template)
        
        battle.ended_at = datetime.utcnow()
        
        # Обновляем статистику игрока
        await self._update_player_stats(db, user.id, mob_template, victory)
        
        # Удаляем из активных битв
        battle_key = f"battle:{battle.id}"
        await self.redis.delete(battle_key)
        if str(battle.id) in self.active_battles:
            del self.active_battles[str(battle.id)]
        
        if battle.id in self.battle_effects:
            del self.battle_effects[battle.id]
        
        return {
            "victory": victory,
            "rewards": rewards,
            "player_hp": battle.player_hp,
            "mob_hp": battle.target_hp
        }
    
    async def _calculate_battle_rewards(self, db: AsyncSession, user: User, 
                                       mob_template: MobTemplate, battle: ActiveBattle) -> Dict[str, Any]:
        """Рассчитать награды за победу"""
        rewards = {
            "xp": 0,
            "gold": 0,
            "items": []
        }
        
        # Опыт
        xp = await BattleFormulaManager.calculate_formula(db, "xp_from_mob", {
            "mob_level": mob_template.level,
            "mob_rarity": 1 if mob_template.is_boss else 0.5 if mob_template.level > user.level + 5 else 0.2,
            "is_boss": mob_template.is_boss,
            "is_elite": mob_template.level > user.level + 5
        })
        
        rewards["xp"] = int(xp)
        user.experience += int(xp)
        
        # Золото
        gold = await BattleFormulaManager.calculate_formula(db, "gold_from_mob", {
            "mob_level": mob_template.level,
            "is_boss": mob_template.is_boss,
            "is_elite": mob_template.level > user.level + 5
        })
        
        rewards["gold"] = int(gold)
        user.gold += int(gold)
        user.total_gold_earned += int(gold)
        
        # Дроп предметов
        result = await db.execute(
            select(MobDrop).where(MobDrop.mob_template_id == mob_template.id).options(
                selectinload(MobDrop.item_template)
            )
        )
        drops = result.scalars().all()
        
        for drop in drops:
            drop_chance = await BattleFormulaManager.calculate_formula(db, "drop_chance", {
                "base_chance": drop.drop_chance,
                "mob_rarity": 1 if mob_template.is_boss else 0.5,
                "luck": 0  # TODO: добавить удачу игрока
            })
            
            if random.random() < drop_chance:
                quantity = random.randint(drop.min_quantity, drop.max_quantity)
                
                # Добавляем предмет в инвентарь
                await self._add_item_to_inventory(db, user.id, drop.item_template, quantity)
                
                rewards["items"].append({
                    "name": drop.item_template.name,
                    "icon": drop.item_template.icon,
                    "quantity": quantity,
                    "rarity": drop.item_template.rarity.value
                })
        
        # Проверяем повышение уровня
        await self._check_level_up(db, user)
        
        # Логируем победу
        audit_log = AuditLog(
            user_id=user.id,
            action="battle_victory",
            details={
                "mob_template_id": str(mob_template.id),
                "mob_name": mob_template.name,
                "xp": rewards["xp"],
                "gold": rewards["gold"],
                "items": rewards["items"]
            }
        )
        db.add(audit_log)
        
        return rewards
    
    async def _calculate_defeat_penalty(self, db: AsyncSession, user: User, 
                                       mob_template: MobTemplate) -> Dict[str, Any]:
        """Рассчитать штраф за поражение"""
        penalty = {
            "gold_lost": 0,
            "xp_lost": 0
        }
        
        # Потеря золота (10%)
        gold_lost = int(user.gold * 0.1)
        user.gold = max(0, user.gold - gold_lost)
        penalty["gold_lost"] = gold_lost
        
        # Потеря опыта (5%)
        xp_lost = int(user.experience * 0.05)
        user.experience = max(0, user.experience - xp_lost)
        penalty["xp_lost"] = xp_lost
        
        # Логируем поражение
        audit_log = AuditLog(
            user_id=user.id,
            action="battle_defeat",
            details={
                "mob_template_id": str(mob_template.id),
                "mob_name": mob_template.name,
                "gold_lost": gold_lost,
                "xp_lost": xp_lost
            }
        )
        db.add(audit_log)
        
        return penalty
    
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
    
    async def _check_level_up(self, db: AsyncSession, user: User):
        """Проверить повышение уровня"""
        xp_needed = user.level * 100 * (1 + user.level * 0.1)
        
        while user.experience >= xp_needed:
            user.level += 1
            user.experience -= int(xp_needed)
            user.free_points += 5  # 5 очков характеристик за уровень
            
            # Увеличиваем характеристики
            user.strength += 1
            user.agility += 1
            user.intelligence += 1
            user.constitution += 1
            
            # Восстанавливаем здоровье и ману
            user.current_hp = user.max_hp = await self.calculate_player_max_hp(db, user)
            user.current_mp = user.max_mp = user.max_mp + 10
            
            # Пересчитываем XP для следующего уровня
            xp_needed = user.level * 100 * (1 + user.level * 0.1)
            
            # Логируем повышение уровня
            audit_log = AuditLog(
                user_id=user.id,
                action="level_up",
                details={
                    "new_level": user.level,
                    "free_points": user.free_points
                }
            )
            db.add(audit_log)
    
    async def _update_player_stats(self, db: AsyncSession, user_id: uuid.UUID, 
                                 mob_template: MobTemplate, victory: bool):
        """Обновить статистику игрока"""
        player_stat = await db.execute(
            select(PlayerStat).where(PlayerStat.user_id == user_id)
        )
        player_stat = player_stat.scalar_one_or_none()
        
        if not player_stat:
            player_stat = PlayerStat(user_id=user_id)
            db.add(player_stat)
            await db.flush()
        
        if victory:
            player_stat.daily_mobs_killed += 1
        
        player_stat.last_battle_time = datetime.utcnow()
    
    async def _update_battle_in_redis(self, battle: ActiveBattle, battle_data: Dict[str, Any] = None):
        """Обновить данные боя в Redis"""
        battle_key = f"battle:{battle.id}"
        
        if not battle_data:
            battle_data_json = await self.redis.get(battle_key)
            battle_data = json.loads(battle_data_json) if battle_data_json else {}
        
        battle_data.update({
            "id": str(battle.id),
            "user_id": str(battle.user_id),
            "mob_template_id": str(battle.mob_template_id),
            "player_hp": battle.player_hp,
            "player_max_hp": battle.player_max_hp,
            "target_hp": battle.target_hp,
            "target_max_hp": battle.target_max_hp,
            "status": battle.status.value,
            "last_action_at": battle.last_action_at.isoformat(),
            "battle_log": battle.battle_log or [],
            "turn": battle_data.get("turn", 1) + 1
        })
        
        await self.redis.setex(
            battle_key,
            7200,
            json.dumps(battle_data)
        )
        self.active_battles[str(battle.id)] = battle_data
    
    # ============ УТИЛИТЫ ============
    
    async def get_active_battle(self, user_id: uuid.UUID) -> Optional[Dict[str, Any]]:
        """Получить активный бой игрока"""
        async with self.db_session_factory() as db:
            result = await db.execute(
                select(ActiveBattle).where(
                    and_(
                        ActiveBattle.user_id == user_id,
                        ActiveBattle.status == BattleStatus.ACTIVE
                    )
                ).options(
                    selectinload(ActiveBattle.mob_template)
                )
            )
            battle = result.scalar_one_or_none()
            
            if not battle:
                return None
            
            battle_key = f"battle:{battle.id}"
            battle_data_json = await self.redis.get(battle_key)
            battle_data = json.loads(battle_data_json) if battle_data_json else {}
            
            return {
                "battle_id": str(battle.id),
                "player_hp": battle.player_hp,
                "player_max_hp": battle.player_max_hp,
                "mob_hp": battle.target_hp,
                "mob_max_hp": battle.target_max_hp,
                "mob_name": battle.mob_template.name,
                "mob_icon": battle.mob_template.icon,
                "mob_level": battle.mob_template.level,
                "turn": battle_data.get("turn", 1),
                "effects": battle_data.get("effects", []),
                "skill_cooldowns": battle_data.get("skill_cooldowns", {})
            }
    
    async def get_available_skills(self, user: User, battle_id: uuid.UUID) -> List[Dict[str, Any]]:
        """Получить доступные навыки для игрока"""
        battle_key = f"battle:{battle_id}"
        battle_data_json = await self.redis.get(battle_key)
        battle_data = json.loads(battle_data_json) if battle_data_json else {}
        skill_cooldowns = battle_data.get("skill_cooldowns", {})
        current_turn = battle_data.get("turn", 1)
        
        available_skills = []
        for skill_id, skill in self.skills.items():
            if user.level >= skill.level_requirement:
                on_cooldown = skill_id in skill_cooldowns and skill_cooldowns[skill_id] > current_turn
                cooldown_remaining = max(0, skill_cooldowns.get(skill_id, 0) - current_turn) if on_cooldown else 0
                
                available_skills.append({
                    "id": skill_id,
                    "name": skill.name,
                    "icon": skill.icon,
                    "description": skill.description,
                    "type": skill.skill_type.value,
                    "damage": skill.damage,
                    "heal": skill.heal,
                    "mp_cost": skill.mp_cost,
                    "cooldown": skill.cooldown,
                    "on_cooldown": on_cooldown,
                    "cooldown_remaining": cooldown_remaining,
                    "available": not on_cooldown and user.current_mp >= skill.mp_cost
                })
        
        return available_skills
    
    async def get_battle_items(self, db: AsyncSession, user_id: uuid.UUID) -> List[Dict[str, Any]]:
        """Получить предметы для использования в бою"""
        result = await db.execute(
            select(Item).where(
                and_(
                    Item.owner_id == user_id,
                    Item.template.has(ItemTemplate.is_consumable == True)
                )
            ).options(selectinload(Item.template))
        )
        items = result.scalars().all()
        
        battle_items = []
        for item in items:
            if item.template.item_type == ItemType.POTION and item.template.potion_effect:
                effect = item.template.potion_effect
                effect_desc = ""
                
                if effect.get("type") == "heal":
                    effect_desc = f"Восстанавливает {effect.get('value', 0)} HP"
                elif effect.get("type") == "mana":
                    effect_desc = f"Восстанавливает {effect.get('value', 0)} MP"
                elif effect.get("type") == "buff":
                    effect_desc = f"{effect.get('buff_type', '')} +{effect.get('value', 0)*100}%"
                
                battle_items.append({
                    "id": str(item.id),
                    "name": item.template.name,
                    "icon": item.template.icon,
                    "description": effect_desc,
                    "quantity": item.quantity,
                    "effect": effect
                })
        
        return battle_items

# ============ ХЭНДЛЕРЫ ДЛЯ АДМИН-ПАНЕЛИ ============

@battle_router.callback_query(F.data.startswith("battle_admin_"))
async def handle_admin_battle(callback: CallbackQuery, state: FSMContext):
    """Обработчик админ-панели битв"""
    action = callback.data.replace("battle_admin_", "")
    
    if action == "menu":
        await show_admin_battle_menu(callback)
    
    elif action == "create_mob":
        await state.set_state(BattleStates.admin_create_mob_name)
        await callback.message.edit_text(
            "🧌 СОЗДАНИЕ НОВОГО МОБА\n\n"
            "Введите название моба:",
            reply_markup=create_cancel_keyboard()
        )
    
    elif action == "create_boss":
        await state.set_state(BattleStates.admin_create_boss)
        await callback.message.edit_text(
            "👑 СОЗДАНИЕ БОССА\n\n"
            "Введите данные в формате:\n"
            "Название:Тип:Уровень:Здоровье:Урон мин-макс\n\n"
            "Пример:\n"
            "Дракон горы:dragon:50:5000:100-150",
            reply_markup=create_cancel_keyboard()
        )
    
    elif action == "list_mobs":
        await show_mobs_list(callback)
    
    elif action == "battle_stats":
        await show_battle_statistics(callback)

async def show_admin_battle_menu(callback: CallbackQuery):
    """Показать меню админ-панели битв"""
    from database import get_db_session
    
    async with get_db_session() as db:
        mobs_count = await db.execute(select(func.count(MobTemplate.id)))
        mobs_count = mobs_count.scalar()
        
        bosses_count = await db.execute(
            select(func.count(MobTemplate.id)).where(MobTemplate.is_boss == True)
        )
        bosses_count = bosses_count.scalar()
        
        active_battles = await db.execute(
            select(func.count(ActiveBattle.id)).where(ActiveBattle.status == BattleStatus.ACTIVE)
        )
        active_battles = active_battles.scalar()
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧌 Создать моба", callback_data="battle_admin_create_mob")],
        [InlineKeyboardButton(text="👑 Создать босса", callback_data="battle_admin_create_boss")],
        [InlineKeyboardButton(text="📋 Список мобов", callback_data="battle_admin_list_mobs")],
        [InlineKeyboardButton(text="⚔️ Создать элитного моба", callback_data="battle_admin_create_elite")],
        [InlineKeyboardButton(text="📊 Статистика битв", callback_data="battle_admin_battle_stats")],
        [InlineKeyboardButton(text="⚙️ Настройки формул", callback_data="battle_admin_formulas")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")]
    ])
    
    await callback.message.edit_text(
        f"⚔️ АДМИН-ПАНЕЛЬ БИТВ\n\n"
        f"🧌 Всего мобов: {mobs_count}\n"
        f"👑 Боссов: {bosses_count}\n"
        f"⚔️ Активных битв: {active_battles}\n\n"
        "Выберите действие:",
        reply_markup=keyboard
    )

async def show_mobs_list(callback: CallbackQuery):
    """Показать список мобов"""
    from database import get_db_session
    
    async with get_db_session() as db:
        mobs = await db.execute(
            select(MobTemplate).order_by(MobTemplate.level)
        )
        mobs = mobs.scalars().all()
        
        text = "🧌 СПИСОК МОБОВ\n\n"
        
        keyboard_buttons = []
        for mob in mobs:
            boss_icon = "👑" if mob.is_boss else ""
            text += f"{boss_icon}{mob.icon} {mob.name}\n"
            text += f"  Уровень: {mob.level} | HP: {mob.health}\n"
            text += f"  Урон: {mob.damage_min}-{mob.damage_max}\n\n"
            
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"✏️ {mob.name[:15]}...",
                    callback_data=f"mob_edit_{mob.id}"
                ),
                InlineKeyboardButton(
                    text="🗑️",
                    callback_data=f"mob_delete_{mob.id}"
                )
            ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="battle_admin_menu")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard)

async def show_battle_statistics(callback: CallbackQuery):
    """Показать статистику битв"""
    from database import get_db_session
    
    async with get_db_session() as db:
        # Статистика по битвам
        total_battles = await db.execute(select(func.count(ActiveBattle.id)))
        total_battles = total_battles.scalar()
        
        victories = await db.execute(
            select(func.count(ActiveBattle.id)).where(ActiveBattle.status == BattleStatus.PLAYER_WON)
        )
        victories = victories.scalar()
        
        defeats = await db.execute(
            select(func.count(ActiveBattle.id)).where(ActiveBattle.status == BattleStatus.PLAYER_LOST)
        )
        defeats = defeats.scalar()
        
        fled = await db.execute(
            select(func.count(ActiveBattle.id)).where(ActiveBattle.status == BattleStatus.FLED)
        )
        fled = fled.scalar()
        
        victory_rate = (victories / total_battles * 100) if total_battles > 0 else 0
        
        # Самый популярный моб
        popular_mob = await db.execute(
            select(MobTemplate.name, func.count(ActiveBattle.id).label('battles'))
            .join(ActiveBattle, ActiveBattle.mob_template_id == MobTemplate.id)
            .group_by(MobTemplate.id)
            .order_by(desc('battles'))
            .limit(1)
        )
        popular_mob = popular_mob.first()
        
        text = "📊 СТАТИСТИКА БИТВ\n\n"
        text += f"Всего битв: {total_battles}\n"
        text += f"Побед: {victories}\n"
        text += f"Поражений: {defeats}\n"
        text += f"Побегов: {fled}\n"
        text += f"Процент побед: {victory_rate:.1f}%\n\n"
        
        if popular_mob:
            text += f"Самый популярный моб:\n"
            text += f"• {popular_mob[0]} - {popular_mob[1]} битв\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="battle_admin_battle_stats")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="battle_admin_menu")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard)

# ============ ХЭНДЛЕРЫ ДЛЯ ИГРОКОВ ============

@battle_router.callback_query(F.data.startswith("battle_"))
async def handle_player_battle(callback: CallbackQuery, state: FSMContext):
    """Обработчик битв для игроков"""
    action = callback.data.replace("battle_", "")
    
    if action == "menu":
        await show_battle_menu(callback)
    
    elif action == "start":
        await state.set_state(BattleStates.select_target)
        await show_available_mobs(callback)
    
    elif action == "active":
        await show_active_battle(callback)
    
    elif action == "skills":
        await show_player_skills(callback)
    
    elif action.startswith("start_"):
        mob_id = uuid.UUID(action.replace("start_", ""))
        await start_battle_handler(callback, mob_id)
    
    elif action.startswith("action_"):
        battle_id = uuid.UUID(action.split("_")[1])
        action_type = action.split("_")[2]
        await process_battle_action_handler(callback, battle_id, action_type)
    
    elif action.startswith("use_item_"):
        battle_id = uuid.UUID(action.split("_")[2])
        item_id = uuid.UUID(action.split("_")[3])
        await use_item_in_battle_handler(callback, battle_id, item_id)
    
    elif action.startswith("use_skill_"):
        battle_id = uuid.UUID(action.split("_")[2])
        skill_id = action.split("_")[3]
        await use_skill_in_battle_handler(callback, battle_id, skill_id)

async def show_battle_menu(callback: CallbackQuery):
    """Показать меню битв"""
    from database import get_db_session
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        # Проверяем активный бой
        from main import battle_manager
        active_battle = await battle_manager.get_active_battle(user.id)
        
        text = f"⚔️ МЕНЮ БИТВ\n\n"
        text += f"Уровень: {user.level}\n"
        text += f"❤️ HP: {user.current_hp}/{user.max_hp}\n"
        text += f"🔷 MP: {user.current_mp}/{user.max_mp}\n"
        text += f"Убито мобов: {user.mobs_killed}\n\n"
        
        if active_battle:
            text += f"⚔️ Активный бой:\n"
            text += f"• {active_battle['mob_icon']} {active_battle['mob_name']} (Ур. {active_battle['mob_level']})\n"
            text += f"• Ход: {active_battle['turn']}\n"
            text += f"• Твое HP: {active_battle['player_hp']}/{active_battle['player_max_hp']}\n"
            text += f"• HP моба: {active_battle['mob_hp']}/{active_battle['mob_max_hp']}\n"
        
        keyboard_buttons = []
        
        if active_battle:
            keyboard_buttons.append([
                InlineKeyboardButton(text="⚔️ Продолжить бой", callback_data="battle_active")
            ])
        else:
            keyboard_buttons.append([
                InlineKeyboardButton(text="⚔️ Начать бой", callback_data="battle_start")
            ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="📚 Навыки", callback_data="battle_skills"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="battle_stats")
        ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard)

async def show_available_mobs(callback: CallbackQuery):
    """Показать доступных для боя мобов"""
    from database import get_db_session
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        # Получаем мобов для текущей локации
        location = await db.get(Location, user.current_location_id)
        if not location:
            await callback.answer("Локация не найдена")
            return
        
        result = await db.execute(
            select(MobSpawn).where(
                and_(
                    MobSpawn.location_id == location.id,
                    MobSpawn.min_level <= user.level,
                    MobSpawn.max_level >= user.level
                )
            ).options(selectinload(MobSpawn.mob_template))
        )
        mob_spawns = result.scalars().all()
        
        if not mob_spawns:
            await callback.message.edit_text(
                "В этой локации нет доступных мобов для боя.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="battle_menu")]
                ])
            )
            return
        
        text = f"🎯 ВЫБОР ПРОТИВНИКА\n\n"
        text += f"📍 Локация: {location.name}\n"
        text += f"👤 Ваш уровень: {user.level}\n\n"
        text += "Доступные мобы:\n\n"
        
        keyboard_buttons = []
        for spawn in mob_spawns:
            if random.random() < spawn.spawn_chance:
                mob = spawn.mob_template
                boss_icon = "👑" if mob.is_boss else "⭐" if mob.level > user.level + 5 else ""
                
                text += f"{boss_icon}{mob.icon} {mob.name}\n"
                text += f"• Уровень: {mob.level}\n"
                text += f"• HP: {mob.health}\n"
                text += f"• Урон: {mob.damage_min}-{mob.damage_max}\n"
                text += f"• Шанс появления: {spawn.spawn_chance*100:.0f}%\n\n"
                
                keyboard_buttons.append([
                    InlineKeyboardButton(
                        text=f"{boss_icon}{mob.icon} Сразиться с {mob.name}",
                        callback_data=f"battle_start_{mob.id}"
                    )
                ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="🔄 Обновить список", callback_data="battle_start"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data="battle_menu")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard)

async def start_battle_handler(callback: CallbackQuery, mob_id: uuid.UUID):
    """Обработчик начала боя"""
    from database import get_db_session
    from main import battle_manager
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        result = await battle_manager.start_battle(db, user.id, mob_id)
        
        if "error" in result:
            await callback.answer(result["error"])
            return
        
        mob = await db.get(MobTemplate, mob_id)
        
        text = f"⚔️ БИТВА НАЧАЛАСЬ!\n\n"
        text += f"👤 {user.username or 'Игрок'} (Ур. {user.level})\n"
        text += f"❤️ HP: {result['player_hp']}/{result['player_max_hp']}\n"
        text += f"🔷 MP: {result['player_mp']}/{result['player_max_mp']}\n\n"
        
        text += f"🆚\n\n"
        
        text += f"{mob.icon} {result['mob_name']} (Ур. {result['mob_level']})\n"
        text += f"❤️ HP: {result['mob_hp']}/{result['mob_max_hp']}\n"
        
        if result.get('is_boss'):
            text += "👑 ЭТО БОСС!\n"
        
        keyboard = create_battle_keyboard(uuid.UUID(result['battle_id']))
        
        await callback.message.edit_text(text, reply_markup=keyboard)

async def show_active_battle(callback: CallbackQuery):
    """Показать активный бой"""
    from database import get_db_session
    from main import battle_manager
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        active_battle = await battle_manager.get_active_battle(user.id)
        
        if not active_battle:
            await callback.answer("У вас нет активного боя")
            await show_battle_menu(callback)
            return
        
        text = f"⚔️ АКТИВНЫЙ БОЙ\n\n"
        text += f"Ход: {active_battle['turn']}\n\n"
        
        text += f"👤 {user.username or 'Игрок'}\n"
        text += f"❤️ HP: {active_battle['player_hp']}/{active_battle['player_max_hp']}\n"
        text += f"🔷 MP: {user.current_mp}/{user.max_mp}\n\n"
        
        text += f"🆚\n\n"
        
        text += f"{active_battle['mob_icon']} {active_battle['mob_name']}\n"
        text += f"❤️ HP: {active_battle['mob_hp']}/{active_battle['mob_max_hp']}\n"
        
        # Показываем эффекты
        if active_battle['effects']:
            text += "\n🎭 Эффекты:\n"
            for effect in active_battle['effects']:
                if str(user.id) == effect['target_id']:
                    text += f"• {effect['effect_type']}: {effect['value']} (осталось {effect['remaining_turns']} ходов)\n"
        
        keyboard = create_battle_keyboard(uuid.UUID(active_battle['battle_id']))
        
        await callback.message.edit_text(text, reply_markup=keyboard)

async def process_battle_action_handler(callback: CallbackQuery, battle_id: uuid.UUID, action_type: str):
    """Обработчик действия в бою"""
    from main import battle_manager
    
    action = BattleAction(action_type)
    result = await battle_manager.process_battle_action(battle_id, action)
    
    if "error" in result:
        await callback.answer(result["error"])
        return
    
    # Получаем обновленные данные
    from database import get_db_session
    async with get_db_session() as db:
        battle = await db.get(ActiveBattle, battle_id)
        user = await db.get(User, battle.user_id)
        mob = await db.get(MobTemplate, battle.mob_template_id)
        
        if not battle or not user or not mob:
            await callback.answer("Ошибка получения данных боя")
            return
        
        text = f"⚔️ ХОД БОЯ #{result['turn']}\n\n"
        
        # Показываем действие игрока
        if action == BattleAction.ATTACK:
            if result.get("hit", False):
                text += f"👤 {user.username or 'Игрок'} атакует!\n"
                if result.get("critical", False):
                    text += f"⚡ КРИТИЧЕСКИЙ УДАР!\n"
                text += f"Нанесено урона: {result.get('damage', 0)}\n"
            else:
                text += f"👤 {user.username or 'Игрок'} промахивается!\n"
        
        elif action == BattleAction.DEFEND:
            text += f"👤 {user.username or 'Игрок'} защищается!\n"
            text += f"Следующий урон снижен на {result.get('defense_bonus', 0)*100}%\n"
        
        elif action == BattleAction.DODGE:
            text += f"👤 {user.username or 'Игрок'} готовится уворачиваться!\n"
            text += f"Шанс уклонения увеличен на {result.get('dodge_bonus', 0)*100}%\n"
        
        elif action == BattleAction.FLEE:
            if result.get("success", False):
                text += f"👤 {user.username or 'Игрок'} успешно сбежал!\n"
            else:
                text += f"👤 {user.username or 'Игрок'} не смог сбежать!\n"
        
        elif action == BattleAction.USE_ITEM:
            text += f"👤 {user.username or 'Игрок'} использует предмет!\n"
            if result.get("heal"):
                text += f"Восстановлено HP: {result['heal']}\n"
            if result.get("mana"):
                text += f"Восстановлено MP: {result['mana']}\n"
        
        elif action == BattleAction.USE_SKILL:
            text += f"👤 {user.username or 'Игрок'} использует навык!\n"
            if result.get("damage"):
                text += f"Нанесено урона: {result['damage']}\n"
            if result.get("heal"):
                text += f"Восстановлено HP: {result['heal']}\n"
        
        # Показываем ход моба
        if result.get("mob_turn"):
            mob_turn = result["mob_turn"]
            if mob_turn.get("hit", False):
                text += f"\n{mob.icon} {mob.name} атакует!\n"
                if mob_turn.get("critical", False):
                    text += f"⚡ КРИТИЧЕСКИЙ УДАР!\n"
                text += f"Получено урона: {mob_turn.get('damage', 0)}\n"
            else:
                text += f"\n{mob.icon} {mob.name} промахивается!\n"
        
        text += f"\n━━━━━━━━━━━━━━━━\n\n"
        text += f"👤 {user.username or 'Игрок'}\n"
        text += f"❤️ HP: {result['player_hp']}/{result['player_max_hp']}\n"
        text += f"🔷 MP: {result.get('player_mp', user.current_mp)}/{result.get('player_max_mp', user.max_mp)}\n\n"
        
        text += f"🆚\n\n"
        
        text += f"{mob.icon} {mob.name}\n"
        text += f"❤️ HP: {result['mob_hp']}/{result['mob_max_hp']}\n"
        
        # Если бой завершен
        if result.get("battle_finished"):
            text += f"\n{'🎉 ПОБЕДА!' if result.get('victory') else '💀 ПОРАЖЕНИЕ'}\n\n"
            
            if result.get("victory"):
                rewards = result.get("rewards", {})
                text += f"Награды:\n"
                text += f"• Опыт: {rewards.get('xp', 0)}\n"
                text += f"• Золото: {rewards.get('gold', 0)}\n"
                
                if rewards.get("items"):
                    text += f"• Предметы:\n"
                    for item in rewards["items"]:
                        text += f"  {item['icon']} {item['name']} ×{item['quantity']}\n"
            else:
                penalty = result.get("rewards", {})
                text += f"Штрафы:\n"
                text += f"• Потеряно золота: {penalty.get('gold_lost', 0)}\n"
                text += f"• Потеряно опыта: {penalty.get('xp_lost', 0)}\n"
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⚔️ В меню битв", callback_data="battle_menu")]
            ])
        else:
            keyboard = create_battle_keyboard(battle_id)
        
        await callback.message.edit_text(text, reply_markup=keyboard)

async def use_item_in_battle_handler(callback: CallbackQuery, battle_id: uuid.UUID, item_id: uuid.UUID):
    """Обработчик использования предмета в бою"""
    from main import battle_manager
    
    result = await battle_manager.process_battle_action(battle_id, BattleAction.USE_ITEM, item_id=item_id)
    
    if "error" in result:
        await callback.answer(result["error"])
        return
    
    # Перенаправляем на обработку действия
    await process_battle_action_handler(callback, battle_id, "use_item")

async def use_skill_in_battle_handler(callback: CallbackQuery, battle_id: uuid.UUID, skill_id: str):
    """Обработчик использования навыка в бою"""
    from main import battle_manager
    
    result = await battle_manager.process_battle_action(battle_id, BattleAction.USE_SKILL, skill_id=skill_id)
    
    if "error" in result:
        await callback.answer(result["error"])
        return
    
    # Перенаправляем на обработку действия
    await process_battle_action_handler(callback, battle_id, "use_skill")

async def show_player_skills(callback: CallbackQuery):
    """Показать навыки игрока"""
    from database import get_db_session
    from main import battle_manager
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        # Проверяем активный бой
        active_battle = await battle_manager.get_active_battle(user.id)
        battle_id = uuid.UUID(active_battle['battle_id']) if active_battle else None
        
        skills = await battle_manager.get_available_skills(user, battle_id) if battle_id else []
        
        text = f"📚 НАВЫКИ ИГРОКА\n\n"
        text += f"🔷 MP: {user.current_mp}/{user.max_mp}\n\n"
        
        if not skills:
            text += "У вас нет доступных навыков.\n"
            text += "Навыки открываются с повышением уровня."
        else:
            for skill in skills:
                available_icon = "✅" if skill["available"] else "❌" if skill["on_cooldown"] else "⚠️"
                cooldown_text = f" (КД: {skill['cooldown_remaining']})" if skill["on_cooldown"] else ""
                
                text += f"{available_icon} {skill['icon']} {skill['name']}{cooldown_text}\n"
                text += f"  {skill['description']}\n"
                
                if skill["damage"] > 0:
                    text += f"  Урон: {skill['damage']} | "
                if skill["heal"] > 0:
                    text += f"  Лечение: {skill['heal']} | "
                
                text += f"Мана: {skill['mp_cost']}\n\n"
        
        keyboard_buttons = []
        
        if active_battle and skills:
            text += "\nВыберите навык для использования в бою:\n"
            for skill in skills:
                if skill["available"]:
                    keyboard_buttons.append([
                        InlineKeyboardButton(
                            text=f"{skill['icon']} {skill['name']}",
                            callback_data=f"battle_use_skill_{battle_id}_{skill['id']}"
                        )
                    ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data="battle_menu")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard)

# ============ УТИЛИТЫ ============

def create_cancel_keyboard() -> InlineKeyboardMarkup:
    """Создать клавиатуру с кнопкой отмены"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])

def create_battle_keyboard(battle_id: uuid.UUID) -> InlineKeyboardMarkup:
    """Создать клавиатуру для боя"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚔️ Атаковать", callback_data=f"battle_action_{battle_id}_attack"),
            InlineKeyboardButton(text="🛡️ Защищаться", callback_data=f"battle_action_{battle_id}_defend")
        ],
        [
            InlineKeyboardButton(text="🌀 Увернуться", callback_data=f"battle_action_{battle_id}_dodge"),
            InlineKeyboardButton(text="🏃 Сбежать", callback_data=f"battle_action_{battle_id}_flee")
        ],
        [
            InlineKeyboardButton(text="📦 Предметы", callback_data=f"battle_items_{battle_id}"),
            InlineKeyboardButton(text="📚 Навыки", callback_data=f"battle_skills_{battle_id}")
        ],
        [InlineKeyboardButton(text="📊 Статистика боя", callback_data=f"battle_stats_{battle_id}")]
    ])

def create_battle_items_keyboard(battle_id: uuid.UUID, items: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    """Создать клавиатуру с предметами для боя"""
    keyboard_buttons = []
    
    for item in items[:8]:  # Ограничиваем 8 предметами
        keyboard_buttons.append([
            InlineKeyboardButton(
                text=f"{item['icon']} {item['name']} ×{item['quantity']}",
                callback_data=f"battle_use_item_{battle_id}_{item['id']}"
            )
        ])
    
    keyboard_buttons.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"battle_active")
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

# ============ ИНИЦИАЛИЗАЦИЯ ============

async def init_battle_module(redis_client, db_session_factory):
    """Инициализировать модуль битв"""
    battle_manager = BattleManager(redis_client, db_session_factory)
    await battle_manager.restore_state()
    return battle_manager

# Экспортируемые объекты
__all__ = [
    'battle_router',
    'BattleManager',
    'init_battle_module',
    'BattleStates',
    'BattleAction',
    'SkillType',
    'BattleFormulaManager'
]