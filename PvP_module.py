# pvp_module.py
"""
Полный модуль PvP системы с восстановлением состояния при перезапуске.
Включает все формулы расчета, обработчики для админ-панели и игрового процесса.
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
from sqlalchemy.orm import selectinload

from models import (
    User, PvPChallenge, PvPMatch, ActiveBattle, BattleStatus,
    AuditLog, SystemSettings, Item, ItemTemplate, MobTemplate,
    Location, ItemRarity, ItemType, MobType, LocationType,
    ActiveAction, ActionType, StateSnapshot, PlayerStat,
    ActiveEffect, Recipe, RecipeIngredient, ChestTemplate,
    ChestReward, GameEvent, EventTrigger, EventReward,
    ResourceTemplate, ResourceSpawn, MobSpawn, MobDrop,
    ProfessionType, ResourceType, EventType, EventActivationType
)

# ============ КОНСТАНТЫ И КОНФИГУРАЦИЯ ============

class PvPStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"

class PvPAction(str, Enum):
    ATTACK = "attack"
    DEFEND = "defend"
    DODGE = "dodge"
    USE_SKILL = "use_skill"
    USE_ITEM = "use_item"
    FLEE = "flee"

# ============ РОУТЕР И СОСТОЯНИЯ ============

pvp_router = Router()

class PvPStates(StatesGroup):
    waiting_for_bet = State()
    waiting_for_target = State()
    in_battle = State()
    admin_create_mob = State()
    admin_create_item = State()
    admin_create_location = State()
    admin_create_recipe = State()
    admin_create_enchantment = State()
    admin_create_chest = State()
    admin_create_event = State()
    admin_create_resource = State()

# ============ МЕНЕДЖЕР ФОРМУЛ ============

class FormulaManager:
    """Менеджер для работы с формулами из базы данных"""
    
    @staticmethod
    async def get_formula(db: AsyncSession, formula_name: str) -> str:
        """Получить формулу из базы данных"""
        result = await db.execute(
            select(SystemSettings.value).where(SystemSettings.key == f"formula_{formula_name}")
        )
        formula = result.scalar_one_or_none()
        if formula:
            return formula
        return await FormulaManager.get_default_formula(formula_name)
    
    @staticmethod
    async def get_default_formula(formula_name: str) -> str:
        """Получить формулу по умолчанию"""
        default_formulas = {
            # Основные формулы боя
            "damage": "base_damage * (1 + strength / 100) * random(0.9, 1.1) * (1.5 if is_critical else 1)",
            "critical_chance": "base_crit + agility * 0.001 + luck * 0.0005",
            "critical_damage": "1.5 + strength * 0.002",
            "dodge_chance": "base_dodge + agility * 0.0015 + luck * 0.0003",
            "hit_chance": "0.85 + agility * 0.0008 - target_dodge",
            "defense_reduction": "damage * (1 - min(0.8, defense / (defense + 100 * attacker_level)))",
            
            # Опыт и уровни
            "xp_from_mob": "mob_level * 10 + (mob_rarity_modifier * 50)",
            "xp_for_next_level": "current_level * 100 * (1 + current_level * 0.1)",
            "xp_from_pvp": "loser_level * 15",
            
            # Характеристики
            "max_hp": "constitution * 10 + level * 5 + equipment_bonus",
            "max_mp": "intelligence * 5 + level * 2 + equipment_bonus",
            "stamina_regen": "constitution * 0.1 + level * 0.05",
            
            # Вес и инвентарь
            "max_weight": "strength * 2 + constitution * 3",
            "move_speed": "agility * 0.1 - (current_weight / max_weight) * 0.5",
            
            # Крафт и профессии
            "craft_chance": "base_chance + profession_level * 0.01 + intelligence * 0.001",
            "gather_chance": "base_chance + profession_level * 0.02 + strength * 0.0005",
            "quality_chance": "0.01 + profession_level * 0.005 + luck * 0.001",
            
            # Цены и экономика
            "item_price": "base_price * (1 + rarity_modifier) * (1 + quality * 0.1)",
            "repair_cost": "base_price * (1 - durability/max_durability) * 0.3",
            
            # Регенерация
            "hp_regen": "constitution * 0.2 + level * 0.1",
            "mp_regen": "intelligence * 0.3 + level * 0.05",
        }
        return default_formulas.get(formula_name, "1")
    
    @staticmethod
    async def calculate_formula(db: AsyncSession, formula_name: str, variables: Dict[str, Any]) -> float:
        """Вычислить значение по формуле"""
        formula_str = await FormulaManager.get_formula(db, formula_name)
        
        try:
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
        except Exception as e:
            print(f"Error calculating formula {formula_name}: {e}")
            return 1.0

# ============ МЕНЕДЖЕР PVP ============

class PvPManager:
    """Менеджер для управления PvP боями"""
    
    def __init__(self, redis_client, db_session_factory):
        self.redis = redis_client
        self.db_session_factory = db_session_factory
        self.active_battles = {}  # {battle_id: battle_data}
        self.active_challenges = {}  # {challenge_id: challenge_data}
    
    async def restore_state(self):
        """Восстановить все активные PvP состояния при запуске бота"""
        async with self.db_session_factory() as db:
            try:
                # 1. Восстановить активные вызовы
                result = await db.execute(
                    select(PvPChallenge).where(
                        PvPChallenge.status.in_(["pending", "accepted"])
                    )
                )
                challenges = result.scalars().all()
                
                for challenge in challenges:
                    if challenge.expires_at < datetime.utcnow():
                        challenge.status = "expired"
                        await self.redis.delete(f"pvp_challenge:{challenge.id}")
                    else:
                        challenge_key = f"pvp_challenge:{challenge.id}"
                        challenge_data = {
                            "id": str(challenge.id),
                            "challenger_id": str(challenge.challenger_id),
                            "target_id": str(challenge.target_id),
                            "bet_amount": challenge.bet_amount,
                            "status": challenge.status,
                            "expires_at": challenge.expires_at.isoformat(),
                        }
                        await self.redis.setex(
                            challenge_key,
                            int((challenge.expires_at - datetime.utcnow()).total_seconds()),
                            json.dumps(challenge_data)
                        )
                        self.active_challenges[str(challenge.id)] = challenge_data
                
                # 2. Восстановить активные битвы
                result = await db.execute(
                    select(ActiveBattle).where(
                        ActiveBattle.status == BattleStatus.ACTIVE
                    ).options(selectinload(ActiveBattle.user), selectinload(ActiveBattle.pvp_target))
                )
                battles = result.scalars().all()
                
                for battle in battles:
                    battle_key = f"pvp_battle:{battle.id}"
                    
                    # Проверяем время последнего действия
                    if battle.last_action_at and (datetime.utcnow() - battle.last_action_at).seconds > 3600:
                        # Бой устарел, помечаем как завершенный
                        battle.status = BattleStatus.PLAYER_LOST
                        battle.ended_at = datetime.utcnow()
                        await db.commit()
                        continue
                    
                    battle_data = {
                        "id": str(battle.id),
                        "user_id": str(battle.user_id),
                        "pvp_target_id": str(battle.pvp_target_id) if battle.pvp_target_id else None,
                        "player_hp": battle.player_hp,
                        "player_max_hp": battle.player_max_hp,
                        "target_hp": battle.target_hp,
                        "target_max_hp": battle.target_max_hp,
                        "status": battle.status.value,
                        "started_at": battle.started_at.isoformat(),
                        "last_action_at": battle.last_action_at.isoformat(),
                        "bet_amount": battle.bet_amount,
                        "battle_log": battle.battle_log or [],
                    }
                    
                    await self.redis.setex(
                        battle_key,
                        7200,  # 2 часа
                        json.dumps(battle_data)
                    )
                    self.active_battles[str(battle.id)] = battle_data
                
                # 3. Восстановить снапшоты состояний
                result = await db.execute(
                    select(StateSnapshot).where(
                        and_(
                            StateSnapshot.is_restored == False,
                            StateSnapshot.expires_at > datetime.utcnow(),
                            StateSnapshot.snapshot_type == "pvp_battle"
                        )
                    )
                )
                snapshots = result.scalars().all()
                
                for snapshot in snapshots:
                    await self.restore_battle_from_snapshot(db, snapshot)
                
                await db.commit()
                print(f"✅ Восстановлено {len(challenges)} вызовов и {len(battles)} битв")
                
            except Exception as e:
                print(f"❌ Ошибка при восстановлении PvP состояния: {e}")
                await db.rollback()
    
    async def restore_battle_from_snapshot(self, db: AsyncSession, snapshot: StateSnapshot):
        """Восстановить битву из снапшота"""
        try:
            snapshot_data = snapshot.snapshot_data
            
            # Создаем новую битву на основе снапшота
            battle = ActiveBattle(
                id=uuid.uuid4(),
                user_id=snapshot.user_id,
                pvp_target_id=uuid.UUID(snapshot_data.get("pvp_target_id")),
                status=BattleStatus.ACTIVE,
                player_hp=snapshot_data.get("player_hp", 100),
                player_max_hp=snapshot_data.get("player_max_hp", 100),
                target_hp=snapshot_data.get("target_hp", 100),
                target_max_hp=snapshot_data.get("target_max_hp", 100),
                bet_amount=snapshot_data.get("bet_amount", 0),
                started_at=datetime.fromisoformat(snapshot_data.get("started_at")),
                last_action_at=datetime.utcnow(),
                battle_log=snapshot_data.get("battle_log", [])
            )
            
            db.add(battle)
            
            # Помечаем снапшот как восстановленный
            snapshot.is_restored = True
            
            # Сохраняем в Redis
            battle_key = f"pvp_battle:{battle.id}"
            battle_data = {
                "id": str(battle.id),
                "user_id": str(battle.user_id),
                "pvp_target_id": str(battle.pvp_target_id),
                "player_hp": battle.player_hp,
                "player_max_hp": battle.player_max_hp,
                "target_hp": battle.target_hp,
                "target_max_hp": battle.target_max_hp,
                "status": battle.status.value,
                "started_at": battle.started_at.isoformat(),
                "last_action_at": battle.last_action_at.isoformat(),
                "bet_amount": battle.bet_amount,
                "battle_log": battle.battle_log or [],
            }
            
            await self.redis.setex(
                battle_key,
                7200,
                json.dumps(battle_data)
            )
            self.active_battles[str(battle.id)] = battle_data
            
            print(f"✅ Восстановлен PvP бой из снапшота {snapshot.id}")
            
        except Exception as e:
            print(f"❌ Ошибка восстановления боя из снапшота: {e}")
    
    # ============ РАСЧЕТЫ И ФОРМУЛЫ ============
    
    async def calculate_damage(self, db: AsyncSession, attacker: User, defender: User, 
                               weapon_data: Optional[Dict] = None) -> Tuple[int, bool]:
        """Рассчитать урон от атаки"""
        # Получаем характеристики атакующего
        attacker_strength = attacker.strength
        attacker_agility = attacker.agility
        
        # Базовый урон
        base_damage = 10  # Минимальный урон
        
        if weapon_data:
            weapon_damage = random.randint(
                weapon_data.get("damage_min", 0),
                weapon_data.get("damage_max", 0)
            )
            base_damage += weapon_damage
        
        # Получаем формулы из БД
        damage_formula = await FormulaManager.get_formula(db, "damage")
        crit_chance_formula = await FormulaManager.get_formula(db, "critical_chance")
        crit_damage_formula = await FormulaManager.get_formula(db, "critical_damage")
        
        # Рассчитываем шанс крита
        variables = {
            "base_damage": base_damage,
            "strength": attacker_strength,
            "agility": attacker_agility,
            "base_crit": 0.05,  # Базовый шанс крита 5%
            "luck": 0,  # Пока не используется
        }
        
        crit_chance = await FormulaManager.calculate_formula(db, "critical_chance", variables)
        crit_chance = min(max(crit_chance, 0), 0.5)  # Ограничиваем 50%
        
        # Проверяем крит
        is_critical = random.random() < crit_chance
        
        # Рассчитываем множитель крита
        crit_multiplier = await FormulaManager.calculate_formula(db, "critical_damage", {
            "strength": attacker_strength,
            "base_damage": 1.5
        })
        
        # Рассчитываем итоговый урон
        damage_variables = {
            "base_damage": base_damage,
            "strength": attacker_strength,
            "is_critical": is_critical,
            "crit_multiplier": crit_multiplier if is_critical else 1,
        }
        
        final_damage = await FormulaManager.calculate_formula(db, "damage", damage_variables)
        
        # Учитываем защиту противника
        defense_reduction = await FormulaManager.calculate_formula(db, "defense_reduction", {
            "damage": final_damage,
            "defense": defender.armor_id,  # TODO: Получить реальную защиту
            "attacker_level": attacker.level
        })
        
        final_damage = max(1, int(final_damage - defense_reduction))
        
        return final_damage, is_critical
    
    async def calculate_dodge_chance(self, db: AsyncSession, defender: User, attacker: User) -> float:
        """Рассчитать шанс уклонения"""
        variables = {
            "base_dodge": 0.05,
            "agility": defender.agility,
            "luck": 0,
            "attacker_level": attacker.level,
            "defender_level": defender.level,
        }
        
        dodge_chance = await FormulaManager.calculate_formula(db, "dodge_chance", variables)
        return min(max(dodge_chance, 0), 0.3)  # Ограничиваем 30%
    
    async def calculate_hit_chance(self, db: AsyncSession, attacker: User, defender: User) -> float:
        """Рассчитать шанс попадания"""
        variables = {
            "agility": attacker.agility,
            "target_dodge": await self.calculate_dodge_chance(db, defender, attacker),
            "attacker_level": attacker.level,
            "defender_level": defender.level,
        }
        
        hit_chance = await FormulaManager.calculate_formula(db, "hit_chance", variables)
        return min(max(hit_chance, 0.5), 0.95)  # Ограничиваем 50-95%
    
    # ============ ОСНОВНЫЕ МЕТОДЫ PVP ============
    
    async def create_challenge(self, challenger_id: uuid.UUID, target_id: uuid.UUID, 
                              bet_amount: int) -> PvPChallenge:
        """Создать PvP вызов"""
        async with self.db_session_factory() as db:
            # Проверяем существование игроков
            challenger = await db.get(User, challenger_id)
            target = await db.get(User, target_id)
            
            if not challenger or not target:
                raise ValueError("Игрок не найден")
            
            # Проверяем уровень игроков
            settings = await db.execute(
                select(SystemSettings.value).where(SystemSettings.key == "pvp_min_level")
            )
            min_level = settings.scalar_one_or_none() or 10
            
            if challenger.level < min_level or target.level < min_level:
                raise ValueError(f"Минимальный уровень для PvP: {min_level}")
            
            # Проверяем разницу уровней
            level_diff = await FormulaManager.get_formula(db, "pvp_level_difference")
            level_diff = int(eval(level_diff)) if level_diff.isdigit() else 15
            
            if abs(challenger.level - target.level) > level_diff:
                raise ValueError(f"Максимальная разница уровней: {level_diff}")
            
            # Проверяем достаточно ли золота
            if challenger.gold < bet_amount:
                raise ValueError("Недостаточно золота для ставки")
            
            # Замораживаем золото
            challenger.gold -= bet_amount
            
            # Создаем вызов
            challenge = PvPChallenge(
                challenger_id=challenger_id,
                target_id=target_id,
                bet_amount=bet_amount,
                status="pending",
                expires_at=datetime.utcnow() + timedelta(minutes=5)
            )
            
            db.add(challenge)
            
            # Логируем действие
            audit_log = AuditLog(
                user_id=challenger_id,
                action="pvp_challenge_created",
                details={
                    "target_id": str(target_id),
                    "bet_amount": bet_amount,
                    "challenge_id": str(challenge.id)
                }
            )
            db.add(audit_log)
            
            await db.commit()
            
            # Сохраняем в Redis
            challenge_key = f"pvp_challenge:{challenge.id}"
            challenge_data = {
                "id": str(challenge.id),
                "challenger_id": str(challenger_id),
                "target_id": str(target_id),
                "bet_amount": bet_amount,
                "status": "pending",
                "expires_at": challenge.expires_at.isoformat(),
            }
            
            await self.redis.setex(
                challenge_key,
                300,  # 5 минут
                json.dumps(challenge_data)
            )
            
            self.active_challenges[str(challenge.id)] = challenge_data
            
            return challenge
    
    async def accept_challenge(self, challenge_id: uuid.UUID) -> ActiveBattle:
        """Принять PvP вызов"""
        async with self.db_session_factory() as db:
            challenge = await db.get(PvPChallenge, challenge_id)
            
            if not challenge:
                raise ValueError("Вызов не найден")
            
            if challenge.status != "pending":
                raise ValueError("Вызов уже обработан")
            
            if challenge.expires_at < datetime.utcnow():
                challenge.status = "expired"
                await db.commit()
                raise ValueError("Вызов истек")
            
            # Получаем игроков
            challenger = await db.get(User, challenge.challenger_id)
            target = await db.get(User, challenge.target_id)
            
            # Проверяем достаточно ли золота у цели
            if target.gold < challenge.bet_amount:
                raise ValueError("У противника недостаточно золота")
            
            # Замораживаем золото цели
            target.gold -= challenge.bet_amount
            
            # Создаем активную битву
            battle = ActiveBattle(
                user_id=challenge.challenger_id,
                pvp_target_id=challenge.target_id,
                status=BattleStatus.ACTIVE,
                player_hp=await self.calculate_max_hp(db, challenger),
                player_max_hp=await self.calculate_max_hp(db, challenger),
                target_hp=await self.calculate_max_hp(db, target),
                target_max_hp=await self.calculate_max_hp(db, target),
                bet_amount=challenge.bet_amount * 2,  # Ставка удваивается
                started_at=datetime.utcnow(),
                last_action_at=datetime.utcnow(),
                battle_log=[]
            )
            
            db.add(battle)
            
            # Обновляем статус вызова
            challenge.status = "accepted"
            
            # Логируем
            audit_log = AuditLog(
                user_id=challenge.target_id,
                action="pvp_challenge_accepted",
                details={
                    "challenge_id": str(challenge_id),
                    "battle_id": str(battle.id)
                }
            )
            db.add(audit_log)
            
            await db.commit()
            
            # Сохраняем в Redis
            battle_key = f"pvp_battle:{battle.id}"
            battle_data = {
                "id": str(battle.id),
                "user_id": str(battle.user_id),
                "pvp_target_id": str(battle.pvp_target_id),
                "player_hp": battle.player_hp,
                "player_max_hp": battle.player_max_hp,
                "target_hp": battle.target_hp,
                "target_max_hp": battle.target_max_hp,
                "status": battle.status.value,
                "started_at": battle.started_at.isoformat(),
                "last_action_at": battle.last_action_at.isoformat(),
                "bet_amount": battle.bet_amount,
                "battle_log": battle.battle_log or [],
            }
            
            await self.redis.setex(
                battle_key,
                7200,
                json.dumps(battle_data)
            )
            
            self.active_battles[str(battle.id)] = battle_data
            
            # Удаляем вызов из Redis
            await self.redis.delete(f"pvp_challenge:{challenge_id}")
            if str(challenge_id) in self.active_challenges:
                del self.active_challenges[str(challenge_id)]
            
            return battle
    
    async def process_battle_action(self, battle_id: uuid.UUID, attacker_id: uuid.UUID, 
                                   action: PvPAction, **kwargs) -> Dict[str, Any]:
        """Обработать действие в битве"""
        async with self.db_session_factory() as db:
            battle = await db.get(ActiveBattle, battle_id)
            
            if not battle:
                raise ValueError("Битва не найдена")
            
            if battle.status != BattleStatus.ACTIVE:
                raise ValueError("Битва уже завершена")
            
            # Определяем кто атакует и кто защищается
            if battle.user_id == attacker_id:
                attacker = await db.get(User, battle.user_id)
                defender = await db.get(User, battle.pvp_target_id)
                is_player_attacking = True
            else:
                attacker = await db.get(User, battle.pvp_target_id)
                defender = await db.get(User, battle.user_id)
                is_player_attacking = False
            
            battle_log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "attacker_id": str(attacker_id),
                "action": action.value,
                "is_player_attacking": is_player_attacking,
            }
            
            result = {}
            
            if action == PvPAction.ATTACK:
                # Рассчитываем урон
                damage, is_critical = await self.calculate_damage(db, attacker, defender)
                
                # Проверяем попадание
                hit_chance = await self.calculate_hit_chance(db, attacker, defender)
                hit_success = random.random() < hit_chance
                
                if hit_success:
                    # Применяем урон
                    if is_player_attacking:
                        battle.target_hp = max(0, battle.target_hp - damage)
                    else:
                        battle.player_hp = max(0, battle.player_hp - damage)
                    
                    battle_log_entry.update({
                        "hit": True,
                        "damage": damage,
                        "critical": is_critical,
                        "remaining_hp": battle.target_hp if is_player_attacking else battle.player_hp
                    })
                    
                    result = {
                        "hit": True,
                        "damage": damage,
                        "critical": is_critical,
                        "attacker_name": attacker.username or "Игрок",
                        "defender_name": defender.username or "Игрок"
                    }
                else:
                    battle_log_entry.update({
                        "hit": False,
                        "reason": "miss"
                    })
                    
                    result = {
                        "hit": False,
                        "reason": "miss",
                        "attacker_name": attacker.username or "Игрок"
                    }
            
            elif action == PvPAction.DEFEND:
                # Защита снижает следующий полученный урон
                battle_log_entry.update({
                    "defending": True,
                    "damage_reduction": 0.5
                })
                
                result = {
                    "action": "defend",
                    "damage_reduction": 0.5,
                    "player_name": attacker.username or "Игрок"
                }
            
            elif action == PvPAction.FLEE:
                # Попытка сбежать
                flee_chance = 0.3  # 30% шанс сбежать
                flee_success = random.random() < flee_chance
                
                if flee_success:
                    battle.status = BattleStatus.FLED
                    battle.ended_at = datetime.utcnow()
                    
                    # Возвращаем золото
                    if is_player_attacking:
                        flee_player = await db.get(User, battle.user_id)
                        target_player = await db.get(User, battle.pvp_target_id)
                    else:
                        flee_player = await db.get(User, battle.pvp_target_id)
                        target_player = await db.get(User, battle.user_id)
                    
                    # Сбежавший теряет 50% ставки
                    lost_amount = int(battle.bet_amount * 0.5)
                    flee_player.gold -= lost_amount
                    target_player.gold += lost_amount
                    
                    battle_log_entry.update({
                        "fled": True,
                        "lost_amount": lost_amount
                    })
                    
                    result = {
                        "fled": True,
                        "lost_amount": lost_amount,
                        "player_name": flee_player.username or "Игрок"
                    }
                else:
                    battle_log_entry.update({
                        "fled": False,
                        "reason": "failed"
                    })
                    
                    result = {
                        "fled": False,
                        "player_name": attacker.username or "Игрок"
                    }
            
            # Обновляем лог битвы
            current_log = battle.battle_log or []
            current_log.append(battle_log_entry)
            battle.battle_log = current_log
            battle.last_action_at = datetime.utcnow()
            
            # Проверяем окончание битвы
            if battle.player_hp <= 0 or battle.target_hp <= 0:
                await self.finish_battle(db, battle)
            
            await db.commit()
            
            # Обновляем в Redis
            battle_key = f"pvp_battle:{battle.id}"
            battle_data = {
                "id": str(battle.id),
                "user_id": str(battle.user_id),
                "pvp_target_id": str(battle.pvp_target_id),
                "player_hp": battle.player_hp,
                "player_max_hp": battle.player_max_hp,
                "target_hp": battle.target_hp,
                "target_max_hp": battle.target_max_hp,
                "status": battle.status.value,
                "started_at": battle.started_at.isoformat(),
                "last_action_at": battle.last_action_at.isoformat(),
                "bet_amount": battle.bet_amount,
                "battle_log": battle.battle_log or [],
            }
            
            await self.redis.setex(
                battle_key,
                7200,
                json.dumps(battle_data)
            )
            
            self.active_battles[str(battle.id)] = battle_data
            
            return result
    
    async def finish_battle(self, db: AsyncSession, battle: ActiveBattle):
        """Завершить битву и раздать награды"""
        # Определяем победителя
        if battle.player_hp <= 0:
            winner_id = battle.pvp_target_id
            loser_id = battle.user_id
            battle.status = BattleStatus.PLAYER_LOST
        else:
            winner_id = battle.user_id
            loser_id = battle.pvp_target_id
            battle.status = BattleStatus.PLAYER_WON
        
        winner = await db.get(User, winner_id)
        loser = await db.get(User, loser_id)
        
        # Награждаем победителя
        winner.gold += battle.bet_amount
        
        # Добавляем опыт
        xp_reward = await self.calculate_xp_from_pvp(db, winner.level, loser.level)
        winner.experience += xp_reward
        
        # Обновляем статистику
        await self.update_pvp_stats(db, winner_id, loser_id, True)
        await self.update_pvp_stats(db, loser_id, winner_id, False)
        
        # Создаем запись о матче
        pvp_match = PvPMatch(
            player1_id=battle.user_id,
            player2_id=battle.pvp_target_id,
            bet_amount=battle.bet_amount,
            winner_id=winner_id,
            loser_id=loser_id,
            player1_hp_lost=battle.player_max_hp - battle.player_hp if battle.player_hp > 0 else battle.player_max_hp,
            player2_hp_lost=battle.target_max_hp - battle.target_hp if battle.target_hp > 0 else battle.target_max_hp,
            rounds_count=len(battle.battle_log or []),
            started_at=battle.started_at,
            ended_at=datetime.utcnow(),
            battle_log=battle.battle_log
        )
        
        db.add(pvp_match)
        battle.ended_at = datetime.utcnow()
        
        # Логируем
        audit_log = AuditLog(
            user_id=winner_id,
            action="pvp_battle_finished",
            details={
                "battle_id": str(battle.id),
                "winner_id": str(winner_id),
                "loser_id": str(loser_id),
                "bet_amount": battle.bet_amount,
                "xp_reward": xp_reward
            }
        )
        db.add(audit_log)
    
    async def calculate_max_hp(self, db: AsyncSession, user: User) -> int:
        """Рассчитать максимальное HP игрока"""
        variables = {
            "constitution": user.constitution,
            "level": user.level,
            "equipment_bonus": 0  # TODO: Добавить бонус от экипировки
        }
        
        max_hp = await FormulaManager.calculate_formula(db, "max_hp", variables)
        return int(max_hp)
    
    async def calculate_xp_from_pvp(self, db: AsyncSession, winner_level: int, loser_level: int) -> int:
        """Рассчитать опыт за PvP победу"""
        variables = {
            "winner_level": winner_level,
            "loser_level": loser_level,
            "base_xp": 50
        }
        
        xp = await FormulaManager.calculate_formula(db, "xp_from_pvp", variables)
        return int(xp)
    
    async def update_pvp_stats(self, db: AsyncSession, player_id: uuid.UUID, 
                               opponent_id: uuid.UUID, won: bool):
        """Обновить статистику PvP"""
        player = await db.get(User, player_id)
        
        if won:
            player.players_killed += 1
        else:
            player.deaths += 1
        
        # Обновляем статистику игрока
        player_stat = await db.execute(
            select(PlayerStat).where(PlayerStat.user_id == player_id)
        )
        player_stat = player_stat.scalar_one_or_none()
        
        if player_stat:
            if won:
                player_stat.daily_players_killed += 1
            player_stat.last_pvp_time = datetime.utcnow()

# ============ ХЭНДЛЕРЫ ДЛЯ АДМИН-ПАНЕЛИ ============

@pvp_router.callback_query(F.data.startswith("pvp_admin_"))
async def handle_admin_pvp(callback: CallbackQuery, state: FSMContext):
    """Обработчик админ-панели PvP"""
    action = callback.data.replace("pvp_admin_", "")
    
    if action == "menu":
        await show_admin_pvp_menu(callback)
    
    elif action == "create_mob":
        await state.set_state(PvPStates.admin_create_mob)
        await callback.message.edit_text(
            "🛠️ СОЗДАНИЕ НОВОГО МОБА\n\n"
            "Введите данные в формате:\n"
            "Название:Описание:Тип:Уровень:Здоровье:Урон мин-макс\n\n"
            "Пример:\n"
            "Лесной волк:Серый хищник леса:beast:5:50:10-15",
            reply_markup=create_cancel_keyboard()
        )
    
    elif action == "create_item":
        await state.set_state(PvPStates.admin_create_item)
        await callback.message.edit_text(
            "⚔️ СОЗДАНИЕ НОВОГО ПРЕДМЕТА\n\n"
            "Введите данные в формате:\n"
            "Название:Тип:Редкость:Уровень:Цена:Урон/Защита\n\n"
            "Пример:\n"
            "Железный меч:weapon:common:5:100:15-25",
            reply_markup=create_cancel_keyboard()
        )
    
    elif action == "formulas":
        await show_formula_editor(callback)

async def show_admin_pvp_menu(callback: CallbackQuery):
    """Показать меню админ-панели PvP"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧌 Создать моба", callback_data="pvp_admin_create_mob")],
        [InlineKeyboardButton(text="⚔️ Создать предмет", callback_data="pvp_admin_create_item")],
        [InlineKeyboardButton(text="📍 Создать локацию", callback_data="pvp_admin_create_location")],
        [InlineKeyboardButton(text="🔨 Создать рецепт", callback_data="pvp_admin_create_recipe")],
        [InlineKeyboardButton(text="✨ Создать зачарование", callback_data="pvp_admin_create_enchantment")],
        [InlineKeyboardButton(text="🎁 Создать сундук", callback_data="pvp_admin_create_chest")],
        [InlineKeyboardButton(text="🎭 Создать событие", callback_data="pvp_admin_create_event")],
        [InlineKeyboardButton(text="📈 Редактор формул", callback_data="pvp_admin_formulas")],
        [InlineKeyboardButton(text="📊 Статистика PvP", callback_data="pvp_admin_stats")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_menu")]
    ])
    
    await callback.message.edit_text(
        "🛡️ АДМИН-ПАНЕЛЬ PVP\n\n"
        "Выберите действие:",
        reply_markup=keyboard
    )

