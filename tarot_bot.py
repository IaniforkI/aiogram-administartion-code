import asyncio
import logging
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from contextlib import contextmanager
from asyncio import Queue
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, Float, JSON, func
from sqlalchemy.orm import declarative_base, sessionmaker, Session, scoped_session
from gigachat import GigaChatAsyncClient
from gigachat.models import Chat, Messages, MessagesRole
import pytz

# ========== КОНФИГУРАЦИЯ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Получаем токены из переменных окружения
BOT_TOKEN = "8006296553:AAHuMkRSaZQax7CrxFCgSKgz_fk_VvGRl7A"
GIGACHAT_TOKEN = "MDE5YWZhNGItNWY5MC03ZjA3LThlYWQtMjczYWZlNDc1NTFiOjAzNjRmOGU5LTk5NjktNGM5MS04Y2FkLWU4MWM4NDkwNjA5Zg=="
GIGACHAT_AUTH_URL = os.getenv("GIGACHAT_AUTH_URL", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth")
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")

# Настройка БД
DATABASE_URL = "sqlite:///tarot_bot_final.db"
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Москва временная зона
MSK_TZ = pytz.timezone('Europe/Moscow')

# ========== МОДЕЛИ БАЗЫ ДАННЫХ ==========
class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, unique=True, nullable=False, index=True)
    username = Column(String(100))
    first_name = Column(String(100))
    last_name = Column(String(100))
    is_admin = Column(Boolean, default=False, index=True)
    is_banned = Column(Boolean, default=False, index=True)
    is_tarologist = Column(Boolean, default=False, index=True)
    balance = Column(Float, default=0.0)
    created_at = Column(DateTime, default=lambda: datetime.now(MSK_TZ))
    last_activity = Column(DateTime, default=lambda: datetime.now(MSK_TZ))
    total_spreads = Column(Integer, default=0)
    
    def to_dict(self):
        """Конвертировать пользователя в словарь"""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.username,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "is_admin": self.is_admin,
            "is_banned": self.is_banned,
            "is_tarologist": self.is_tarologist,
            "balance": self.balance,
            "created_at": self.created_at.strftime("%d.%m.%Y %H:%M") if self.created_at else None,
            "last_activity": self.last_activity.strftime("%d.%m.%Y %H:%M") if self.last_activity else None,
            "total_spreads": self.total_spreads
        }

class TarotSpread(Base):
    __tablename__ = "tarot_spreads"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    spread_type = Column(String(50), nullable=False)
    question = Column(Text)
    interpretation = Column(Text, nullable=False)
    is_tarologist = Column(Boolean, default=False)
    tarologist_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(MSK_TZ), index=True)
    cards = Column(JSON, nullable=True)
    tokens_used = Column(Integer, default=0)
    
    def to_dict(self):
        """Конвертировать расклад в словарь"""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "spread_type": self.spread_type,
            "question": self.question,
            "interpretation": self.interpretation,
            "is_tarologist": self.is_tarologist,
            "tarologist_id": self.tarologist_id,
            "created_at": self.created_at.strftime("%d.%m.%Y %H:%M") if self.created_at else None,
            "preview": f"{self.get_spread_name()}: {self.question[:50] if self.question else 'Без вопроса'}..."
        }
    
    def get_spread_name(self):
        """Получить название расклада"""
        spread_names = {
            'one_card': 'Одна карта',
            'three_cards': '3 карты',
            'celtic_cross': 'Кельтский крест',
            'yes_no': 'Да/Нет',
            'relationship': 'Расклад на отношения',
            'career': 'Расклад на карьеру',
            'tarologist_answer': 'Ответ таролога'
        }
        return spread_names.get(self.spread_type, self.spread_type)

class TarotQuestion(Base):
    __tablename__ = "tarot_questions"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    question = Column(Text, nullable=False)
    status = Column(String(20), default="pending", index=True)  # pending, assigned, answered, cancelled
    tarologist_id = Column(Integer, nullable=True, index=True)
    answer = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(MSK_TZ), index=True)
    assigned_at = Column(DateTime, nullable=True)
    answered_at = Column(DateTime, nullable=True)
    
    def to_dict(self):
        """Конвертировать вопрос в словарь"""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "question": self.question,
            "status": self.status,
            "tarologist_id": self.tarologist_id,
            "created_at": self.created_at.strftime("%d.%m.%Y %H:%M") if self.created_at else None,
            "assigned_at": self.assigned_at.strftime("%d.%m.%Y %H:%M") if self.assigned_at else None,
            "answered_at": self.answered_at.strftime("%d.%m.%Y %H:%M") if self.answered_at else None
        }

# ========== СИСТЕМА ОЧЕРЕДИ ДЛЯ ТАРОЛОГОВ ==========
class TarotQueue:
    def __init__(self):
        self.pending_questions: List[int] = []  # IDs вопросов
        self.assigned_questions: Dict[int, int] = {}  # tarologist_id -> question_id
        self.active_tarologists: Dict[int, datetime] = {}  # tarologist_id -> last_ping
        self._lock = asyncio.Lock()
    
    async def add_question(self, question_id: int):
        """Добавить вопрос в очередь"""
        async with self._lock:
            if question_id not in self.pending_questions:
                self.pending_questions.append(question_id)
                logger.info(f"Question {question_id} added to queue")
    
    async def assign_question(self, tarologist_id: int) -> Optional[int]:
        """Назначить вопрос тарологу"""
        async with self._lock:
            if not self.pending_questions:
                return None
            
            # Проверяем, не занят ли уже таролог
            if tarologist_id in self.assigned_questions:
                return None
            
            question_id = self.pending_questions.pop(0)
            self.assigned_questions[tarologist_id] = question_id
            self.active_tarologists[tarologist_id] = datetime.now(MSK_TZ)
            logger.info(f"Question {question_id} assigned to tarologist {tarologist_id}")
            return question_id
    
    async def complete_question(self, tarologist_id: int) -> bool:
        """Завершить вопрос"""
        async with self._lock:
            if tarologist_id in self.assigned_questions:
                del self.assigned_questions[tarologist_id]
                self.active_tarologists[tarologist_id] = datetime.now(MSK_TZ)
                return True
            return False
    
    async def get_tarologist_question(self, tarologist_id: int) -> Optional[int]:
        """Получить текущий вопрос таролога"""
        async with self._lock:
            return self.assigned_questions.get(tarologist_id)
    
    async def remove_tarologist(self, tarologist_id: int):
        """Удалить таролога из системы"""
        async with self._lock:
            if tarologist_id in self.assigned_questions:
                # Возвращаем вопрос в очередь
                question_id = self.assigned_questions[tarologist_id]
                self.pending_questions.insert(0, question_id)
                del self.assigned_questions[tarologist_id]
            
            if tarologist_id in self.active_tarologists:
                del self.active_tarologists[tarologist_id]
    
    async def get_stats(self) -> Dict[str, any]:
        """Получить статистику очереди"""
        async with self._lock:
            return {
                "pending": len(self.pending_questions),
                "assigned": len(self.assigned_questions),
                "active_tarologists": len(self.active_tarologists)
            }
    
    async def cleanup_inactive(self, inactive_minutes: int = 30):
        """Очистка неактивных тарологов"""
        async with self._lock:
            now = datetime.now(MSK_TZ)
            inactive_tarologists = []
            
            for tarologist_id, last_activity in self.active_tarologists.items():
                if (now - last_activity).total_seconds() > inactive_minutes * 60:
                    inactive_tarologists.append(tarologist_id)
            
            for tarologist_id in inactive_tarologists:
                await self.remove_tarologist(tarologist_id)
                logger.info(f"Removed inactive tarologist {tarologist_id}")

# Глобальная очередь
tarot_queue = TarotQueue()
            
# ========== GIGACHAT КЛИЕНТ ==========
import asyncio
from typing import Optional
from asyncio import Queue



