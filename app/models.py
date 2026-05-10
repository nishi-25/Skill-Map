from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, UniqueConstraint, Table
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base

# ── スキル承認ステータス ────────────────────────────────────────
APPROVAL_STATUS = {
    "pending":  "承認待ち",
    "approved": "承認済み",
    "rejected": "差し戻し",
}

APPROVAL_STATUS_COLORS = {
    "pending":  "warning",
    "approved": "success",
    "rejected": "danger",
}

# ── ユーザーのスキル自己評価レベル ──────────────────────────────
SKILL_LEVELS = {
    0: "未経験",
    1: "入門",
    2: "基礎",
    3: "実務可",
    4: "エキスパート",
}

LEVEL_COLORS = {
    0: "secondary",
    1: "info",
    2: "primary",
    3: "warning",
    4: "danger",
}

# ── スキルカタログの難易度ティア ────────────────────────────────
SKILL_TIERS = {
    "beginner":     "初級",
    "basic":        "基礎",
    "intermediate": "中級",
    "advanced":     "上級",
}

TIER_COLORS = {
    "beginner":     "success",
    "basic":        "primary",
    "intermediate": "warning",
    "advanced":     "danger",
}

# ── ティア表示名（カスタマイズ可、DB優先） ──────────────────────
DEFAULT_TIER_NAMES = {
    "beginner":     "初級",
    "basic":        "基礎",
    "intermediate": "中級",
    "advanced":     "上級",
}

TIER_ICONS = {
    "beginner":     "bi-rocket-takeoff",
    "basic":        "bi-book",
    "intermediate": "bi-star-half",
    "advanced":     "bi-trophy",
}

TIER_DESCRIPTIONS = {
    "beginner":     "基本的な操作・知識を習得するスキル",
    "basic":        "業務で実際に活用できる実践スキル",
    "intermediate": "チームをリードできる応用・設計スキル",
    "advanced":     "専門家レベルの高度・体系的なスキル",
}