async def show_formula_editor(callback: CallbackQuery):
    """Показать редактор формул"""
    from database import get_db_session
    
    async with get_db_session() as db:
        formulas = await db.execute(
            select(SystemSettings).where(SystemSettings.key.like("formula_%"))
        )
        formulas = formulas.scalars().all()
        
        text = "📈 РЕДАКТОР ФОРМУЛ\n\n"
        
        keyboard_buttons = []
        for formula in formulas:
            formula_name = formula.key.replace("formula_", "")
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"✏️ {formula_name}",
                    callback_data=f"pvp_edit_formula_{formula_name}"
                )
            ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="➕ Новая формула", callback_data="pvp_new_formula"),
            InlineKeyboardButton(text="⬅️ Назад", callback_data="pvp_admin_menu")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        await callback.message.edit_text(text, reply_markup=keyboard)

# ============ ХЭНДЛЕРЫ ДЛЯ ИГРОКОВ ============

@pvp_router.callback_query(F.data.startswith("pvp_"))
async def handle_player_pvp(callback: CallbackQuery, state: FSMContext):
    """Обработчик PvP для игроков"""
    action = callback.data.replace("pvp_", "")
    
    if action == "menu":
        await show_pvp_menu(callback)
    
    elif action == "challenge":
        await state.set_state(PvPStates.waiting_for_bet)
        await callback.message.edit_text(
            "⚔️ ВЫЗОВ НА ДУЭЛЬ\n\n"
            "Введите сумму ставки:",
            reply_markup=create_cancel_keyboard()
        )
    
    elif action == "ranking":
        await show_pvp_ranking(callback)
    
    elif action == "history":
        await show_pvp_history(callback)