class GigaChatTarotClient:
    def __init__(self, credentials: str, auth_url: str = GIGACHAT_AUTH_URL, scope: str = GIGACHAT_SCOPE, max_concurrent: int = 1):
        self.credentials = credentials
        self.auth_url = auth_url
        self.scope = scope
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.request_queue = Queue()
        self.client = None  # Будем создавать при первом использовании
        self.processing_task = None
        self.is_shutdown = False
        
        # Запускаем обработчик очереди
        self._start_queue_processor()
        
        # Промпты
        self.prompts = {
            "one_card": """Ты опытный таролог с 20-летним стажем. Пользователь спрашивает: "{question}"

Вытащи одну карту Таро для ответа на этот вопрос.

В ответе строго придерживайся структуры:

<b>🎴 КАРТА: [НАЗВАНИЕ КАРТЫ]</b>
<b>📖 Значение:</b> [Краткое классическое значение карты, 2-3 предложения]
<b>💫 Интерпретация для вашего вопроса:</b> [Развернутая трактовка именно в контексте вопроса пользователя, 4-5 предложений]
<b>✨ Совет от карт:</b> [Практический совет, что делать или не делать, 2-3 предложения]
<b>🔮 Общее настроение расклада:</b> [Эмоциональная окраска, 1-2 предложения]

Отвечай только на русском языке. , будь внимательным, поддерживающим, но честным. """,

            "three_cards": """Ты опытный таролог с 20-летним стажем. Пользователь спрашивает: "{question}"

Сделай расклад на три карты: Прошлое, Настоящее, Будущее.

В ответе строго придерживайся структуры:

<b>🎴 РАСКЛАД НА ТРИ КАРТЫ: ПРОШЛОЕ - НАСТОЯЩЕЕ - БУДУЩЕЕ</b>

<b>1️⃣ ПРОШЛОЕ - [НАЗВАНИЕ КАРТЫ]:</b>
<b>📖 Значение:</b> [Значение карты]
<b>💫 Интерпретация:</b> [Как это связано с прошлым в контексте вопроса]

<b>2️⃣ НАСТОЯЩЕЕ - [НАЗВАНИЕ КАРТЫ]:</b>
<b>📖 Значение:</b> [Значение карты]
<b>💫 Интерпретация:</b> [Что это означает сейчас в контексте вопроса]

<b>3️⃣ БУДУЩЕЕ - [НАЗВАНИЕ КАРТЫ]:</b>
<b>📖 Значение:</b> [Значение карты]
<b>💫 Интерпретация:</b> [Что это предвещает в будущем]

<b>✨ ОБЩИЙ ВЫВОД И СОВЕТ:</b>
[Сводная интерпретация всего расклада и практический совет, 4-5 предложений]

<b>🔮 КЛЮЧЕВЫЕ ВЫВОДЫ:</b>
• [Вывод 1]
• [Вывод 2]
• [Вывод 3]

Отвечай только на русском языке. """,

            "celtic_cross": """Ты опытный таролог с 20-летним стажем. Пользователь спрашивает: "{question}"

Сделай полный расклад "Кельтский крест" (10 карт).

В ответе строго придерживайся структуры:

<b>🎴 КЕЛЬТСКИЙ КРЕСТ - 10 КАРТ</b>

<b>ПОЗИЦИИ И ИХ ЗНАЧЕНИЕ:</b>

<b>1. Настоящая ситуация</b> - [КАРТА]
<b>📖</b> [Трактовка для этой позиции]

<b>2. Препятствие</b> - [КАРТА]
<b>📖</b> [Трактовка для этой позиции]

<b>3. Бессознательное влияние</b> - [КАРТА]
<b>📖</b> [Трактовка для этой позиции]

<b>4. Прошлое</b> - [КАРТА]
<b>📖</b> [Трактовка для этой позиции]

<b>5. Сознательные цели</b> - [КАРТА]
<b>📖</b> [Трактовка для этой позиции]

<b>6. Ближайшее будущее</b> - [КАРТА]
<b>📖</b> [Трактовка для этой позиции]

<b>7. Отношение к себе</b> - [КАРТА]
<b>📖</b> [Трактовка для этой позиции]

<b>8. Внешние влияния</b> - [КАРТА]
<b>📖</b> [Трактовка для этой позиции]

<b>9. Надежды и страхи</b> - [КАРТА]
<b>📖</b> [Трактовка для этой позиции]

<b>10. Итог</b> - [КАРТА]
<b>📖</b> [Трактовка для этой позиции]

<b>✨ ПОЛНАЯ ИНТЕРПРЕТАЦИЯ РАСКЛАДА:</b>
[Подробный анализ всех карт вместе, их взаимодействие, 6-8 предложений]

<b>💭 РЕКОМЕНДАЦИИ:</b>
• [Рекомендация 1]
• [Рекомендация 2]
• [Рекомендация 3]

<b>🔮 ЗАКЛЮЧЕНИЕ:</b>
[Финальные выводы, 2-3 предложения]

Отвечай только на русском языке. """,

            "yes_no": """Ты опытный таролог. Пользователь спрашивает: "{question}"

Вытащи одну карту Таро для ответа ДА/НЕТ на этот вопрос.

В ответе строго придерживайся структуры:

<b>🎴 КАРТА ОТВЕТА: [НАЗВАНИЕ КАРТЫ]</b>

<b>📖 Краткое значение:</b> [1-2 предложения]

<b>✅ ОТВЕТ:</b> [ДА/НЕТ/НЕЙТРАЛЬНО с объяснением почему]

<b>💫 Подробная трактовка:</b> [3-4 предложения с объяснением]

<b>✨ Что это значит для вас:</b> [2-3 предложения]

<b>⚠️ Важные нюансы:</b> [1-2 предложения]

Отвечай только на русском языке. """,

            "relationship": """Ты опытный таролог по отношениям. Пользователь спрашивает: "{question}"

Сделай расклад на отношения из 5 карт.

В ответе строго придерживайся структуры:

<b>🎴 РАСКЛАД НА ОТНОШЕНИЯ - 5 КАРТ</b>

<b>1️⃣ Ваши чувства</b> - [КАРТА]
<b>📖</b> [Трактовка]

<b>2️⃣ Чувства партнера</b> - [КАРТА]
<b>📖</b> [Трактовка]

<b>3️⃣ Текущая динамика</b> - [КАРТА]
<b>📖</b> [Трактовка]

<b>4️⃣ Препятствия</b> - [КАРТА]
<b>📖</b> [Трактовка]

<b>5️⃣ Будущее отношений</b> - [КАРТА]
<b>📖</b> [Трактовка]

<b>✨ АНАЛИЗ ОТНОШЕНИЙ:</b>
[Подробный анализ, 4-5 предложений]

<b>💖 РЕКОМЕНДАЦИИ ДЛЯ ПАРЫ:</b>
• [Рекомендация 1]
• [Рекомендация 2]

<b>🔮 ПЕРСПЕКТИВЫ:</b>
[Вывод о перспективах, 2-3 предложения]

Отвечай только на русском языке. """,

            "career": """Ты опытный таролог по карьерным вопросам. Пользователь спрашивает: "{question}"

Сделай расклад на карьеру из 4 карт.

В ответе строго придерживайся структуры:

<b>🎴 КАРЬЕРНЫЙ РАСКЛАД - 4 КАРТЫ</b>

<b>1️⃣ Текущая ситуация на работе</b> - [КАРТА]
<b>📖</b> [Трактовка]

<b>2️⃣ Ваши сильные стороны</b> - [КАРТА]
<b>📖</b> [Трактовка]

<b>3️⃣ Возможности роста</b> - [КАРТА]
<b>📖</b> [Трактовка]

<b>4️⃣ Будущее карьеры</b> - [КАРТА]
<b>📖</b> [Трактовка]

<b>✨ ПРОФЕССИОНАЛЬНЫЙ АНАЛИЗ:</b>
[Анализ карьерной ситуации, 4-5 предложений]

<b>💼 КАРЬЕРНЫЕ РЕКОМЕНДАЦИИ:</b>
• [Рекомендация 1]
• [Рекомендация 2]

<b>🚀 ШАГИ ДЛЯ РАЗВИТИЯ:</b>
[Конкретные шаги, 2-3 предложения]

Отвечай только на русском языке. """
        }
    
    def _start_queue_processor(self):
        """Запускает обработчик очереди в фоновом режиме"""
        if not self.processing_task or self.processing_task.done():
            self.processing_task = asyncio.create_task(self._process_queue())
            logger.info("GigaChat очередь запущена")
    
    async def _ensure_client(self):
        """Создает клиент если он еще не создан"""
        if self.client is None and not self.is_shutdown:
            try:
                self.client = GigaChatAsyncClient(
                    credentials=self.credentials,
                    auth_url=self.auth_url,
                    scope=self.scope,
                    verify_ssl_certs=False
                )
                logger.info("GigaChat клиент создан")
            except Exception as e:
                logger.error(f"Ошибка создания GigaChat клиента: {e}")
                raise
    
    async def _process_queue(self):
        """Обработчик очереди запросов"""
        while not self.is_shutdown:
            try:
                # Ждем задачу из очереди с таймаутом
                try:
                    future, spread_type, question = await asyncio.wait_for(
                        self.request_queue.get(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue  # Проверяем shutdown и продолжаем
                
                # Обрабатываем запрос с семафором
                async with self.semaphore:
                    try:
                        result = await self._generate_spread_internal(spread_type, question)
                        if not future.done():
                            future.set_result(result)
                    except Exception as e:
                        if not future.done():
                            future.set_exception(e)
                    finally:
                        self.request_queue.task_done()
                        
            except asyncio.CancelledError:
                logger.info("GigaChat очередь остановлена")
                break
            except Exception as e:
                logger.error(f"Ошибка в обработчике очереди: {e}")
                await asyncio.sleep(1)
    
    async def _generate_spread_internal(self, spread_type: str, question: str) -> str:
        """Внутренний метод генерации расклада"""
        try:
            # Убеждаемся что клиент создан
            await self._ensure_client()
            
            prompt = self.prompts.get(spread_type, self.prompts["one_card"])
            formatted_prompt = prompt.format(question=question)
            
            messages = [
                Messages(role=MessagesRole.SYSTEM, content="Не используй в своем ответе ни какую разметку текста"),
                Messages(role=MessagesRole.USER, content=formatted_prompt)
            ]
            
            chat = Chat(
                messages=messages,
                model="GigaChat",
                temperature=0.7,
                max_tokens=2000
            )
            
            # Не используем async with, так как клиент создан отдельно
            if self.client:
                response = await self.client.achat(chat)
                result = response.choices[0].message.content
                
                # Очищаем результат от незакрытых HTML тегов
                result = clean_html_tags(result)
                return result
            else:
                raise Exception("GigaChat клиент не доступен")
                
        except Exception as e:
            logger.error(f"GigaChat internal error: {e}")
            
            # Если ошибка связана с клиентом, пересоздаем его
            if "closed" in str(e) or "client has been" in str(e):
                try:
                    await self._recreate_client()
                except:
                    pass
            
            error_responses = {
                "one_card": "<i>Извините, карты не отвечают сейчас. Попробуйте задать вопрос позже.</i> 🃏",
                "three_cards": "<i>Карты задумались... Пожалуйста, попробуйте еще раз через несколько минут.</i> ⏳",
                "celtic_cross": "<i>Кельтский крест требует концентрации. Пожалуйста, повторите запрос.</i> 🧿",
                "default": "<i>Произошла ошибка при генерации расклада. Пожалуйста, попробуйте снова.</i> 🔮"
            }
            return error_responses.get(spread_type, error_responses["default"])
    
    async def _recreate_client(self):
        """Пересоздает клиент"""
        try:
            if self.client:
                try:
                    await self.client.close()
                except:
                    pass
            
            self.client = GigaChatAsyncClient(
                credentials=self.credentials,
                auth_url=self.auth_url,
                scope=self.scope,
                verify_ssl_certs=False
            )
            logger.info("GigaChat клиент пересоздан")
        except Exception as e:
            logger.error(f"Ошибка пересоздания GigaChat клиента: {e}")
            raise
    
    async def generate_spread(self, spread_type: str, question: str, timeout: int = 90) -> str:
        """Генерация расклада через GigaChat с очередью"""
        logger.info(f"Добавлен запрос в очередь: {spread_type}")
        
        # Создаем Future для результата
        future = asyncio.Future()
        
        # Добавляем в очередь
        await self.request_queue.put((future, spread_type, question))
        
        # Ждем результат с таймаутом
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            logger.info(f"Запрос выполнен: {spread_type}")
            return result
        except asyncio.TimeoutError:
            logger.warning(f"Таймаут запроса: {spread_type}")
            
            # Пробуем отменить future если он еще не завершен
            if not future.done():
                future.cancel()
            
            return "<i>⏳ Превышено время ожидания ответа от карт. Сервер перегружен, попробуйте позже.</i>"
        except Exception as e:
            logger.error(f"Ошибка при выполнении запроса: {e}")
            error_responses = {
                "one_card": "<i>Извините, произошла ошибка при обращении к картам.</i> 🃏",
                "three_cards": "<i>Ошибка при генерации расклада. Попробуйте позже.</i> ⏳",
                "default": "<i>Произошла ошибка. Пожалуйста, попробуйте снова.</i> 🔮"
            }
            return error_responses.get(spread_type, error_responses["default"])
    
    async def close(self):
        """Корректное закрытие клиента"""
        self.is_shutdown = True
        
        # Отменяем обработчик очереди
        if self.processing_task:
            self.processing_task.cancel()
            try:
                await self.processing_task
            except asyncio.CancelledError:
                pass
        
        # Закрываем клиент GigaChat
        if self.client:
            try:
                await self.client.close()
                logger.info("GigaChat клиент закрыт")
            except Exception as e:
                logger.error(f"Ошибка закрытия GigaChat клиента: {e}")
        
        # Очищаем очередь
        while not self.request_queue.empty():
            try:
                future, spread_type, question = self.request_queue.get_nowait()
                if not future.done():
                    future.set_exception(asyncio.CancelledError("Клиент закрыт"))
                self.request_queue.task_done()
            except asyncio.QueueEmpty:
                break
    
    def get_queue_stats(self) -> Dict:
        """Получить статистику очереди"""
        return {
            "queue_size": self.request_queue.qsize(),
            "max_concurrent": self.max_concurrent,
            "active_requests": self.max_concurrent - self.semaphore._value,
            "is_shutdown": self.is_shutdown,
            "client_exists": self.client is not None
        }

# ========== ХЕЛПЕР ФУНКЦИИ ==========
@contextmanager
def get_db():
    """Контекстный менеджер для получения сессии БД"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_db_session():
    """Получить сессию БД"""
    return SessionLocal()

async def get_or_create_user(user_id: int, username: str = None, 
                            first_name: str = None, last_name: str = None) -> Dict:
    """Получить или создать пользователя"""
    with get_db() as db:
        user = db.query(User).filter(User.user_id == user_id).first()
        
        if not user:
            user = User(
                user_id=user_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                last_activity=datetime.now(MSK_TZ)
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info(f"New user created: {user_id} ({username})")
        else:
            user.last_activity = datetime.now(MSK_TZ)
            user.username = username or user.username
            user.first_name = first_name or user.first_name
            user.last_name = last_name or user.last_name
            db.commit()
        
        # Возвращаем словарь, а не объект SQLAlchemy
        return user.to_dict()

def is_admin(user_id: int) -> bool:
    """Проверить, является ли пользователь админом"""
    with get_db() as db:
        user = db.query(User).filter(User.user_id == user_id).first()
        return user and user.is_admin

def is_tarologist(user_id: int) -> bool:
    """Проверить, является ли пользователь тарологом"""
    with get_db() as db:
        user = db.query(User).filter(User.user_id == user_id).first()
        return user and user.is_tarologist

def get_user_info(user_id: int) -> Optional[Dict]:
    """Получить информацию о пользователе"""
    with get_db() as db:
        user = db.query(User).filter(User.user_id == user_id).first()
        if user:
            # Получаем статистику пользователя
            total_spreads = db.query(TarotSpread).filter(TarotSpread.user_id == user_id).count()
            ai_spreads = db.query(TarotSpread).filter(
                TarotSpread.user_id == user_id,
                TarotSpread.is_tarologist == False
            ).count()
            tarologist_spreads = db.query(TarotSpread).filter(
                TarotSpread.user_id == user_id,
                TarotSpread.is_tarologist == True
            ).count()
            
            info = user.to_dict()
            info.update({
                "total_spreads": total_spreads,
                "ai_spreads": ai_spreads,
                "tarologist_spreads": tarologist_spreads,
                "questions_asked": db.query(TarotQuestion).filter(TarotQuestion.user_id == user_id).count()
            })
            return info
        return None

def get_all_users(limit: int = 100) -> List[Dict]:
    """Получить всех пользователей"""
    with get_db() as db:
        users = db.query(User).order_by(User.created_at.desc()).limit(limit).all()
        return [user.to_dict() for user in users]

def search_users(search_term: str) -> List[Dict]:
    """Поиск пользователей по username или ID"""
    with get_db() as db:
        users = db.query(User).filter(
            (User.username.ilike(f"%{search_term}%")) | 
            (User.first_name.ilike(f"%{search_term}%")) |
            (User.user_id.cast(String).ilike(f"%{search_term}%"))
        ).limit(20).all()
        return [user.to_dict() for user in users]

def get_user_spreads(user_id: int, limit: int = 20) -> List[Dict]:
    """Получить историю раскладов пользователя"""
    with get_db() as db:
        spreads = (db.query(TarotSpread)
                  .filter(TarotSpread.user_id == user_id)
                  .order_by(TarotSpread.created_at.desc())
                  .limit(limit)
                  .all())
        return [spread.to_dict() for spread in spreads]

def get_spread_by_id(spread_id: int) -> Optional[Dict]:
    """Получить расклад по ID"""
    with get_db() as db:
        spread = db.query(TarotSpread).filter(TarotSpread.id == spread_id).first()
        return spread.to_dict() if spread else None

def save_spread(user_id: int, spread_type: str, question: str, 
                interpretation: str, is_tarologist: bool = False, 
                tarologist_id: int = None) -> int:
    """Сохранить расклад в базу"""
    with get_db() as db:
        spread = TarotSpread(
            user_id=user_id,
            spread_type=spread_type,
            question=question,
            interpretation=interpretation,
            is_tarologist=is_tarologist,
            tarologist_id=tarologist_id,
            created_at=datetime.now(MSK_TZ)
        )
        db.add(spread)
        
        # Обновляем счетчик раскладов пользователя
        user = db.query(User).filter(User.user_id == user_id).first()
        if user:
            user.total_spreads += 1
        
        db.commit()
        db.refresh(spread)
        return spread.id

def create_tarot_question(user_id: int, question: str) -> int:
    """Создать вопрос для таролога"""
    with get_db() as db:
        tarot_question = TarotQuestion(
            user_id=user_id,
            question=question,
            status="pending",
            created_at=datetime.now(MSK_TZ)
        )
        db.add(tarot_question)
        db.commit()
        db.refresh(tarot_question)
        
        # Добавляем в очередь
        asyncio.create_task(tarot_queue.add_question(tarot_question.id))
        
        return tarot_question.id

def get_tarot_question(question_id: int) -> Optional[Dict]:
    """Получить вопрос по ID"""
    with get_db() as db:
        question = db.query(TarotQuestion).filter(TarotQuestion.id == question_id).first()
        return question.to_dict() if question else None

def update_question_status(question_id: int, tarologist_id: int, status: str):
    """Обновить статус вопроса"""
    with get_db() as db:
        question = db.query(TarotQuestion).filter(TarotQuestion.id == question_id).first()
        if question:
            question.status = status
            question.tarologist_id = tarologist_id
            
            if status == "assigned":
                question.assigned_at = datetime.now(MSK_TZ)
            elif status == "answered":
                question.answered_at = datetime.now(MSK_TZ)
            
            db.commit()

def get_pending_questions_count() -> int:
    """Получить количество ожидающих вопросов"""
    with get_db() as db:
        return db.query(TarotQuestion).filter(TarotQuestion.status == "pending").count()

def get_tarologist_stats(tarologist_id: int) -> Dict:
    """Получить статистику таролога"""
    with get_db() as db:
        user = db.query(User).filter(User.user_id == tarologist_id).first()
        if not user:
            return {}
        
        answered = db.query(TarotQuestion).filter(
            TarotQuestion.tarologist_id == tarologist_id,
            TarotQuestion.status == "answered"
        ).count()
        
        spreads = db.query(TarotSpread).filter(
            TarotSpread.tarologist_id == tarologist_id
        ).count()
        
        return {
            "username": user.username,
            "answered_questions": answered,
            "tarologist_spreads": spreads,
            "total_users_helped": db.query(func.distinct(TarotQuestion.user_id))
                .filter(TarotQuestion.tarologist_id == tarologist_id)
                .count()
        }

def get_bot_stats() -> Dict:
    """Получить статистику бота"""
    with get_db() as db:
        total_users = db.query(User).count()
        active_users = db.query(User).filter(
            User.last_activity >= datetime.now(MSK_TZ) - timedelta(days=7)
        ).count()
        total_spreads = db.query(TarotSpread).count()
        ai_spreads = db.query(TarotSpread).filter(TarotSpread.is_tarologist == False).count()
        tarologist_spreads = db.query(TarotSpread).filter(TarotSpread.is_tarologist == True).count()
        total_questions = db.query(TarotQuestion).count()
        pending_questions = db.query(TarotQuestion).filter(TarotQuestion.status == "pending").count()
        
        return {
            "total_users": total_users,
            "active_users": active_users,
            "total_spreads": total_spreads,
            "ai_spreads": ai_spreads,
            "tarologist_spreads": tarologist_spreads,
            "total_questions": total_questions,
            "pending_questions": pending_questions
        }

# ========== FSM СОСТОЯНИЯ ==========
class AdminStates(StatesGroup):
    waiting_for_ban_user_id = State()
    waiting_for_unban_user_id = State()
    waiting_for_make_admin_user_id = State()
    waiting_for_tarologist_user_id = State()
    waiting_for_remove_tarologist_user_id = State()
    waiting_for_remove_admin_user_id = State()
    viewing_user_info = State()
    sending_broadcast = State()

class TarotStates(StatesGroup):
    choosing_spread = State()
    asking_question = State()
    viewing_history = State()

class TarologistStates(StatesGroup):
    waiting_for_answer = State()
    viewing_questions = State()

# ========== КЛАВИАТУРЫ ==========
def get_main_keyboard(user_id: int):
    """Главная клавиатура"""
    builder = ReplyKeyboardBuilder()
    
    builder.button(text="🔮 Сделать расклад")
    builder.button(text="📜 История раскладов")
    builder.button(text="👤 Мой профиль")
    
    if is_tarologist(user_id):
        builder.button(text="🎴 Панель таролога")
    
    if is_admin(user_id):
        builder.button(text="⚙️ Админ панель")
    
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)

def get_spreads_keyboard():
    """Клавиатура выбора расклада"""
    builder = InlineKeyboardBuilder()
    
    spreads = [
        ("🎴 Одна карта", "spread_one_card"),
        ("🔮 3 карты", "spread_three_cards"),
        ("🧿 Кельтский крест", "spread_celtic_cross"),
        ("❓ Да/Нет", "spread_yes_no"),
        ("💖 Отношения", "spread_relationship"),
        ("💼 Карьера", "spread_career"),
        ("👨‍🔮 Вопрос тарологу", "ask_tarologist"),
        ("❌ Отмена", "cancel")
    ]
    
    for text, callback in spreads:
        builder.button(text=text, callback_data=callback)
    
    builder.adjust(2)
    return builder.as_markup()

def get_admin_keyboard():
    """Клавиатура админ-панели"""
    builder = InlineKeyboardBuilder()
    
    buttons = [
        ("👥 Все пользователи", "admin_users"),
        ("🔍 Поиск пользователя", "admin_search"),
        ("📊 Статистика бота", "admin_stats"),
        ("🚫 Забанить", "admin_ban"),
        ("✅ Разбанить", "admin_unban"),
        ("👑 Сделать админом", "admin_make_admin"),
        ("🗑️ Удалить админа", "admin_remove_admin"),
        ("🎴 Сделать тарологом", "admin_make_tarologist"),
        ("👋 Удалить таролога", "admin_remove_tarologist"),
        ("📢 Рассылка", "admin_broadcast"),
        ("❌ Закрыть", "admin_close")
    ]
    
    for text, callback in buttons:
        builder.button(text=text, callback_data=callback)
    
    builder.adjust(2)
    return builder.as_markup()

def get_tarologist_keyboard():
    """Клавиатура таролога"""
    builder = InlineKeyboardBuilder()
    
    buttons = [
        ("📥 Взять вопрос", "tarologist_take"),
        ("📤 Отправить ответ", "tarologist_answer"),
        ("📋 Мои вопросы", "tarologist_my_questions"),
        ("📊 Моя статистика", "tarologist_stats"),
        ("📈 Статистика очереди", "tarologist_queue_stats"),
        ("🏠 В главное меню", "tarologist_home")
    ]
    
    for text, callback in buttons:
        builder.button(text=text, callback_data=callback)
    
    builder.adjust(2)
    return builder.as_markup()

def get_history_keyboard(spreads: List[Dict], page: int = 0, page_size: int = 10):
    """Клавиатура истории раскладов"""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    
    start_idx = page * page_size
    end_idx = start_idx + page_size
    paginated_spreads = spreads[start_idx:end_idx]
    
    keyboard = []
    
    # 1. Добавляем расклады по 2 в ряд
    row = []
    for i, spread in enumerate(paginated_spreads):
        if i > 0 and i % 2 == 0:
            keyboard.append(row)
            row = []
        
        row.append(InlineKeyboardButton(
            text=f"📜 #{spread['id']} - {spread['preview'][:30]}...",
            callback_data=f"history_{spread['id']}"
        ))
    
    if row:  # Добавляем последнюю неполную строку
        keyboard.append(row)
    
    # 2. Добавляем пагинационные кнопки (если есть)
    pagination_row = []
    if page > 0:
        pagination_row.append(InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=f"history_page_{page-1}"
        ))
    
    if end_idx < len(spreads):
        pagination_row.append(InlineKeyboardButton(
            text="Вперед ➡️",
            callback_data=f"history_page_{page+1}"
        ))
    
    if pagination_row:
        keyboard.append(pagination_row)
    
    # 3. Добавляем кнопку "Назад" отдельной строкой
    keyboard.append([InlineKeyboardButton(
        text="🔙 Назад",
        callback_data="back_to_main"
    )])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_back_keyboard():
    """Клавиатура с кнопкой Назад"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data="back_to_main")
    return builder.as_markup()

def get_user_actions_keyboard(user_id: int):
    """Клавиатура действий с пользователем"""
    builder = InlineKeyboardBuilder()
    
    builder.button(text="👁️ Просмотр профиля", callback_data=f"admin_view_user_{user_id}")
    builder.button(text="🚫 Забанить", callback_data=f"admin_ban_user_{user_id}")
    builder.button(text="✅ Разбанить", callback_data=f"admin_unban_user_{user_id}")
    builder.button(text="👑 Сделать админом", callback_data=f"admin_make_admin_{user_id}")
    builder.button(text="🎴 Сделать тарологом", callback_data=f"admin_make_tarologist_{user_id}")
    builder.button(text="📜 История раскладов", callback_data=f"admin_user_history_{user_id}")
    
    builder.adjust(2)
    return builder.as_markup()

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========
from aiogram.client.default import DefaultBotProperties
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ========== ОБРАБОТЧИКИ КОМАНД ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Обработчик команды /start"""
    user_dict = await get_or_create_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    )
    
    welcome_text = (
        f"✨ <b>Добро пожаловать, {message.from_user.first_name}!</b>\n\n"
        "Я - бот для гадания на Таро с искусственным интеллектом GigaChat.\n\n"
        "<b>🔮 Доступные расклады:</b>\n"
        "• 🎴 <b>Одна карта</b> - быстрый ответ на вопрос\n"
        "• 🔮 <b>3 карты</b> - Прошлое, Настоящее, Будущее\n"
        "• 🧿 <b>Кельтский крест</b> - полный расклад на 10 карт\n"
        "• ❓ <b>Да/Нет</b> - прямой ответ на вопрос\n"
        "• 💖 <b>Отношения</b> - расклад на любовь и отношения\n"
        "• 💼 <b>Карьера</b> - профессиональный расклад\n\n"
        "<b>👨‍🔮 А также:</b>\n"
        "• Вопросы реальным тарологам\n"
        "• История всех ваших раскладов\n"
        "• Админ-панель (если вы администратор)\n\n"
        "Выберите действие в меню ниже 👇"
    )
    
    await message.answer(welcome_text, reply_markup=get_main_keyboard(message.from_user.id), parse_mode="HTML")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Обработчик команды /help"""
    help_text = (
        "<b>🆘 Помощь по командам:</b>\n\n"
        "<b>🔮 Гадание:</b>\n"
        "• <code>/spread</code> или кнопка 'Сделать расклад' - начать новый расклад\n"
        "• <code>/history</code> или кнопка 'История' - посмотреть прошлые расклады\n\n"
        "<b>👤 Профиль:</b>\n"
        "• <code>/profile</code> - информация о вашем профиле\n\n"
        "<b>⚙️ Администраторам:</b>\n"
        "• <code>/admin</code> - открыть админ-панель\n\n"
        "<b>🎴 Тарологам:</b>\n"
        "• <code>/tarologist</code> - панель таролога\n\n"
        "<b>📞 Поддержка:</b>\n"
        "Для связи с администратором используйте команду <code>/support</code>\n\n"
        "<i>Если возникли проблемы, попробуйте перезапустить бота /start</i>"
    )
    
    await message.answer(help_text, reply_markup=get_main_keyboard(message.from_user.id), parse_mode="HTML")

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    """Обработчик команды /admin"""
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав администратора.")
        return
    
    admin_text = (
        "<b>⚙️ Административная панель</b>\n\n"
        "Выберите действие:"
    )
    
    await message.answer(admin_text, reply_markup=get_admin_keyboard())

@dp.message(Command("tarologist"))
async def cmd_tarologist(message: types.Message):
    """Обработчик команды /tarologist"""
    if not is_tarologist(message.from_user.id):
        await message.answer("⛔ Вы не являетесь тарологом.")
        return
    
    tarologist_text = (
        "<b>🎴 Панель таролога</b>\n\n"
        "Здесь вы можете отвечать на вопросы пользователей.\n"
        "Выберите действие:"
    )
    
    await message.answer(tarologist_text, reply_markup=get_tarologist_keyboard())

@dp.message(Command("spread"))
async def cmd_spread(message: types.Message, state: FSMContext):
    """Обработчик команды /spread"""
    user_dict = await get_or_create_user(message.from_user.id)
    
    if user_dict.get('is_banned'):
        await message.answer("⛔ Ваш аккаунт заблокирован.")
        return
    
    await state.set_state(TarotStates.choosing_spread)
    
    spread_text = (
        "<b>🔮 Выберите тип расклада:</b>\n\n"
        "• 🎴 <b>Одна карта</b> - быстрый ответ на вопрос\n"
        "• 🔮 <b>3 карты</b> - Прошлое, Настоящее, Будущее\n"
        "• 🧿 <b>Кельтский крест</b> - полный анализ ситуации (10 карт)\n"
        "• ❓ <b>Да/Нет</b> - прямой ответ ДА или НЕТ\n"
        "• 💖 <b>Отношения</b> - расклад на любовь и отношения\n"
        "• 💼 <b>Карьера</b> - профессиональный расклад\n"
        "• 👨‍🔮 <b>Вопрос тарологу</b> - получите ответ от реального специалиста"
    )
    
    await message.answer(spread_text, reply_markup=get_spreads_keyboard())

@dp.message(Command("history"))
async def cmd_history(message: types.Message, state: FSMContext):
    """Обработчик команды /history"""
    await get_or_create_user(message.from_user.id)  # Обновляем активность
    
    spreads = get_user_spreads(message.from_user.id, limit=50)
    
    if not spreads:
        await message.answer("📭 У вас еще нет сохраненных раскладов.")
        return
    
    await state.set_state(TarotStates.viewing_history)
    await state.update_data(history_page=0, history_spreads=spreads)
    
    history_text = (
        f"<b>📜 История ваших раскладов</b>\n\n"
        f"Всего раскладов: {len(spreads)}\n\n"
        f"Выберите расклад для просмотра:"
    )
    
    await message.answer(history_text, reply_markup=get_history_keyboard(spreads, page=0))

@dp.message(Command("profile"))
async def cmd_profile(message: types.Message):
    """Обработчик команды /profile"""
    user_info = get_user_info(message.from_user.id)
    
    if not user_info:
        await message.answer("❌ Ошибка получения профиля.")
        return
    
    profile_text = (
        f"<b>👤 Ваш профиль</b>\n\n"
        f"<b>🆔 ID:</b> {user_info['user_id']}\n"
        f"<b>👤 Имя:</b> {user_info['first_name']} {user_info['last_name'] or ''}\n"
        f"<b>📛 Ник:</b> @{user_info['username'] or 'Нет'}\n"
        f"<b>📅 Регистрация:</b> {user_info['created_at']}\n"
        f"<b>🕐 Последняя активность:</b> {user_info['last_activity']}\n\n"
        f"<b>📊 Статистика:</b>\n"
        f"• Всего раскладов: <b>{user_info['total_spreads']}</b>\n"
        f"• ИИ-раскладов: <b>{user_info['ai_spreads']}</b>\n"
        f"• Раскладов от тарологов: <b>{user_info['tarologist_spreads']}</b>\n"
        f"• Заданных вопросов: <b>{user_info['questions_asked']}</b>\n\n"
        f"<b>⚡ Статусы:</b>\n"
        f"• {'✅ Администратор' if user_info['is_admin'] else '❌ Пользователь'}\n"
        f"• {'🎴 Таролог' if user_info['is_tarologist'] else '❌ Не таролог'}\n"
        f"• {'🚫 Заблокирован' if user_info['is_banned'] else '✅ Активен'}\n\n"
        f"<b>💰 Баланс:</b> {user_info['balance']} руб."
    )
    
    await message.answer(profile_text, reply_markup=get_main_keyboard(message.from_user.id), parse_mode="HTML")

@dp.message(Command("support"))
async def cmd_support(message: types.Message):
    """Обработчик команды /support"""
    support_text = (
        "<b>📞 Поддержка</b>\n\n"
        "<i>Если у вас возникли проблемы:</i>\n"
        "1. Попробуйте перезапустить бота командой <code>/start</code>\n"
        "2. Проверьте, что вы правильно формулируете вопросы\n"
        "3. Если проблема не решена, свяжитесь с администратором\n\n"
        "<i>Для администраторов доступна команда <code>/admin</code></i>"
    )
    
    await message.answer(support_text, reply_markup=get_main_keyboard(message.from_user.id), parse_mode="HTML")

# ========== ОБРАБОТЧИКИ КНОПОК ГЛАВНОГО МЕНЮ ==========
@dp.message(lambda message: message.text == "🔮 Сделать расклад")
async def btn_spread(message: types.Message, state: FSMContext):
    """Кнопка 'Сделать расклад'"""
    await cmd_spread(message, state)

@dp.message(lambda message: message.text == "📜 История раскладов")
async def btn_history(message: types.Message, state: FSMContext):
    """Кнопка 'История раскладов'"""
    await cmd_history(message, state)

@dp.message(lambda message: message.text == "👤 Мой профиль")
async def btn_profile(message: types.Message):
    """Кнопка 'Мой профиль'"""
    await cmd_profile(message)

@dp.message(lambda message: message.text == "🎴 Панель таролога")
async def btn_tarologist_panel(message: types.Message):
    """Кнопка 'Панель таролога'"""
    await cmd_tarologist(message)

@dp.message(lambda message: message.text == "⚙️ Админ панель")
async def btn_admin_panel(message: types.Message):
    """Кнопка 'Админ панель'"""
    await cmd_admin(message)

# ========== ОБРАБОТЧИКИ РАСКЛАДОВ ==========
@dp.callback_query(lambda c: c.data.startswith('spread_') or c.data in ['ask_tarologist', 'cancel'])
async def process_spread_choice(callback: types.CallbackQuery, state: FSMContext):
    """Обработчик выбора типа расклада"""
    if callback.data == 'cancel':
        await state.clear()
        await callback.message.edit_text("❌ Действие отменено.")
        await callback.message.answer(
            "Возвращаемся в главное меню...",
            reply_markup=get_main_keyboard(callback.from_user.id)
        )
        await callback.answer()
        return
    
    user_dict = await get_or_create_user(callback.from_user.id)
    
    if user_dict.get('is_banned'):
        await callback.message.edit_text("⛔ Ваш аккаунт заблокирован.")
        await callback.answer()
        return
    
    if callback.data == 'ask_tarologist':
        await state.set_state(TarotStates.asking_question)
        await state.update_data(spread_type="tarologist")
        
        await callback.message.edit_text(
            "<b>👨‍🔮 Вопрос тарологу</b>\n\n"
            "Опишите вашу ситуацию или задайте вопрос тарологу.\n"
            "<i>Постарайтесь быть максимально конкретными для точного ответа.</i>\n"
            "<i>Таролог ответит вам в течение 24 часов.</i>\n\n"
            "<b>Напишите ваш вопрос:</b>"
        )
    else:
        spread_type_map = {
            'spread_one_card': 'one_card',
            'spread_three_cards': 'three_cards',
            'spread_celtic_cross': 'celtic_cross',
            'spread_yes_no': 'yes_no',
            'spread_relationship': 'relationship',
            'spread_career': 'career'
        }
        
        spread_type = spread_type_map.get(callback.data, 'one_card')
        await state.set_state(TarotStates.asking_question)
        await state.update_data(spread_type=spread_type)
        
        spread_names = {
            'one_card': "Одна карта",
            'three_cards': "3 карты (Прошлое-Настоящее-Будущее)",
            'celtic_cross': "Кельтский крест (10 карт)",
            'yes_no': "Расклад Да/Нет",
            'relationship': "Расклад на отношения",
            'career': "Карьерный расклад"
        }
        
        await callback.message.edit_text(
            f"<b>🔮 Вы выбрали: {spread_names[spread_type]}</b>\n\n"
            "Теперь сформулируйте ваш вопрос или опишите ситуацию.\n"
            "<i>Чем конкретнее вопрос, тем точнее будет ответ.</i>\n\n"
            "<b>Напишите ваш вопрос:</b>"
        )
    
    await callback.answer()

@dp.message(TarotStates.asking_question)
async def process_spread_question(message: types.Message, state: FSMContext):
    """Обработчик вопроса для расклада"""
    user_data = await state.get_data()
    spread_type = user_data.get('spread_type')
    question = message.text
    
    if spread_type == "tarologist":
        # Создание вопроса для таролога
        question_id = create_tarot_question(message.from_user.id, question)
        
        await message.answer(
            "✅ <b>Ваш вопрос отправлен тарологам!</b>\n\n"
            "<i>Как только один из тарологов возьмет ваш вопрос, "
            "вы получите уведомление с ответом.</i>\n\n"
            "⏳ Обычно ответ приходит в течение 24 часов.\n"
            "📜 Вы можете отслеживать статус в истории раскладов.",
            parse_mode="HTML"
        )
        
        # Уведомляем тарологов о новом вопросе
        with get_db() as db:
            tarologists = db.query(User).filter(
                User.is_tarologist == True,
                User.is_banned == False
            ).all()
            
            for tarologist in tarologists:
                try:
                    await bot.send_message(
                        tarologist.user_id,
                        f"📥 <b>Новый вопрос от пользователя!</b>\n\n"
                        f"👤 Пользователь: @{message.from_user.username or 'Без ника'}\n"
                        f"❓ Вопрос: {question[:200]}...\n\n"
                        f"<i>Используйте панель таролога, чтобы взять вопрос.</i>",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"Failed to notify tarologist {tarologist.user_id}: {e}")
    
    else:
        # Используем глобальный экземпляр GigaChat клиента
        processing_msg = None
        try:
            processing_msg = await message.answer("🔄 Карты тасуются... Пожалуйста, подождите ⏳")
            
            # Отправляем запрос в очередь GigaChat
            interpretation = await giga_client.generate_spread(spread_type, question)
            
            # Сохранение в базу
            spread_id = save_spread(
                user_id=message.from_user.id,
                spread_type=spread_type,
                question=question,
                interpretation=interpretation
            )
            
            # Удаляем сообщение "Карты тасуются..."
            if processing_msg:
                try:
                    await processing_msg.delete()
                except:
                    pass
            
            # Отправка результата
            result_text = (
                f"✨ <b>Ваш расклад готов!</b>\n\n"
                f"{interpretation}\n\n"
                f"📌 <b>Запись сохранена в истории</b>\n"
                f"ID расклада: <code>#{spread_id}</code>\n"
                f"Тип расклада: {spread_type}"
            )
            
            # Разбиваем длинные сообщения
            if len(result_text) > 4000:
                parts = [result_text[i:i+4000] for i in range(0, len(result_text), 4000)]
                for part in parts:
                    await message.answer(part, parse_mode="HTML")
            else:
                await message.answer(result_text, parse_mode="HTML")
            
            await message.answer(
                "📜 Чтобы посмотреть историю раскладов, нажмите /history\n"
                "🔮 Для нового расклада - /spread",
                reply_markup=get_main_keyboard(message.from_user.id)
            )
            
        except asyncio.TimeoutError:
            if processing_msg:
                try:
                    await processing_msg.delete()
                except:
                    pass
            
            await message.answer(
                "⏳ <b>Превышено время ожидания ответа от карт.</b>\n\n"
                "<i>Сервер карт перегружен. Пожалуйста, попробуйте через несколько минут.</i>",
                parse_mode="HTML",
                reply_markup=get_main_keyboard(message.from_user.id)
            )
            
        except Exception as e:
            if processing_msg:
                try:
                    await processing_msg.delete()
                except:
                    pass
            
            logger.error(f"Error generating spread: {e}")
            await message.answer(
                "❌ Произошла ошибка при генерации расклада.\n"
                "Пожалуйста, попробуйте еще раз позже.",
                reply_markup=get_main_keyboard(message.from_user.id)
            )
    
    await state.clear()


def clean_html_tags(text: str) -> str:
    """Очищает текст от незакрытых HTML тегов"""
    import re
    
    # Проверяем парность тегов
    tags_to_check = ['b', 'i', 'u', 'code', 'pre', 'a', 's', 'strike']
    
    for tag in tags_to_check:
        open_pattern = f'<{tag}[^>]*>'
        close_pattern = f'</{tag}>'
        
        open_count = len(re.findall(open_pattern, text, re.IGNORECASE))
        close_count = len(re.findall(close_pattern, text, re.IGNORECASE))
        
        # Если теги не сбалансированы, удаляем все вхождения этого тега
        if open_count != close_count:
            # Удаляем открывающие теги
            text = re.sub(open_pattern, '', text, flags=re.IGNORECASE)
            # Удаляем закрывающие теги
            text = re.sub(close_pattern, '', text, flags=re.IGNORECASE)
    
    # Также проверяем незакрытые теги в целом
    stack = []
    result = []
    i = 0
    
    while i < len(text):
        if text[i] == '<' and i + 1 < len(text):
            # Нашли начало тега
            j = text.find('>', i)
            if j != -1:
                tag = text[i:j+1]
                
                # Проверяем, закрывающий ли это тег
                if tag.startswith('</'):
                    if stack:
                        stack.pop()
                    result.append(tag)
                elif tag.endswith('/>'):
                    # Самозакрывающийся тег
                    result.append(tag)
                elif not any(tag.startswith(f'<{x} ') for x in ['a href', 'img', 'br', 'hr']):
                    # Открывающий тег (но не особые теги)
                    result.append(tag)
                    stack.append(tag.split()[0].strip('<>'))
                else:
                    result.append(tag)
                i = j + 1
                continue
        
        result.append(text[i])
        i += 1
    
    # Если остались незакрытые теги, закрываем их
    while stack:
        tag = stack.pop()
        result.append(f'</{tag}>')
    
    return ''.join(result)


# Альтернативная, более простая функция очистки
def sanitize_html(text: str) -> str:
    """Безопасная очистка HTML тегов"""
    import html
    
    # Экранируем спецсимволы
    text = html.escape(text)
    
    # Разрешаем только безопасные теги
    allowed_tags = {
        'b': ['<b>', '</b>'],
        'i': ['<i>', '</i>'],
        'u': ['<u>', '</u>'],
        'code': ['<code>', '</code>'],
        'pre': ['<pre>', '</pre>']
    }
    
    # Заменяем безопасные теги обратно
    for tag, (open_tag, close_tag) in allowed_tags.items():
        text = text.replace(f'&lt;{tag}&gt;', open_tag)
        text = text.replace(f'&lt;/{tag}&gt;', close_tag)
    
    return text

# ========== ОБРАБОТЧИКИ ИСТОРИИ ==========
@dp.callback_query(lambda c: c.data.startswith('history_'))
async def process_history_item(callback: types.CallbackQuery, state: FSMContext):
    """Обработчик выбора расклада из истории"""
    if callback.data.startswith('history_page_'):
        # Пагинация истории
        page = int(callback.data.split('_')[-1])
        user_data = await state.get_data()
        spreads = user_data.get('history_spreads', [])
        
        await state.update_data(history_page=page)
        
        history_text = (
            f"<b>📜 История ваших раскладов</b>\n\n"
            f"Всего раскладов: {len(spreads)}\n"
            f"Страница {page + 1}\n\n"
            f"Выберите расклад для просмотра:"
        )
        
        await callback.message.edit_text(
            history_text,
            reply_markup=get_history_keyboard(spreads, page=page)
        )
        await callback.answer()
        return
    
    spread_id = int(callback.data.split('_')[1])
    
    spread = get_spread_by_id(spread_id)
    
    if not spread or spread['user_id'] != callback.from_user.id:
        await callback.answer("❌ Расклад не найден или у вас нет доступа.")
        return
    
    spread_type_names = {
        'one_card': '🎴 Одна карта',
        'three_cards': '🔮 3 карты',
        'celtic_cross': '🧿 Кельтский крест',
        'yes_no': '❓ Да/Нет',
        'relationship': '💖 Отношения',
        'career': '💼 Карьера',
        'tarologist_answer': '👨‍🔮 Ответ таролога'
    }
    
    tarologist_info = ""
    if spread['is_tarologist'] and spread['tarologist_id']:
        tarologist_user = get_user_info(spread['tarologist_id'])
        if tarologist_user:
            tarologist_info = f"👨‍🔮 Таролог: @{tarologist_user['username'] or tarologist_user['user_id']}\n"
    
    history_text = (
        f"<b>📜 Расклад #{spread['id']}</b>\n\n"
        f"<b>📅 Дата:</b> {spread['created_at']}\n"
        f"<b>🎴 Тип:</b> {spread_type_names.get(spread['spread_type'], spread['spread_type'])}\n"
        f"<b>👤 Ответил:</b> {'👨‍🔮 Таролог' if spread['is_tarologist'] else '🤖 ИИ'}\n"
        f"{tarologist_info}\n"
        f"<b>❓ Вопрос:</b>\n{spread['question'] or 'Без вопроса'}\n\n"
        f"<b>✨ Трактовка:</b>\n{spread['interpretation']}"
    )
    
    # Разбиваем длинные сообщения
    if len(history_text) > 4000:
        parts = [history_text[i:i+4000] for i in range(0, len(history_text), 4000)]
        for part in parts:
            await callback.message.answer(part, parse_mode="HTML")
    else:
        await callback.message.answer(history_text, parse_mode="HTML")
    
    await callback.answer()

@dp.callback_query(lambda c: c.data == 'back_to_main')
async def back_to_main(callback: types.CallbackQuery, state: FSMContext):
    """Кнопка 'Назад' в главное меню"""
    await state.clear()
    await callback.message.edit_text(
        "Возвращаемся в главное меню...",
        reply_markup=None
    )
    
    welcome_text = (
        f"✨ <b>С возвращением, {callback.from_user.first_name}!</b>\n\n"
        "Выберите действие в меню:"
    )
    
    await callback.message.answer(
        welcome_text,
        reply_markup=get_main_keyboard(callback.from_user.id),
        parse_mode="HTML"
    )
    await callback.answer()

# ========== ОБРАБОТЧИКИ АДМИН-ПАНЕЛИ ==========
@dp.callback_query(lambda c: c.data.startswith('admin_'))
async def process_admin_action(callback: types.CallbackQuery, state: FSMContext):
    """Обработчик действий админ-панели"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ У вас нет прав администратора.")
        return
    
    action = callback.data
    
    if action == 'admin_users':
        users = get_all_users(50)
        
        if not users:
            await callback.message.edit_text("📭 Нет пользователей в базе.")
            await callback.answer()
            return
        
        users_text = "<b>👥 Последние пользователи:</b>\n\n"
        
        for i, user in enumerate(users[:15], 1):
            status = ""
            if user['is_banned']:
                status = "🚫"
            elif user['is_admin']:
                status = "👑"
            elif user['is_tarologist']:
                status = "🎴"
            else:
                status = "👤"
            
            users_text += (
                f"{i}. {status} <b>ID:</b> {user['user_id']}\n"
                f"   👤 @{user['username'] or 'Без ника'}\n"
                f"   📛 {user['first_name']} {user['last_name'] or ''}\n"
                f"   📅 {user['created_at']}\n\n"
            )
        
        users_text += f"<b>Всего пользователей:</b> {len(users)}"
        
        await callback.message.edit_text(users_text, parse_mode="HTML")
        
    elif action == 'admin_search':
        await callback.message.edit_text(
            "<b>🔍 Поиск пользователя</b>\n\n"
            "Отправьте мне username, имя или ID пользователя для поиска.\n"
            "<i>Например: @username, Иван, или 123456789</i>"
        )
        await state.set_state(AdminStates.viewing_user_info)
        
    elif action == 'admin_stats':
        stats = get_bot_stats()
        queue_stats = await tarot_queue.get_stats()
        
        stats_text = (
            "<b>📊 Статистика бота:</b>\n\n"
            f"<b>👥 Всего пользователей:</b> {stats['total_users']}\n"
            f"<b>🟢 Активных (7 дней):</b> {stats['active_users']}\n"
            f"<b>🔮 Всего раскладов:</b> {stats['total_spreads']}\n"
            f"<b>🤖 ИИ-раскладов:</b> {stats['ai_spreads']}\n"
            f"<b>👨‍🔮 Раскладов от тарологов:</b> {stats['tarologist_spreads']}\n"
            f"<b>❓ Всего вопросов тарологам:</b> {stats['total_questions']}\n"
            f"<b>⏳ Ожидающих вопросов:</b> {stats['pending_questions']}\n\n"
            f"<b>📋 Очередь тарологов:</b>\n"
            f"• Ожидают: <b>{queue_stats['pending']}</b>\n"
            f"• В работе: <b>{queue_stats['assigned']}</b>\n"
            f"• Активных тарологов: <b>{queue_stats['active_tarologists']}</b>"
        )
        
        await callback.message.edit_text(stats_text, parse_mode="HTML")
    
    elif action == 'admin_ban':
        await state.set_state(AdminStates.waiting_for_ban_user_id)
        await callback.message.edit_text(
            "<b>🚫 Бан пользователя</b>\n\n"
            "Отправьте мне ID пользователя, которого нужно забанить.\n\n"
            "<i>Для отмены отправьте /cancel</i>"
        )
    
    elif action == 'admin_unban':
        await state.set_state(AdminStates.waiting_for_unban_user_id)
        await callback.message.edit_text(
            "<b>✅ Разбан пользователя</b>\n\n"
            "Отправьте мне ID пользователя, которого нужно разбанить.\n\n"
            "<i>Для отмены отправьте /cancel</i>"
        )
    
    elif action == 'admin_make_admin':
        await state.set_state(AdminStates.waiting_for_make_admin_user_id)
        await callback.message.edit_text(
            "<b>👑 Назначение администратора</b>\n\n"
            "Отправьте мне ID пользователя, которого нужно сделать администратором.\n\n"
            "<i>Для отмены отправьте /cancel</i>"
        )
    
    elif action == 'admin_remove_admin':
        await state.set_state(AdminStates.waiting_for_remove_admin_user_id)
        await callback.message.edit_text(
            "<b>🗑️ Удаление администратора</b>\n\n"
            "Отправьте мне ID администратора, которого нужно удалить.\n\n"
            "<i>Для отмены отправьте /cancel</i>"
        )
    
    elif action == 'admin_make_tarologist':
        await state.set_state(AdminStates.waiting_for_tarologist_user_id)
        await callback.message.edit_text(
            "<b>🎴 Назначение таролога</b>\n\n"
            "Отправьте мне ID пользователя, которого нужно сделать тарологом.\n\n"
            "<i>Для отмены отправьте /cancel</i>"
        )
    
    elif action == 'admin_remove_tarologist':
        await state.set_state(AdminStates.waiting_for_remove_tarologist_user_id)
        await callback.message.edit_text(
            "<b>👋 Удаление таролога</b>\n\n"
            "Отправьте мне ID таролога, которого нужно удалить.\n\n"
            "<i>Для отмены отправьте /cancel</i>"
        )
    
    elif action == 'admin_broadcast':
        await state.set_state(AdminStates.sending_broadcast)
        await callback.message.edit_text(
            "<b>📢 Рассылка сообщения</b>\n\n"
            "Отправьте мне сообщение для рассылки всем пользователям.\n\n"
            "<i>Для отмены отправьте /cancel</i>"
        )
    
    elif action == 'admin_close':
        await state.clear()
        await callback.message.edit_text("✅ Админ-панель закрыта.", reply_markup=None)
        await callback.message.answer(
            "Возвращаемся в главное меню...",
            reply_markup=get_main_keyboard(callback.from_user.id)
        )
    
    elif action.startswith('admin_view_user_'):
        user_id = int(action.split('_')[-1])
        await view_user_info(callback, user_id)
    
    elif action.startswith('admin_ban_user_'):
        user_id = int(action.split('_')[-1])
        await ban_user_by_id(callback, user_id)
    
    elif action.startswith('admin_unban_user_'):
        user_id = int(action.split('_')[-1])
        await unban_user_by_id(callback, user_id)
    
    elif action.startswith('admin_make_admin_user_'):
        user_id = int(action.split('_')[-1])
        await make_admin_by_id(callback, user_id)
    
    elif action.startswith('admin_make_tarologist_user_'):
        user_id = int(action.split('_')[-1])
        await make_tarologist_by_id(callback, user_id)
    
    elif action.startswith('admin_user_history_'):
        user_id = int(action.split('_')[-1])
        await view_user_history(callback, user_id)
    
    await callback.answer()

async def view_user_info(callback: types.CallbackQuery, user_id: int):
    """Показать информацию о пользователе"""
    user_info = get_user_info(user_id)
    
    if not user_info:
        await callback.answer("❌ Пользователь не найден.")
        return
    
    user_text = (
        f"<b>👤 Информация о пользователе</b>\n\n"
        f"<b>🆔 ID:</b> {user_info['user_id']}\n"
        f"<b>📛 Ник:</b> @{user_info['username'] or 'Нет'}\n"
        f"<b>👤 Имя:</b> {user_info['first_name']} {user_info['last_name'] or ''}\n"
        f"<b>📅 Регистрация:</b> {user_info['created_at']}\n"
        f"<b>🕐 Последняя активность:</b> {user_info['last_activity']}\n\n"
        f"<b>📊 Статистика:</b>\n"
        f"• Всего раскладов: <b>{user_info['total_spreads']}</b>\n"
        f"• ИИ-раскладов: <b>{user_info['ai_spreads']}</b>\n"
        f"• Раскладов от тарологов: <b>{user_info['tarologist_spreads']}</b>\n"
        f"• Заданных вопросов: <b>{user_info['questions_asked']}</b>\n\n"
        f"<b>⚡ Статусы:</b>\n"
        f"• {'✅ Администратор' if user_info['is_admin'] else '❌ Пользователь'}\n"
        f"• {'🎴 Таролог' if user_info['is_tarologist'] else '❌ Не таролог'}\n"
        f"• {'🚫 Заблокирован' if user_info['is_banned'] else '✅ Активен'}"
    )
    
    await callback.message.edit_text(user_text, reply_markup=get_user_actions_keyboard(user_info['user_id']), parse_mode="HTML")
    await callback.answer()

async def ban_user_by_id(callback: types.CallbackQuery, user_id: int):
    """Забанить пользователя по ID"""
    with get_db() as db:
        user = db.query(User).filter(User.user_id == user_id).first()
        
        if not user:
            await callback.answer("❌ Пользователь не найден.")
            return
        
        if user.is_admin:
            await callback.answer("⚠️ Нельзя забанить администратора.")
            return
        
        user.is_banned = True
        db.commit()
        
        try:
            await bot.send_message(
                user_id,
                "<b>⛔ Ваш аккаунт был заблокирован администратором.</b>\n"
                "<i>По вопросам обращайтесь к администрации.</i>",
                parse_mode="HTML"
            )
        except:
            pass
        
        await callback.message.edit_text(
            f"✅ Пользователь @{user.username or user_id} забанен.",
            reply_markup=get_user_actions_keyboard(user_id)
        )
    await callback.answer()

async def unban_user_by_id(callback: types.CallbackQuery, user_id: int):
    """Разбанить пользователя по ID"""
    with get_db() as db:
        user = db.query(User).filter(User.user_id == user_id).first()
        
        if not user:
            await callback.answer("❌ Пользователь не найден.")
            return
        
        user.is_banned = False
        db.commit()
        
        try:
            await bot.send_message(
                user_id,
                "<b>✅ Ваш аккаунт был разблокирован администратором.</b>\n"
                "<i>Добро пожаловать обратно!</i>",
                parse_mode="HTML"
            )
        except:
            pass
        
        await callback.message.edit_text(
            f"✅ Пользователь @{user.username or user_id} разбанен.",
            reply_markup=get_user_actions_keyboard(user_id)
        )
    await callback.answer()

async def make_admin_by_id(callback: types.CallbackQuery, user_id: int):
    """Сделать пользователя администратором"""
    with get_db() as db:
        user = db.query(User).filter(User.user_id == user_id).first()
        
        if not user:
            await callback.answer("❌ Пользователь не найден.")
            return
        
        user.is_admin = True
        db.commit()
        
        try:
            await bot.send_message(
                user_id,
                "<b>👑 Вы были назначены администратором бота!</b>\n"
                "<i>Используйте /admin для доступа к панели управления.</i>",
                parse_mode="HTML"
            )
        except:
            pass
        
        await callback.message.edit_text(
            f"✅ Пользователь @{user.username or user_id} назначен администратором.",
            reply_markup=get_user_actions_keyboard(user_id)
        )
    await callback.answer()

async def make_tarologist_by_id(callback: types.CallbackQuery, user_id: int):
    """Сделать пользователя тарологом"""
    with get_db() as db:
        user = db.query(User).filter(User.user_id == user_id).first()
        
        if not user:
            await callback.answer("❌ Пользователь не найден.")
            return
        
        user.is_tarologist = True
        db.commit()
        
        try:
            await bot.send_message(
                user_id,
                "<b>🎴 Вы были назначены тарологом бота!</b>\n"
                "<i>Теперь вы можете отвечать на вопросы пользователей.</i>\n"
                "<i>Используйте /tarologist для доступа к панели.</i>",
                parse_mode="HTML"
            )
        except:
            pass
        
        await callback.message.edit_text(
            f"✅ Пользователь @{user.username or user_id} назначен тарологом.",
            reply_markup=get_user_actions_keyboard(user_id)
        )
    await callback.answer()

async def view_user_history(callback: types.CallbackQuery, user_id: int):
    """Посмотреть историю раскладов пользователя"""
    spreads = get_user_spreads(user_id, limit=20)
    
    if not spreads:
        await callback.answer("📭 У пользователя нет раскладов.")
        return
    
    user_info = get_user_info(user_id)
    username = f"@{user_info['username']}" if user_info and user_info['username'] else f"ID: {user_id}"
    
    history_text = (
        f"<b>📜 История раскладов пользователя {username}</b>\n\n"
        f"Всего раскладов: {len(spreads)}\n\n"
        "<i>Последние расклады:</i>"
    )

    builder = InlineKeyboardBuilder()
    for spread in spreads[:10]:
        builder.button(
            text=f"#{spread['id']} - {spread['spread_type']}",
            callback_data=f"admin_spread_{spread['id']}"
        )
    
    builder.button(text="🔙 Назад", callback_data=f"admin_view_user_{user_id}")
    builder.adjust(1)
    
    await callback.message.edit_text(history_text, reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith('admin_spread_'))
async def view_admin_spread(callback: types.CallbackQuery):
    """Просмотр расклада администратором"""
    spread_id = int(callback.data.split('_')[-1])
    
    spread = get_spread_by_id(spread_id)
    
    if not spread:
        await callback.answer("❌ Расклад не найден.")
        return
    
    spread_type_names = {
        'one_card': '🎴 Одна карта',
        'three_cards': '🔮 3 карты',
        'celtic_cross': '🧿 Кельтский крест',
        'yes_no': '❓ Да/Нет',
        'relationship': '💖 Отношения',
        'career': '💼 Карьера',
        'tarologist_answer': '👨‍🔮 Ответ таролога'
    }
    
    tarologist_info = ""
    if spread['is_tarologist'] and spread['tarologist_id']:
        tarologist_user = get_user_info(spread['tarologist_id'])
        if tarologist_user:
            tarologist_info = f"👨‍🔮 Таролог: @{tarologist_user['username'] or tarologist_user['user_id']}\n"
    
    history_text = (
        f"<b>📜 Расклад #{spread['id']}</b>\n\n"
        f"<b>👤 Пользователь ID:</b> {spread['user_id']}\n"
        f"<b>📅 Дата:</b> {spread['created_at']}\n"
        f"<b>🎴 Тип:</b> {spread_type_names.get(spread['spread_type'], spread['spread_type'])}\n"
        f"<b>👤 Ответил:</b> {'👨‍🔮 Таролог' if spread['is_tarologist'] else '🤖 ИИ'}\n"
        f"{tarologist_info}\n"
        f"<b>❓ Вопрос:</b>\n{spread['question'] or 'Без вопроса'}\n\n"
        f"<b>✨ Трактовка:</b>\n{spread['interpretation'][:1000]}..."
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Назад", callback_data=f"admin_user_history_{spread['user_id']}")
    
    await callback.message.edit_text(history_text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()

@dp.message(AdminStates.viewing_user_info)
async def process_user_search(message: types.Message, state: FSMContext):
    """Обработчик поиска пользователя"""
    if message.text == '/cancel':
        await state.clear()
        await message.answer("❌ Поиск отменен.")
        await message.answer("Выберите действие:", reply_markup=get_admin_keyboard())
        return
    
    search_term = message.text.strip()
    users = search_users(search_term)
    
    if not users:
        await message.answer(f"❌ Пользователи по запросу '{search_term}' не найдены.")
        return
    
    if len(users) == 1:
        user_info = users[0]
        user_text = (
            f"<b>👤 Информация о пользователе</b>\n\n"
            f"<b>🆔 ID:</b> {user_info['user_id']}\n"
            f"<b>📛 Ник:</b> @{user_info['username'] or 'Нет'}\n"
            f"<b>👤 Имя:</b> {user_info['first_name']} {user_info['last_name'] or ''}\n"
            f"<b>📅 Регистрация:</b> {user_info['created_at']}\n"
            f"<b>🕐 Последняя активность:</b> {user_info['last_activity']}\n\n"
            f"<b>⚡ Статусы:</b>\n"
            f"• {'✅ Администратор' if user_info['is_admin'] else '❌ Пользователь'}\n"
            f"• {'🎴 Таролог' if user_info['is_tarologist'] else '❌ Не таролог'}\n"
            f"• {'🚫 Заблокирован' if user_info['is_banned'] else '✅ Активен'}"
        )
        
        await message.answer(user_text, reply_markup=get_user_actions_keyboard(user_info['user_id']), parse_mode="HTML")
    else:
        builder = InlineKeyboardBuilder()
        for user in users[:10]:
            builder.button(
                text=f"👤 {user['user_id']} - @{user['username'] or 'нет ника'}",
                callback_data=f"admin_view_user_{user['user_id']}"
            )
        builder.adjust(1)
        
        await message.answer(
            f"<b>🔍 Найдено пользователей: {len(users)}</b>\n\n"
            "<i>Выберите пользователя:</i>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )

@dp.message(AdminStates.waiting_for_ban_user_id)
async def process_ban_user(message: types.Message, state: FSMContext):
    """Обработчик бана пользователя (текстовый ввод)"""
    if message.text == '/cancel':
        await state.clear()
        await message.answer("❌ Бан отменен.")
        await message.answer("Выберите действие:", reply_markup=get_admin_keyboard())
        return
    
    try:
        user_id = int(message.text)
        
        with get_db() as db:
            user = db.query(User).filter(User.user_id == user_id).first()
            
            if not user:
                await message.answer("❌ Пользователь не найден.")
                return
            
            if user.is_admin:
                await message.answer("⚠️ Нельзя забанить администратора.")
                return
            
            user.is_banned = True
            db.commit()
            
            try:
                await bot.send_message(
                    user_id,
                    "<b>⛔ Ваш аккаунт был заблокирован администратором.</b>\n"
                    "<i>По вопросам обращайтесь к администрации.</i>",
                    parse_mode="HTML"
                )
            except:
                pass
            
            await message.answer(f"✅ Пользователь @{user.username or user_id} забанен.")
    except ValueError:
        await message.answer("❌ Неверный формат ID. Отправьте числовой ID.")
    
    await state.clear()
    await message.answer("Выберите действие:", reply_markup=get_admin_keyboard())

@dp.message(AdminStates.waiting_for_unban_user_id)
async def process_unban_user(message: types.Message, state: FSMContext):
    """Обработчик разбана пользователя"""
    if message.text == '/cancel':
        await state.clear()
        await message.answer("❌ Разбан отменен.")
        await message.answer("Выберите действие:", reply_markup=get_admin_keyboard())
        return
    
    try:
        user_id = int(message.text)
        
        with get_db() as db:
            user = db.query(User).filter(User.user_id == user_id).first()
            
            if not user:
                await message.answer("❌ Пользователь не найден.")
                return
            
            user.is_banned = False
            db.commit()
            
            try:
                await bot.send_message(
                    user_id,
                    "<b>✅ Ваш аккаунт был разблокирован администратором.</b>\n"
                    "<i>Добро пожаловать обратно!</i>",
                    parse_mode="HTML"
                )
            except:
                pass
            
            await message.answer(f"✅ Пользователь @{user.username or user_id} разбанен.")
    except ValueError:
        await message.answer("❌ Неверный формат ID. Отправьте числовой ID.")
    
    await state.clear()
    await message.answer("Выберите действие:", reply_markup=get_admin_keyboard())

@dp.message(AdminStates.waiting_for_make_admin_user_id)
async def process_make_admin(message: types.Message, state: FSMContext):
    """Обработчик назначения администратора"""
    if message.text == '/cancel':
        await state.clear()
        await message.answer("❌ Назначение отменено.")
        await message.answer("Выберите действие:", reply_markup=get_admin_keyboard())
        return
    
    try:
        user_id = int(message.text)
        
        with get_db() as db:
            user = db.query(User).filter(User.user_id == user_id).first()
            
            if not user:
                await message.answer("❌ Пользователь не найден.")
                return
            
            user.is_admin = True
            db.commit()
            
            try:
                await bot.send_message(
                    user_id,
                    "<b>👑 Вы были назначены администратором бота!</b>\n"
                    "<i>Используйте /admin для доступа к панели управления.</i>",
                    parse_mode="HTML"
                )
            except:
                pass
            
            await message.answer(f"✅ Пользователь @{user.username or user_id} назначен администратором.")
    except ValueError:
        await message.answer("❌ Неверный формат ID. Отправьте числовой ID.")
    
    await state.clear()
    await message.answer("Выберите действие:", reply_markup=get_admin_keyboard())

@dp.message(AdminStates.waiting_for_remove_admin_user_id)
async def process_remove_admin(message: types.Message, state: FSMContext):
    """Обработчик удаления администратора"""
    if message.text == '/cancel':
        await state.clear()
        await message.answer("❌ Удаление отменено.")
        await message.answer("Выберите действие:", reply_markup=get_admin_keyboard())
        return
    
    try:
        user_id = int(message.text)
        
        with get_db() as db:
            user = db.query(User).filter(User.user_id == user_id).first()
            
            if not user:
                await message.answer("❌ Пользователь не найден.")
                return
            
            if user.user_id == message.from_user.id:
                await message.answer("⚠️ Вы не можете удалить сами себя.")
                return
            
            user.is_admin = False
            db.commit()
            
            try:
                await bot.send_message(
                    user_id,
                    "<b>👋 Вы были удалены из администраторов бота.</b>",
                    parse_mode="HTML"
                )
            except:
                pass
            
            await message.answer(f"✅ Пользователь @{user.username or user_id} удален из администраторов.")
    except ValueError:
        await message.answer("❌ Неверный формат ID. Отправьте числовой ID.")
    
    await state.clear()
    await message.answer("Выберите действие:", reply_markup=get_admin_keyboard())

@dp.message(AdminStates.waiting_for_tarologist_user_id)
async def process_make_tarologist(message: types.Message, state: FSMContext):
    """Обработчик назначения таролога"""
    if message.text == '/cancel':
        await state.clear()
        await message.answer("❌ Назначение отменено.")
        await message.answer("Выберите действие:", reply_markup=get_admin_keyboard())
        return
    
    try:
        user_id = int(message.text)
        
        with get_db() as db:
            user = db.query(User).filter(User.user_id == user_id).first()
            
            if not user:
                await message.answer("❌ Пользователь не найден.")
                return
            
            user.is_tarologist = True
            db.commit()
            
            try:
                await bot.send_message(
                    user_id,
                    "<b>🎴 Вы были назначены тарологом бота!</b>\n"
                    "<i>Теперь вы можете отвечать на вопросы пользователей.</i>\n"
                    "<i>Используйте /tarologist для доступа к панели.</i>",
                    parse_mode="HTML"
                )
            except:
                pass
            
            await message.answer(f"✅ Пользователь @{user.username or user_id} назначен тарологом.")
    except ValueError:
        await message.answer("❌ Неверный формат ID. Отправьте числовой ID.")
    
    await state.clear()
    await message.answer("Выберите действие:", reply_markup=get_admin_keyboard())

@dp.message(AdminStates.waiting_for_remove_tarologist_user_id)
async def process_remove_tarologist(message: types.Message, state: FSMContext):
    """Обработчик удаления таролога"""
    if message.text == '/cancel':
        await state.clear()
        await message.answer("❌ Удаление отменено.")
        await message.answer("Выберите действие:", reply_markup=get_admin_keyboard())
        return
    
    try:
        user_id = int(message.text)
        
        with get_db() as db:
            user = db.query(User).filter(User.user_id == user_id).first()
            
            if not user:
                await message.answer("❌ Пользователь не найден.")
                return
            
            user.is_tarologist = False
            db.commit()
            
            # Удаляем из очереди тарологов
            await tarot_queue.remove_tarologist(user_id)
            
            try:
                await bot.send_message(
                    user_id,
                    "<b>👋 Вы были удалены из тарологов бота.</b>",
                    parse_mode="HTML"
                )
            except:
                pass
            
            await message.answer(f"✅ Пользователь @{user.username or user_id} удален из тарологов.")
    except ValueError:
        await message.answer("❌ Неверный формат ID. Отправьте числовой ID.")
    
    await state.clear()
    await message.answer("Выберите действие:", reply_markup=get_admin_keyboard())

@dp.message(AdminStates.sending_broadcast)
async def process_broadcast(message: types.Message, state: FSMContext):
    """Обработчик рассылки"""
    if message.text == '/cancel':
        await state.clear()
        await message.answer("❌ Рассылка отменена.")
        await message.answer("Выберите действие:", reply_markup=get_admin_keyboard())
        return
    
    broadcast_text = message.text
    
    # Полностью очищаем текст от HTML тегов
    broadcast_text = remove_all_html_tags(broadcast_text)
    
    await message.answer("🔄 Начинаю рассылку...")
    
    with get_db() as db:
        users = db.query(User).filter(User.is_banned == False).all()
        sent_count = 0
        failed_count = 0
        
        for user in users:
            try:
                # Отправляем как простой текст БЕЗ parse_mode
                await bot.send_message(user.user_id, broadcast_text)
                sent_count += 1
            except Exception as e:
                logger.error(f"Failed to send broadcast to {user.user_id}: {e}")
                failed_count += 1
            await asyncio.sleep(0.05)
        
        await message.answer(
            f"✅ Рассылка завершена!\n\n"
            f"📤 Отправлено: {sent_count}\n"
            f"❌ Не отправлено: {failed_count}\n"
            f"👥 Всего пользователей: {len(users)}"
        )
    
    await state.clear()
    await message.answer("Выберите действие:", reply_markup=get_admin_keyboard())


def remove_all_html_tags(text: str) -> str:
    """Удаляет ВСЕ HTML теги из текста"""
    import re
    import html
    
    # 1. Сначала декодируем HTML сущности
    text = html.unescape(text)
    
    # 2. Удаляем все HTML теги (включая самозакрывающиеся)
    text = re.sub(r'<[^>]+>', '', text)
    
    # 3. Заменяем HTML-специфичные последовательности
    replacements = {
        '&nbsp;': ' ',
        '&amp;': '&',
        '&quot;': '"',
        '&apos;': "'",
        '&lt;': '<',
        '&gt;': '>',
        '&copy;': '(c)',
        '&reg;': '(r)',
        '&trade;': '(tm)',
        '&euro;': '€',
        '&pound;': '£',
        '&yen;': '¥',
        '&cent;': '¢',
    }
    
    for entity, replacement in replacements.items():
        text = text.replace(entity, replacement)
    
    # 4. Удаляем множественные пробелы и переносы
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)
    
    # 5. Убираем пробелы в начале и конце строк
    lines = text.split('\n')
    lines = [line.strip() for line in lines]
    text = '\n'.join(lines)
    
    return text.strip()

# ========== ОБРАБОТЧИКИ ПАНЕЛИ ТАРОЛОГА ==========
@dp.callback_query(lambda c: c.data.startswith('tarologist_'))
async def process_tarologist_action(callback: types.CallbackQuery, state: FSMContext):
    """Обработчик действий панели таролога"""
    if not is_tarologist(callback.from_user.id):
        await callback.answer("⛔ Вы не являетесь тарологом.")
        return
    
    action = callback.data
    
    if action == 'tarologist_take':
        # Взять вопрос из очереди
        question_id = await tarot_queue.assign_question(callback.from_user.id)
        
        if not question_id:
            await callback.message.edit_text(
                "📭 В очереди нет вопросов.\n"
                "Ожидайте новых вопросов от пользователей."
            )
            await callback.answer()
            return
        
        question = get_tarot_question(question_id)
        
        if not question:
            await callback.message.edit_text("❌ Ошибка получения вопроса.")
            await callback.answer()
            return
        
        # Обновляем статус вопроса
        update_question_status(question_id, callback.from_user.id, "assigned")
        
        # Получаем информацию о пользователе
        user_info = get_user_info(question['user_id'])
        username = f"@{user_info['username']}" if user_info and user_info['username'] else f"ID: {question['user_id']}"
        
        question_text = (
            f"<b>📥 Новый вопрос в работе</b>\n\n"
            f"<b>ID вопроса:</b> #{question['id']}\n"
            f"<b>👤 Пользователь:</b> {username}\n"
            f"<b>📅 Задан:</b> {question['created_at']}\n\n"
            f"<b>❓ Вопрос:</b>\n{question['question']}\n\n"
            f"<b>📝 Напишите ответ:</b>\n"
            f"<i>Используйте кнопку 'Отправить ответ' или напишите ответ прямо здесь.</i>"
        )
        
        await state.set_state(TarologistStates.waiting_for_answer)
        await state.update_data(question_id=question['id'])
        
        await callback.message.edit_text(question_text, parse_mode="HTML")
        
        try:
            # Уведомляем пользователя
            await bot.send_message(
                question['user_id'],
                f"<b>🎴 Ваш вопрос взят в работу тарологом.</b>\n"
                f"<i>Ожидайте ответ в ближайшее время.</i>",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to notify user {question['user_id']}: {e}")
        
    elif action == 'tarologist_answer':
        # Отправить ответ на вопрос
        question_id = await tarot_queue.get_tarologist_question(callback.from_user.id)
        
        if not question_id:
            await callback.message.edit_text(
                "❌ У вас нет активного вопроса.\n"
                "Возьмите вопрос из очереди сначала."
            )
            await callback.answer()
            return
        
        await state.set_state(TarologistStates.waiting_for_answer)
        await state.update_data(question_id=question_id)
        
        question = get_tarot_question(question_id)
        
        if question:
            await callback.message.edit_text(
                f"<b>📤 Отправка ответа</b>\n\n"
                f"<b>❓ Вопрос:</b> {question['question'][:200]}...\n\n"
                "<b>Напишите ваш ответ пользователю.</b>\n"
                "<i>Вы можете использовать форматирование HTML.</i>\n\n"
                "<b>Отправьте текст ответа:</b>\n"
                "<i>(Для отмены отправьте /cancel)</i>",
                parse_mode="HTML"
            )
        else:
            await callback.message.edit_text(
                "<b>📤 Отправка ответа</b>\n\n"
                "<b>Напишите ваш ответ пользователю.</b>\n"
                "<i>Вы можете использовать форматирование HTML.</i>\n\n"
                "<b>Отправьте текст ответа:</b>\n"
                "<i>(Для отмены отправьте /cancel)</i>",
                parse_mode="HTML"
            )
    
    elif action == 'tarologist_my_questions':
        # Просмотр своих вопросов
        with get_db() as db:
            questions = db.query(TarotQuestion).filter(
                TarotQuestion.tarologist_id == callback.from_user.id
            ).order_by(TarotQuestion.created_at.desc()).limit(10).all()
            
            if not questions:
                await callback.message.edit_text(
                    "📭 У вас еще нет взятых вопросов.\n"
                    "Возьмите вопрос из очереди, нажав 'Взять вопрос'."
                )
                await callback.answer()
                return
            
            questions_text = "<b>📋 Ваши вопросы:</b>\n\n"
            
            for i, q in enumerate(questions, 1):
                status_emoji = "✅" if q.status == "answered" else "🔄"
                questions_text += (
                    f"{i}. {status_emoji} <b>#{q.id}</b>\n"
                    f"   👤 Пользователь ID: {q.user_id}\n"
                    f"   📅 {q.created_at.strftime('%d.%m.%Y %H:%M')}\n"
                    f"   📝 {q.question[:50]}...\n\n"
                )
            
            await callback.message.edit_text(questions_text, parse_mode="HTML")
    
    elif action == 'tarologist_stats':
        # Статистика таролога
        stats = get_tarologist_stats(callback.from_user.id)
        
        if not stats:
            await callback.message.edit_text("❌ Статистика не найдена.")
            await callback.answer()
            return
        
        stats_text = (
            "<b>📊 Ваша статистика как таролога:</b>\n\n"
            f"<b>👤 Ваш ник:</b> @{stats['username'] or 'Нет'}\n\n"
            f"<b>✅ Ответов дано:</b> {stats['answered_questions']}\n"
            f"<b>🔮 Раскладов сделано:</b> {stats['tarologist_spreads']}\n"
            f"<b>👥 Пользователей помогли:</b> {stats['total_users_helped']}\n\n"
            f"<b>🎴 Ваш рейтинг:</b>\n"
            f"• Активность: {'🔴 Низкая' if stats['answered_questions'] < 5 else '🟡 Средняя' if stats['answered_questions'] < 20 else '🟢 Высокая'}\n"
            f"• Помощь пользователям: {stats['total_users_helped']} чел."
        )
        
        await callback.message.edit_text(stats_text, parse_mode="HTML")
    
    elif action == 'tarologist_queue_stats':
        # Статистика очереди
        queue_stats = await tarot_queue.get_stats()
        pending_count = get_pending_questions_count()
        
        stats_text = (
            "<b>📈 Статистика очереди:</b>\n\n"
            f"<b>⏳ Ожидают в очереди:</b> {pending_count}\n"
            f"<b>🔄 В работе у тарологов:</b> {queue_stats['assigned']}\n"
            f"<b>🎴 Активных тарологов:</b> {queue_stats['active_tarologists']}\n\n"
            f"<b>📊 Ваш статус:</b>\n"
            f"{'✅ Вы активны' if callback.from_user.id in tarot_queue.active_tarologists else '❌ Вы неактивны'}"
        )
        
        await callback.message.edit_text(stats_text, parse_mode="HTML")
    
    elif action == 'tarologist_home':
        # Возврат в главное меню
        await state.clear()
        await tarot_queue.remove_tarologist(callback.from_user.id)
        
        await callback.message.edit_text(
            "✅ Панель таролога закрыта.",
            reply_markup=None
        )
        
        await callback.message.answer(
            "Возвращаемся в главное меню...",
            reply_markup=get_main_keyboard(callback.from_user.id)
        )
    
    await callback.answer()

@dp.message(TarologistStates.waiting_for_answer)
async def process_tarologist_answer(message: types.Message, state: FSMContext):
    """Обработчик ответа таролога"""
    if message.text == '/cancel':
        await state.clear()
        await message.answer("❌ Отправка ответа отменена.")
        await message.answer(
            "Выберите действие:",
            reply_markup=get_tarologist_keyboard()
        )
        return
    
    user_data = await state.get_data()
    question_id = user_data.get('question_id')
    answer = message.text
    
    if not question_id:
        await message.answer("❌ Ошибка: вопрос не найден.")
        await state.clear()
        return
    
    question = get_tarot_question(question_id)
    
    if not question:
        await message.answer("❌ Вопрос не найден в базе.")
        await state.clear()
        return
    
    # Проверяем, что таролог еще работает над этим вопросом
    current_question_id = await tarot_queue.get_tarologist_question(message.from_user.id)
    if current_question_id != question_id:
        await message.answer("❌ Этот вопрос больше не назначен вам.")
        await state.clear()
        await message.answer(
            "Выберите действие:",
            reply_markup=get_tarologist_keyboard()
        )
        return
    
    # Обновляем вопрос
    update_question_status(question_id, message.from_user.id, "answered")
    
    # Сохраняем расклад в историю
    spread_id = save_spread(
        user_id=question['user_id'],
        spread_type="tarologist_answer",
        question=question['question'],
        interpretation=answer,
        is_tarologist=True,
        tarologist_id=message.from_user.id
    )
    
    # Отправляем ответ пользователю
    try:
        user_info = get_user_info(question['user_id'])
        tarologist_info = get_user_info(message.from_user.id)
        tarologist_name = f"@{tarologist_info['username']}" if tarologist_info and tarologist_info['username'] else "Таролог"
        
        await bot.send_message(
            question['user_id'],
            f"<b>🎴 Ответ от таролога {tarologist_name}</b>\n\n"
            f"<b>❓ Ваш вопрос:</b>\n{question['question']}\n\n"
            f"<b>✨ Ответ таролога:</b>\n{answer}\n\n"
            f"<b>📌 Ответ сохранен в истории под номером #{spread_id}</b>\n"
            f"<i>📜 Посмотреть в истории: /history</i>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to send answer to user {question['user_id']}: {e}")
        await message.answer("⚠️ Не удалось отправить ответ пользователю (возможно, он заблокировал бота).")
    
    # Завершаем вопрос в очереди
    await tarot_queue.complete_question(message.from_user.id)
    
    await message.answer(
        f"✅ Ответ отправлен пользователю!\n"
        f"Расклад сохранен в истории под номером #{spread_id}"
    )
    
    await state.clear()
    await message.answer(
        "Выберите действие:",
        reply_markup=get_tarologist_keyboard()
    )

# ========== ФУНКЦИИ ОБСЛУЖИВАНИЯ ==========
async def cleanup_task():
    """Фоновая задача для очистки неактивных тарологов"""
    while True:
        try:
            await asyncio.sleep(1800)  # Каждые 30 минут
            await tarot_queue.cleanup_inactive()
            logger.info("Cleaned up inactive tarologists")
        except Exception as e:
            logger.error(f"Error in cleanup task: {e}")



giga_client = None

async def create_giga_client():
    """Создание глобального клиента GigaChat"""
    global giga_client
    if not giga_client:
        giga_client = GigaChatTarotClient(
            credentials=GIGACHAT_TOKEN,
            auth_url=GIGACHAT_AUTH_URL,
            scope=GIGACHAT_SCOPE,
            max_concurrent=1  # или 2, если хотите параллельные запросы
        )
    return giga_client

async def close_giga_client():
    """Корректное закрытие клиента GigaChat"""
    global giga_client
    if giga_client:
        await giga_client.close()
        giga_client = None


# ========== ЗАПУСК БОТА ==========
async def main():
    """Основная функция запуска бота"""
    logger.info("Запуск бота Таро с GigaChat...")
    
    # Создаем таблицы БД
    Base.metadata.create_all(bind=engine)
    
    # Создаем первого администратора, если его нет
    with get_db() as db:
        admin = db.query(User).filter(User.is_admin == True).first()
        if not admin:
            # Добавляем администратора по умолчанию
            default_admin_id = int(6401175778)
            
            if default_admin_id != 0:
                admin_user = User(
                    user_id=default_admin_id,
                    username="admin",
                    first_name="Admin",
                    is_admin=True,
                    is_tarologist=True
                )
                db.add(admin_user)
                db.commit()
                logger.info(f"Создан администратор по умолчанию с ID: {default_admin_id}")
            else:
                logger.warning("DEFAULT_ADMIN_ID не установлен. Администратор не создан.")


        pending_questions = db.query(TarotQuestion).filter(
            TarotQuestion.status == "pending"
        ).all()
        
        for question in pending_questions:
            await tarot_queue.add_question(question.id)
            logger.info(f"Добавлен в очередь вопрос #{question.id} (pending)")
        
        # 2. Находим вопросы со статусом "assigned" и восстанавливаем их назначение
        assigned_questions = db.query(TarotQuestion).filter(
            TarotQuestion.status == "assigned"
        ).all()
        
        for question in assigned_questions:
            if question.tarologist_id:
                # Восстанавливаем назначение в очереди
                tarot_queue.assigned_questions[question.tarologist_id] = question.id
                tarot_queue.active_tarologists[question.tarologist_id] = datetime.now(MSK_TZ)
                logger.info(f"Восстановлено назначение: таролог {question.tarologist_id} -> вопрос #{question.id}")
        
        # 3. Сбрасываем статус "assigned" вопросов, так как тарологи могли отключиться
        # (это опционально - зависит от вашей логики)
        for question in assigned_questions:
            question.status = "pending"  # Или оставить "assigned", если хотите сохранить
            db.add(question)
        
        db.commit()
    
        logger.info(f"Очередь восстановлена. В ожидании: {len(tarot_queue.pending_questions)}, "
                    f"Назначено: {len(tarot_queue.assigned_questions)}")

    await create_giga_client()
    # Запускаем фоновую задачу
    asyncio.create_task(cleanup_task())
    
    # Запускаем бота
    try:
        await dp.start_polling(bot)
    finally:
        await close_giga_client()

if __name__ == "__main__":
    # Запускаем главную функцию
    asyncio.run(main())