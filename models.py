from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from enum import IntEnum
import json

class UserStatus(IntEnum):
    """Статусы пользователя"""
    ACTIVE = 1
    BLOCKED = 2
    TEMPORARILY_BLOCKED = 3
    INACTIVE = 4

class ActionType(IntEnum):
    """Типы действий"""
    USER_REGISTERED = 1
    USER_BLOCKED = 2
    USER_UNBLOCKED = 3
    USER_WARNED = 4
    CHAT_JOINED = 5
    CHAT_LEFT = 6
    MESSAGE_SENT = 7
    COMMAND_USED = 8
    SETTINGS_CHANGED = 9
    BROADCAST_SENT = 10
    POLL_CREATED = 11
    GIVEAWAY_CREATED = 12
    REPORT_SUBMITTED = 13

class ReportType(IntEnum):
    """Типы жалоб"""
    SPAM = 1
    ABUSE = 2
    SCAM = 3
    PORNOGRAPHY = 4
    VIOLENCE = 5
    OTHER = 6

@dataclass
class User:
    """Модель пользователя"""
    user_id: int
    username: Optional[str] = None
    first_name: str = ""
    last_name: Optional[str] = None
    language_code: str = "ru"
    is_premium: bool = False
    email: Optional[str] = None
    phone: Optional[str] = None
    rating: int = 0
    warnings: int = 0
    status: UserStatus = UserStatus.ACTIVE
    registration_date: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    bot_id: int = 0
    
    # Дополнительные поля
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def full_name(self) -> str:
        """Полное имя пользователя"""
        if self.last_name:
            return f"{self.first_name} {self.last_name}"
        return self.first_name
    
    @property
    def is_blocked(self) -> bool:
        """Заблокирован ли пользователь"""
        return self.status in [UserStatus.BLOCKED, UserStatus.TEMPORARILY_BLOCKED]
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертация в словарь"""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "language_code": self.language_code,
            "is_premium": self.is_premium,
            "email": self.email,
            "phone": self.phone,
            "rating": self.rating,
            "warnings": self.warnings,
            "status": self.status.value,
            "registration_date": self.registration_date.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "bot_id": self.bot_id,
            "metadata": json.dumps(self.metadata, ensure_ascii=False)
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'User':
        """Создание из словаря"""
        metadata = {}
        if 'metadata' in data and data['metadata']:
            try:
                metadata = json.loads(data['metadata'])
            except:
                pass
        
        return cls(
            user_id=int(data['user_id']),
            username=data.get('username'),
            first_name=data.get('first_name', ''),
            last_name=data.get('last_name'),
            language_code=data.get('language_code', 'ru'),
            is_premium=bool(data.get('is_premium', False)),
            email=data.get('email'),
            phone=data.get('phone'),
            rating=int(data.get('rating', 0)),
            warnings=int(data.get('warnings', 0)),
            status=UserStatus(data.get('status', 1)),
            registration_date=datetime.fromisoformat(data['registration_date']) if 'registration_date' in data else datetime.now(),
            last_activity=datetime.fromisoformat(data['last_activity']) if 'last_activity' in data else datetime.now(),
            bot_id=int(data.get('bot_id', 0)),
            metadata=metadata
        )

@dataclass
class Chat:
    """Модель чата"""
    chat_id: int
    title: str = ""
    chat_type: str = "private"
    username: Optional[str] = None
    members_count: int = 0
    owner_id: Optional[int] = None
    bot_id: int = 0
    join_date: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    
    # Настройки
    settings: Dict[str, Any] = field(default_factory=lambda: {
        "automoderation_enabled": True,
        "warnings_enabled": True,
        "statistics_enabled": True,
        "rules_enabled": True,
        "max_warnings": 3,
        "warn_expire_days": 30
    })
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертация в словарь"""
        return {
            "chat_id": self.chat_id,
            "title": self.title,
            "chat_type": self.chat_type,
            "username": self.username,
            "members_count": self.members_count,
            "owner_id": self.owner_id,
            "bot_id": self.bot_id,
            "join_date": self.join_date.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "settings": json.dumps(self.settings, ensure_ascii=False)
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Chat':
        """Создание из словаря"""
        settings = {}
        if 'settings' in data and data['settings']:
            try:
                settings = json.loads(data['settings'])
            except:
                settings = {}
        
        return cls(
            chat_id=int(data['chat_id']),
            title=data.get('title', ''),
            chat_type=data.get('chat_type', 'private'),
            username=data.get('username'),
            members_count=int(data.get('members_count', 0)),
            owner_id=data.get('owner_id'),
            bot_id=int(data.get('bot_id', 0)),
            join_date=datetime.fromisoformat(data['join_date']) if 'join_date' in data else datetime.now(),
            last_activity=datetime.fromisoformat(data['last_activity']) if 'last_activity' in data else datetime.now(),
            settings=settings
        )

@dataclass
class BotAdmin:
    """Модель админа бота"""
    user_id: int
    level: int = 1  # 1-3
    permissions: List[str] = field(default_factory=list)
    added_by: Optional[int] = None
    added_date: datetime = field(default_factory=datetime.now)
    bot_id: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертация в словарь"""
        return {
            "user_id": self.user_id,
            "level": self.level,
            "permissions": json.dumps(self.permissions, ensure_ascii=False),
            "added_by": self.added_by,
            "added_date": self.added_date.isoformat(),
            "bot_id": self.bot_id
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BotAdmin':
        """Создание из словаря"""
        permissions = []
        if 'permissions' in data and data['permissions']:
            try:
                permissions = json.loads(data['permissions'])
            except:
                permissions = []
        
        return cls(
            user_id=int(data['user_id']),
            level=int(data.get('level', 1)),
            permissions=permissions,
            added_by=data.get('added_by'),
            added_date=datetime.fromisoformat(data['added_date']) if 'added_date' in data else datetime.now(),
            bot_id=int(data.get('bot_id', 0))
        )

@dataclass
class ChatAdmin:
    """Модель админа чата"""
    chat_id: int
    user_id: int
    level: int = 1  # 1-5
    permissions: List[str] = field(default_factory=list)
    added_by: Optional[int] = None
    added_date: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None
    bot_id: int = 0
    
    @property
    def is_expired(self) -> bool:
        """Истек ли срок админства"""
        if self.expires_at:
            return datetime.now() > self.expires_at
        return False
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертация в словарь"""
        return {
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "level": self.level,
            "permissions": json.dumps(self.permissions, ensure_ascii=False),
            "added_by": self.added_by,
            "added_date": self.added_date.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "bot_id": self.bot_id
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ChatAdmin':
        """Создание из словаря"""
        permissions = []
        if 'permissions' in data and data['permissions']:
            try:
                permissions = json.loads(data['permissions'])
            except:
                permissions = []
        
        expires_at = None
        if data.get('expires_at'):
            expires_at = datetime.fromisoformat(data['expires_at'])
        
        return cls(
            chat_id=int(data['chat_id']),
            user_id=int(data['user_id']),
            level=int(data.get('level', 1)),
            permissions=permissions,
            added_by=data.get('added_by'),
            added_date=datetime.fromisoformat(data['added_date']) if 'added_date' in data else datetime.now(),
            expires_at=expires_at,
            bot_id=int(data.get('bot_id', 0))
        )

@dataclass
class ActionLog:
    """Лог действия"""
    id: Optional[int] = None
    user_id: Optional[int] = None
    chat_id: Optional[int] = None
    action_type: ActionType = ActionType.MESSAGE_SENT
    action_data: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    bot_id: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертация в словарь"""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "action_type": self.action_type.value,
            "action_data": json.dumps(self.action_data, ensure_ascii=False),
            "timestamp": self.timestamp.isoformat(),
            "bot_id": self.bot_id
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ActionLog':
        """Создание из словаря"""
        action_data = {}
        if 'action_data' in data and data['action_data']:
            try:
                action_data = json.loads(data['action_data'])
            except:
                action_data = {}
        
        return cls(
            id=data.get('id'),
            user_id=data.get('user_id'),
            chat_id=data.get('chat_id'),
            action_type=ActionType(data.get('action_type', 7)),
            action_data=action_data,
            timestamp=datetime.fromisoformat(data['timestamp']) if 'timestamp' in data else datetime.now(),
            bot_id=int(data.get('bot_id', 0))
        )

@dataclass
class Broadcast:
    """Модель рассылки"""
    id: Optional[int] = None
    created_by: int = 0
    target_type: str = "all_users"  # all_users, all_chats, filtered
    target_filter: Dict[str, Any] = field(default_factory=dict)
    message_type: str = "text"
    message_data: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending, sending, completed, cancelled
    sent_count: int = 0
    failed_count: int = 0
    scheduled_for: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    bot_id: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертация в словарь"""
        return {
            "id": self.id,
            "created_by": self.created_by,
            "target_type": self.target_type,
            "target_filter": json.dumps(self.target_filter, ensure_ascii=False),
            "message_type": self.message_type,
            "message_data": json.dumps(self.message_data, ensure_ascii=False),
            "status": self.status,
            "sent_count": self.sent_count,
            "failed_count": self.failed_count,
            "scheduled_for": self.scheduled_for.isoformat() if self.scheduled_for else None,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "bot_id": self.bot_id
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Broadcast':
        """Создание из словаря"""
        target_filter = {}
        message_data = {}
        
        if 'target_filter' in data and data['target_filter']:
            try:
                target_filter = json.loads(data['target_filter'])
            except:
                target_filter = {}
        
        if 'message_data' in data and data['message_data']:
            try:
                message_data = json.loads(data['message_data'])
            except:
                message_data = {}
        
        scheduled_for = None
        if data.get('scheduled_for'):
            scheduled_for = datetime.fromisoformat(data['scheduled_for'])
        
        started_at = None
        if data.get('started_at'):
            started_at = datetime.fromisoformat(data['started_at'])
        
        completed_at = None
        if data.get('completed_at'):
            completed_at = datetime.fromisoformat(data['completed_at'])
        
        return cls(
            id=data.get('id'),
            created_by=int(data.get('created_by', 0)),
            target_type=data.get('target_type', 'all_users'),
            target_filter=target_filter,
            message_type=data.get('message_type', 'text'),
            message_data=message_data,
            status=data.get('status', 'pending'),
            sent_count=int(data.get('sent_count', 0)),
            failed_count=int(data.get('failed_count', 0)),
            scheduled_for=scheduled_for,
            created_at=datetime.fromisoformat(data['created_at']) if 'created_at' in data else datetime.now(),
            started_at=started_at,
            completed_at=completed_at,
            bot_id=int(data.get('bot_id', 0))
        )

@dataclass
class CustomCommand:
    """Модель кастомной команды"""
    id: Optional[int] = None
    name: str = ""
    description: str = ""
    command_text: str = ""
    response_type: str = "text"
    response_data: Dict[str, Any] = field(default_factory=dict)
    buttons: List[Dict[str, Any]] = field(default_factory=list)
    works_in: str = "everywhere"  # everywhere, private_only, chats_only
    access_level: int = 0  # 0 = все, 1+ = только админы уровня X
    usage_count: int = 0
    created_by: Optional[int] = None
    created_at: datetime = field(default_factory=datetime.now)
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    bot_id: int = 0
    
    @property
    def is_valid(self) -> bool:
        """Действительна ли команда сейчас"""
        now = datetime.now()
        if self.valid_from and now < self.valid_from:
            return False
        if self.valid_until and now > self.valid_until:
            return False
        return True
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертация в словарь"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "command_text": self.command_text,
            "response_type": self.response_type,
            "response_data": json.dumps(self.response_data, ensure_ascii=False),
            "buttons": json.dumps(self.buttons, ensure_ascii=False),
            "works_in": self.works_in,
            "access_level": self.access_level,
            "usage_count": self.usage_count,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "bot_id": self.bot_id
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CustomCommand':
        """Создание из словаря"""
        response_data = {}
        buttons = []
        
        if 'response_data' in data and data['response_data']:
            try:
                response_data = json.loads(data['response_data'])
            except:
                response_data = {}
        
        if 'buttons' in data and data['buttons']:
            try:
                buttons = json.loads(data['buttons'])
            except:
                buttons = []
        
        valid_from = None
        if data.get('valid_from'):
            valid_from = datetime.fromisoformat(data['valid_from'])
        
        valid_until = None
        if data.get('valid_until'):
            valid_until = datetime.fromisoformat(data['valid_until'])
        
        return cls(
            id=data.get('id'),
            name=data.get('name', ''),
            description=data.get('description', ''),
            command_text=data.get('command_text', ''),
            response_type=data.get('response_type', 'text'),
            response_data=response_data,
            buttons=buttons,
            works_in=data.get('works_in', 'everywhere'),
            access_level=int(data.get('access_level', 0)),
            usage_count=int(data.get('usage_count', 0)),
            created_by=data.get('created_by'),
            created_at=datetime.fromisoformat(data['created_at']) if 'created_at' in data else datetime.now(),
            valid_from=valid_from,
            valid_until=valid_until,
            bot_id=int(data.get('bot_id', 0))
        )