async def show_pvp_menu(callback: CallbackQuery):
    """Показать меню PvP для игроков"""
    from database import get_db_session
    
    async with get_db_session() as db:
        user = await db.execute(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        user = user.scalar_one_or_none()
        
        if not user:
            await callback.answer("Игрок не найден")
            return
        
        # Получаем активные вызовы
        challenges = await db.execute(
            select(PvPChallenge).where(
                and_(
                    PvPChallenge.target_id == user.id,
                    PvPChallenge.status == "pending"
                )
            )
        )
        challenges = challenges.scalars().all()
        
        text = (
            "⚔️ PVP АРЕНА\n\n"
            f"Уровень: {user.level}\n"
            f"Побед: {user.players_killed}\n"
            f"Поражений: {user.deaths}\n"
            f"Золото: {user.gold}\n\n"
        )
        
        if challenges:
            text += "📨 Активные вызовы:\n"
            for challenge in challenges[:3]:
                challenger = await db.get(User, challenge.challenger_id)
                text += f"• {challenger.username}: {challenge.bet_amount} золота\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚔️ Вызвать на дуэль", callback_data="pvp_challenge")],
            [InlineKeyboardButton(text="📊 Рейтинг", callback_data="pvp_ranking")],
            [InlineKeyboardButton(text="📋 История", callback_data="pvp_history")],
            [InlineKeyboardButton(text="⚔️ Активные битвы", callback_data="pvp_active")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard)