def get_tier_display_names(db):
    """DBのAppSettingからカスタムティア名を取得。未設定ならデフォルト"""
    names = dict(DEFAULT_TIER_NAMES)
    for key in names:
        setting = db.query(AppSetting).filter(
            AppSetting.key == f"tier_name_{key}"
        ).first()
        if setting and setting.value.strip():
            names[key] = setting.value.strip()
    return names


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(200), nullable=True)
    display_name = Column(String(100), nullable=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default="user")        # admin / manager / user
    is_approved = Column(Boolean, default=False)
    avatar_path = Column(String(255), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    skill_levels = relationship("UserSkillLevel", back_populates="user",
                                foreign_keys="UserSkillLevel.user_id",
                                cascade="all, delete-orphan")
    group_memberships = relationship("GroupMembership", back_populates="user",
                                     foreign_keys="GroupMembership.user_id",
                                     cascade="all, delete-orphan")
    managed_groups = relationship("Group", back_populates="manager",
                                  foreign_keys="Group.manager_id")


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    color = Column(String(20), default="#6366f1")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    skills = relationship("Skill", back_populates="category")
    creator = relationship("User", foreign_keys=[created_by])


# ─── スキルカタログ（Admin/Manager が管理する共通マスター） ─────

class Skill(Base):
    __tablename__ = "skills"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    # 難易度ティア: beginner / basic / intermediate / advanced
    tier = Column(String(20), default="basic", nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    is_archived = Column(Boolean, default=False, nullable=False)

    category = relationship("Category", back_populates="skills")
    creator = relationship("User", foreign_keys=[created_by])
    user_levels = relationship("UserSkillLevel", back_populates="skill",
                               cascade="all, delete-orphan")
    tags = relationship("SkillTag", secondary="skill_tag_associations", back_populates="skills")


# ─── ユーザーのスキルレベル自己申告 ──────────────────────────────

class UserSkillLevel(Base):
    __tablename__ = "user_skill_levels"
    __table_args__ = (UniqueConstraint("user_id", "skill_id", name="uq_user_skill"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    skill_id = Column(Integer, ForeignKey("skills.id"), nullable=False)
    level = Column(Integer, default=0)    # 0=未経験 〜 4=エキスパート
    approval_status = Column(String(20), default="pending")  # pending / approved / rejected
    approver_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approver_comment = Column(Text, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="skill_levels", foreign_keys=[user_id])
    skill = relationship("Skill", back_populates="user_levels")
    approver = relationship("User", foreign_keys=[approver_id])


# ─── スキルレベル変更履歴（時系列追跡用） ────────────────────────

class SkillLevelHistory(Base):
    __tablename__ = "skill_level_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    skill_id = Column(Integer, ForeignKey("skills.id"), nullable=False)
    level = Column(Integer, nullable=False)
    previous_level = Column(Integer, nullable=True)
    changed_at = Column(DateTime, server_default=func.now())
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    user = relationship("User", foreign_keys=[user_id])
    skill = relationship("Skill", foreign_keys=[skill_id])
    approver = relationship("User", foreign_keys=[approved_by])


# ─── グループ ─────────────────────────────────────────────────────

# グループ × スキル 中間テーブル
group_skills = Table(
    "group_skills",
    Base.metadata,
    Column("id", Integer, primary_key=True),
    Column("group_id", Integer, ForeignKey("groups.id"), nullable=False),
    Column("skill_id", Integer, ForeignKey("skills.id"), nullable=False),
    UniqueConstraint("group_id", "skill_id", name="uq_group_skill"),
)

# グループ × Manager 中間テーブル（複数Manager対応）
group_managers = Table(
    "group_managers",
    Base.metadata,
    Column("id", Integer, primary_key=True),
    Column("group_id", Integer, ForeignKey("groups.id"), nullable=False),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    UniqueConstraint("group_id", "user_id", name="uq_group_manager"),
)


class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    manager_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    parent_id = Column(Integer, ForeignKey("groups.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    manager = relationship("User", back_populates="managed_groups",
                           foreign_keys=[manager_id])
    managers = relationship("User", secondary=group_managers,
                            backref="co_managed_groups")
    parent = relationship("Group", remote_side="Group.id",
                          backref="children", foreign_keys=[parent_id])
    memberships = relationship("GroupMembership", back_populates="group",
                               cascade="all, delete-orphan")
    skills = relationship("Skill", secondary=group_skills, backref="groups")


class GroupMembership(Base):
    __tablename__ = "group_memberships"

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    joined_at = Column(DateTime, server_default=func.now())

    group = relationship("Group", back_populates="memberships")
    user = relationship("User", back_populates="group_memberships",
                        foreign_keys=[user_id])


class GroupTransfer(Base):
    __tablename__ = "group_transfers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    from_group_id = Column(Integer, ForeignKey("groups.id"), nullable=True)
    to_group_id = Column(Integer, ForeignKey("groups.id"), nullable=True)
    transferred_at = Column(DateTime, server_default=func.now())
    transferred_by = Column(Integer, ForeignKey("users.id"), nullable=False)

    user = relationship("User", foreign_keys=[user_id])
    from_group = relationship("Group", foreign_keys=[from_group_id])
    to_group = relationship("Group", foreign_keys=[to_group_id])
    operator = relationship("User", foreign_keys=[transferred_by])


# ─── お知らせ（今後の追加機能） ───────────────────────────────────

ANNOUNCEMENT_TYPES = {
    "feature":     "🚀 新機能",
    "improvement": "🔧 改善",
    "info":        "📋 お知らせ",
    "maintenance": "⚠️ メンテナンス",
}
ANNOUNCEMENT_TYPE_COLORS = {
    "feature":     "primary",
    "improvement": "success",
    "info":        "info",
    "maintenance": "warning",
}


class Announcement(Base):
    __tablename__ = "announcements"

    id           = Column(Integer, primary_key=True, index=True)
    title        = Column(String(200), nullable=False)
    content      = Column(Text, nullable=False)
    ann_type     = Column(String(20), default="feature")   # feature/improvement/info/maintenance
    scheduled_at = Column(DateTime, nullable=True)          # 予定日（任意）
    is_published = Column(Boolean, default=True)            # 公開/非公開
    created_by   = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at   = Column(DateTime, server_default=func.now())
    updated_at   = Column(DateTime, server_default=func.now(), onupdate=func.now())

    creator = relationship("User", foreign_keys=[created_by])


# ─── 教育リソース ────────────────────────────────────────────────

class EducationalLink(Base):
    __tablename__ = "educational_links"

    id          = Column(Integer, primary_key=True, index=True)
    title       = Column(String(200), nullable=False)
    url         = Column(String(1000), nullable=False)
    description = Column(Text, nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    skill_id    = Column(Integer, ForeignKey("skills.id"), nullable=True)
    created_by  = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at  = Column(DateTime, server_default=func.now())
    updated_at  = Column(DateTime, server_default=func.now(), onupdate=func.now())

    category = relationship("Category", foreign_keys=[category_id])
    skill    = relationship("Skill",    foreign_keys=[skill_id])
    creator  = relationship("User",     foreign_keys=[created_by])


# ─── 問い合わせ・要望チケット ─────────────────────────────────────

TICKET_TYPES = {"inquiry": "問い合わせ", "request": "要望"}
TICKET_STATUS = {
    "open":        "未対応",
    "in_progress": "対応中",
    "resolved":    "解決済み",
    "closed":      "クローズ",
}
TICKET_STATUS_COLORS = {
    "open":        "warning",
    "in_progress": "primary",
    "resolved":    "success",
    "closed":      "secondary",
}
TICKET_PRIORITY = {"low": "低", "medium": "中", "high": "高"}
TICKET_PRIORITY_COLORS = {"low": "success", "medium": "warning", "high": "danger"}


class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    ticket_type = Column(String(20), default="inquiry")   # inquiry / request
    status = Column(String(20), default="open")           # open / in_progress / resolved / closed
    priority = Column(String(10), default="medium")       # low / medium / high
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    # 未読フラグ
    unread_admin = Column(Boolean, default=True)   # ユーザーからの新着
    unread_user  = Column(Boolean, default=False)  # Adminからの返信あり

    creator  = relationship("User", foreign_keys=[created_by], backref="created_tickets")
    messages = relationship("TicketMessage", back_populates="ticket",
                            cascade="all, delete-orphan",
                            order_by="TicketMessage.created_at")


class TicketMessage(Base):
    __tablename__ = "ticket_messages"

    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=False)
    user_id   = Column(Integer, ForeignKey("users.id"),   nullable=False)
    message   = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    ticket = relationship("Ticket", back_populates="messages")
    user   = relationship("User", foreign_keys=[user_id])


# ─── アプリ設定 (Key-Value) ──────────────────────────────────────

class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, default="")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


# ─── スキルタグ ───────────────────────────────────────────────────

skill_tag_associations = Table(
    "skill_tag_associations",
    Base.metadata,
    Column("skill_id", Integer, ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("skill_tags.id", ondelete="CASCADE"), primary_key=True),
)


class SkillTag(Base):
    __tablename__ = "skill_tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False, unique=True)
    color = Column(String, default="#6c757d")
    created_at = Column(DateTime, default=datetime.utcnow)

    skills = relationship("Skill", secondary="skill_tag_associations", back_populates="tags")