async def show_pvp_ranking(callback: CallbackQuery):
    """Показать рейтинг PvP"""
    from database import get_db_session
    
    async with get_db_session() as db:
        # Получаем топ игроков по убийствам
        top_players = await db.execute(
            select(User).order_by(desc(User.players_killed)).limit(10)
        )
        top_players = top_players.scalars().all()
        
        text = "🏆 ТОП PVP ИГРОКОВ\n\n"
        
        for i, player in enumerate(top_players, 1):
            ratio = player.players_killed / max(player.deaths, 1)
            text += f"{i}. {player.username or 'Игрок'}\n"
            text += f"   Уровень: {player.level} | Побед: {player.players_killed} | K/D: {ratio:.2f}\n\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚔️ Моя статистика", callback_data="pvp_my_stats")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="pvp_menu")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard)

# ============ УТИЛИТЫ ============

def create_cancel_keyboard() -> InlineKeyboardMarkup:
    """Создать клавиатуру с кнопкой отмены"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")]
    ])

def create_battle_keyboard(battle_id: uuid.UUID) -> InlineKeyboardMarkup:
    """Создать клавиатуру для битвы"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚔️ Атаковать", 
                               callback_data=f"battle_attack_{battle_id}"),
            InlineKeyboardButton(text="🛡️ Защищаться", 
                               callback_data=f"battle_defend_{battle_id}")
        ],
        [
            InlineKeyboardButton(text="🌀 Увернуться", 
                               callback_data=f"battle_dodge_{battle_id}"),
            InlineKeyboardButton(text="🏃 Сбежать", 
                               callback_data=f"battle_flee_{battle_id}")
        ],
        [InlineKeyboardButton(text="📊 Статистика", 
                            callback_data=f"battle_stats_{battle_id}")]
    ])

# ============ ИНИЦИАЛИЗАЦИЯ ============

async def init_pvp_module(redis_client, db_session_factory):
    """Инициализировать модуль PvP"""
    pvp_manager = PvPManager(redis_client, db_session_factory)
    await pvp_manager.restore_state()
    return pvp_manager

# Экспортируемые объекты
__all__ = [
    'pvp_router',
    'PvPManager',
    'FormulaManager',
    'init_pvp_module',
    'PvPStates',
    'PvPStatus',
    'PvPAction'
]