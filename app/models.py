from datetime import datetime, date as _date_type
from sqlalchemy import Column, Integer, String, Text, DateTime, Date, Float, ForeignKey, Boolean, UniqueConstraint, Table
from sqlalchemy.orm import relationship, backref
from sqlalchemy.sql import func
from database import Base

# ── スキル承認ステータス ────────────────────────────────────────
APPROVAL_STATUS = {
    "pending":  "承認待ち",
    "approved": "承認済み",
    "rejected": "差し戻し",
    "revoke_pending": "取消申請中",
}

APPROVAL_STATUS_COLORS = {
    "pending":  "warning",
    "approved": "success",
    "rejected": "danger",
    "revoke_pending": "dark",
}

# ── ユーザーのスキル自己評価レベル ──────────────────────────────
SKILL_LEVELS = {
    0: "未経験",
    1: "入門",
    2: "実務可",
    3: "指導可",
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
    "basic":        "C級",
    "intermediate": "B級",
    "advanced":     "A級",
}

TIER_COLORS = {
    "basic":        "primary",
    "intermediate": "warning",
    "advanced":     "danger",
}

# Python側でのティア並び替え用（basic < intermediate < advanced）
TIER_ORDER = {
    "basic":        0,
    "intermediate": 1,
    "advanced":     2,
}

# ── ティア表示名（カスタマイズ可、DB優先） ──────────────────────
DEFAULT_TIER_NAMES = {
    "basic":        "C級",
    "intermediate": "B級",
    "advanced":     "A級",
}

TIER_ICONS = {
    "basic":        "bi-book",
    "intermediate": "bi-star-half",
    "advanced":     "bi-trophy",
}

TIER_DESCRIPTIONS = {
    "basic":        "決められた手順・手順書のとおりに、指示された作業を正確に実施できる（手順内のOK/NG判定を含む）",
    "intermediate": "作業の目的・背景・前後関係を理解し、状況に応じて判断・調整しながら作業を進められる",
    "advanced":     "専門知識をもとに課題を分析し、手順や仕組みの改善・効率化を提案・実行できる",
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
    suppress_ann_popup = Column(Boolean, default=False)   # お知らせポップアップを非表示にするか
    must_change_password = Column(Boolean, default=False) # 仮パスワードでログイン後の強制変更フラグ
    nav_pinned_sections = Column(Text, nullable=True)     # ナビゲーターで常時展開するセクション名のJSON配列
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
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    is_archived = Column(Boolean, default=False, nullable=False)

    category = relationship("Category", back_populates="skills")
    creator = relationship("User", foreign_keys=[created_by])
    user_levels = relationship("UserSkillLevel", back_populates="skill",
                               cascade="all, delete-orphan")
    tags = relationship("SkillTag", secondary="skill_tag_associations", back_populates="skills")
    sub_skills = relationship("SubSkill", back_populates="skill",
                              order_by="SubSkill.order_index",
                              cascade="all, delete-orphan")

    @property
    def tier(self):
        """サブスキルの難易度ティアの最頻値を代表値として返す（同数なら難度が高い方を優先）"""
        if not self.sub_skills:
            return "basic"
        counts = {}
        for sub in self.sub_skills:
            counts[sub.tier] = counts.get(sub.tier, 0) + 1
        return max(counts, key=lambda t: (counts[t], TIER_ORDER.get(t, 0)))


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

    override_level = Column(Integer, nullable=True)    # 手動上書きレベル
    override_reason = Column(Text, nullable=True)      # 上書き理由

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
    area_id     = Column(Integer, ForeignKey("learning_path_areas.id", ondelete="CASCADE"), nullable=True)
    step_order  = Column(Integer, nullable=True)
    created_by  = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at  = Column(DateTime, server_default=func.now())
    updated_at  = Column(DateTime, server_default=func.now(), onupdate=func.now())

    category = relationship("Category", foreign_keys=[category_id])
    skill    = relationship("Skill",    foreign_keys=[skill_id])
    creator  = relationship("User",     foreign_keys=[created_by])
    area     = relationship("LearningPathArea", back_populates="steps")


# ─── 学習パスエリア（フリー作成） ────────────────────────────────────

class LearningPathArea(Base):
    __tablename__ = "learning_path_areas"

    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String(200), nullable=False)
    description = Column(String(500), nullable=True)
    parent_id   = Column(Integer, ForeignKey("learning_path_areas.id", ondelete="CASCADE"), nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    skill_id    = Column(Integer, ForeignKey("skills.id"), nullable=True)
    order_index = Column(Integer, default=0)
    created_by  = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at  = Column(DateTime, server_default=func.now())

    category = relationship("Category", foreign_keys=[category_id])
    skill    = relationship("Skill",    foreign_keys=[skill_id])
    creator  = relationship("User",     foreign_keys=[created_by])
    steps    = relationship("EducationalLink", back_populates="area",
                            cascade="all, delete-orphan",
                            order_by="EducationalLink.step_order")
    children = relationship("LearningPathArea",
                            foreign_keys="[LearningPathArea.parent_id]",
                            back_populates="parent",
                            cascade="all, delete-orphan",
                            order_by="LearningPathArea.order_index")
    parent   = relationship("LearningPathArea",
                            foreign_keys="[LearningPathArea.parent_id]",
                            back_populates="children",
                            remote_side="[LearningPathArea.id]")


# ─── 学習進捗 ────────────────────────────────────────────────────

class UserLearningProgress(Base):
    __tablename__ = "user_learning_progress"

    id                  = Column(Integer, primary_key=True, index=True)
    user_id             = Column(Integer, ForeignKey("users.id"), nullable=False)
    educational_link_id = Column(Integer, ForeignKey("educational_links.id"), nullable=False)
    completed_at        = Column(DateTime, server_default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "educational_link_id"),)

    user = relationship("User",             foreign_keys=[user_id])
    link = relationship("EducationalLink",  foreign_keys=[educational_link_id])


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


# ─── 業務マップ ───────────────────────────────────────────────────

# 業務エリア × グループ 中間テーブル（ルートエリアのみ対象）
business_map_area_groups = Table(
    "business_map_area_groups",
    Base.metadata,
    Column("id", Integer, primary_key=True),
    Column("area_id", Integer, ForeignKey("business_map_areas.id", ondelete="CASCADE"), nullable=False),
    Column("group_id", Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
    UniqueConstraint("area_id", "group_id", name="uq_bm_area_group"),
)


class BusinessMapArea(Base):
    __tablename__ = "business_map_areas"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    color = Column(String(20), default="#6366f1")
    order_index = Column(Integer, default=0)
    parent_id = Column(Integer, ForeignKey("business_map_areas.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    area_sub_skills = relationship("BusinessMapAreaSkill", back_populates="area",
                                    order_by="BusinessMapAreaSkill.order_index",
                                    cascade="all, delete-orphan")
    parent = relationship("BusinessMapArea", remote_side="BusinessMapArea.id",
                           backref=backref("children", cascade="all, delete-orphan",
                                            order_by="BusinessMapArea.order_index"),
                           foreign_keys=[parent_id])
    creator = relationship("User", foreign_keys=[created_by])
    groups = relationship("Group", secondary="business_map_area_groups", lazy="selectin")


class BusinessMapAreaSkill(Base):
    __tablename__ = "business_map_area_skills"
    __table_args__ = (UniqueConstraint("area_id", "sub_skill_id", name="uq_area_subskill"),)

    id = Column(Integer, primary_key=True, index=True)
    area_id = Column(Integer, ForeignKey("business_map_areas.id", ondelete="CASCADE"), nullable=False)
    sub_skill_id = Column(Integer, ForeignKey("sub_skills.id", ondelete="CASCADE"), nullable=False)
    order_index = Column(Integer, default=0)

    area = relationship("BusinessMapArea", back_populates="area_sub_skills")
    sub_skill = relationship("SubSkill")


# ─── サブスキル ───────────────────────────────────────────────────

class SubSkill(Base):
    __tablename__ = "sub_skills"
    id = Column(Integer, primary_key=True)
    skill_id = Column(Integer, ForeignKey("skills.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    order_index = Column(Integer, default=0)
    # 難易度ティア: basic / intermediate / advanced
    tier = Column(String(20), default="basic", nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    skill = relationship("Skill", back_populates="sub_skills")
    creator = relationship("User", foreign_keys=[created_by])


# ─── ユーザーのサブスキル達成状況 ────────────────────────────────

class UserSubSkillLevel(Base):
    __tablename__ = "user_sub_skill_levels"
    __table_args__ = (UniqueConstraint("user_id", "sub_skill_id", name="uq_user_subSkill"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    sub_skill_id = Column(Integer, ForeignKey("sub_skills.id", ondelete="CASCADE"), nullable=False)
    can_do = Column(Boolean, default=False, nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user = relationship("User", foreign_keys=[user_id])
    sub_skill = relationship("SubSkill", foreign_keys=[sub_skill_id])


# ─── スキルエビデンス ─────────────────────────────────────────────

class SkillEvidence(Base):
    __tablename__ = "skill_evidences"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    skill_id = Column(Integer, ForeignKey("skills.id", ondelete="CASCADE"), nullable=False)
    evidence_type = Column(String(20), nullable=False)  # 'url', 'note', 'file'
    title = Column(String(200), nullable=True)
    content = Column(Text, nullable=False, default="")
    file_path = Column(String(500), nullable=True)           # アップロードファイルのサーバ保存パス
    original_filename = Column(String(255), nullable=True)   # 元のファイル名（表示用）
    created_at = Column(DateTime, server_default=func.now())

    user = relationship("User", foreign_keys=[user_id])
    skill = relationship("Skill", foreign_keys=[skill_id])


# ─── スキル目標 ───────────────────────────────────────────────────

class SkillGoal(Base):
    __tablename__ = "skill_goals"
    __table_args__ = (UniqueConstraint("user_id", "skill_id", name="uq_skill_goal"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    skill_id = Column(Integer, ForeignKey("skills.id", ondelete="CASCADE"), nullable=False)
    target_level = Column(Integer, nullable=False)  # 1-4
    target_date = Column(Date, nullable=True)
    note = Column(String(200), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user = relationship("User", foreign_keys=[user_id])
    skill = relationship("Skill", foreign_keys=[skill_id])


# ─── 達成バッジ ───────────────────────────────────────────────────

BADGE_DEFS = {
    "first_declare":    {"label": "初申告",        "icon": "bi-star-fill",        "color": "#f59e0b", "desc": "初めてスキルを申告した"},
    "first_approved":   {"label": "初承認",        "icon": "bi-patch-check-fill", "color": "#22c55e", "desc": "初めてスキルが承認された"},
    "cat_complete":     {"label": "カテゴリ制覇",   "icon": "bi-trophy-fill",       "color": "#f97316", "desc": "1カテゴリの全スキルを申告した"},
    "sub_check_10":     {"label": "サブスキル10",  "icon": "bi-check2-all",        "color": "#3b82f6", "desc": "サブスキルを10個以上チェックした"},
    "sub_check_50":     {"label": "サブスキル50",  "icon": "bi-check-circle-fill", "color": "#8b5cf6", "desc": "サブスキルを50個以上チェックした"},
    "level4_skill":     {"label": "上級スキル",    "icon": "bi-award-fill",         "color": "#ef4444", "desc": "上級レベルのスキルを申告した"},
    "multi_cat_5":      {"label": "マルチスキル",  "icon": "bi-grid-fill",          "color": "#0ea5e9", "desc": "5カテゴリ以上でスキルを申告した"},
}


class UserBadge(Base):
    __tablename__ = "user_badges"
    __table_args__ = (UniqueConstraint("user_id", "badge_key", name="uq_user_badge"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    badge_key = Column(String(50), nullable=False)
    awarded_at = Column(DateTime, server_default=func.now())

    user = relationship("User", foreign_keys=[user_id])


class AdminTodo(Base):
    __tablename__ = "admin_todos"

    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    priority = Column(String(20), default="medium", nullable=False)   # high / medium / long_term
    status = Column(String(20), default="pending", nullable=False)    # pending / in_progress / review / done
    order_index = Column(Integer, default=0)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    creator = relationship("User", foreign_keys=[created_by])


# ─── 資格情報 ───────────────────────────────────────────────────

class CertificationCatalog(Base):
    """事前登録された資格マスタ（ユーザーはここから選択して資格情報を登録する）"""
    __tablename__ = "certification_catalog"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False, unique=True)
    issuer = Column(String(200), nullable=True)
    category_name = Column(String(100), nullable=True)  # 関連するスキルカタログのカテゴリ名等
    description = Column(Text, nullable=True)
    has_score = Column(Boolean, default=False, nullable=False)  # TOEIC等、点数入力欄を表示するか
    tier = Column(String(20), default="basic", nullable=False)  # 難易度（basic/intermediate/advanced）
    is_archived = Column(Boolean, default=False, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    creator = relationship("User", foreign_keys=[created_by])


class Certification(Base):
    __tablename__ = "certifications"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    catalog_id = Column(Integer, ForeignKey("certification_catalog.id"), nullable=True)
    name = Column(String(200), nullable=False)
    issuer = Column(String(200), nullable=True)
    issued_date = Column(Date, nullable=True)
    expiry_date = Column(Date, nullable=True)
    certificate_number = Column(String(100), nullable=True)
    score = Column(Integer, nullable=True)  # TOEIC等の点数
    note = Column(Text, nullable=True)
    file_path = Column(String(500), nullable=True)
    original_filename = Column(String(255), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user = relationship("User", foreign_keys=[user_id])
    catalog = relationship("CertificationCatalog", foreign_keys=[catalog_id])


# ─── 試験機能 ───────────────────────────────────────────────────

QUESTION_TYPES = {"single": "単一選択", "multi": "複数選択"}
EXAM_ASSIGN_STATUS = {
    "assigned": "未受験",
    "in_progress": "受験中",
    "submitted": "採点待ち",
    "graded": "完了",
}
EXAM_ASSIGN_STATUS_COLORS = {
    "assigned": "secondary",
    "in_progress": "warning",
    "submitted": "info",
    "graded": "success",
}


class Exam(Base):
    __tablename__ = "exams"

    id = Column(Integer, primary_key=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    exam_type = Column(String(20), nullable=False)  # 旧フィールド（未使用）
    has_written = Column(Boolean, default=True, nullable=False)    # 学科試験を含むか
    has_practical = Column(Boolean, default=True, nullable=False)  # 実技試験を含むか
    time_limit_minutes = Column(Integer, nullable=True)   # 学科のみ
    pass_score = Column(Integer, nullable=True)           # 学科のみ、合格ライン(%)
    is_archived = Column(Boolean, default=False, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # 受験条件: 対象スキルの対象ティアの取得率が required_completion_rate(%) 以上で受験可能
    target_skill_id = Column(Integer, ForeignKey("skills.id"), nullable=True)
    target_tier = Column(String(20), nullable=True)            # basic / intermediate / advanced
    required_completion_rate = Column(Integer, nullable=True)  # 0-100 (%)

    creator = relationship("User", foreign_keys=[created_by])
    target_skill = relationship("Skill", foreign_keys=[target_skill_id])
    questions = relationship("ExamQuestion", order_by="ExamQuestion.order_index",
                              cascade="all, delete-orphan", back_populates="exam")
    criteria = relationship("ExamCriterion", order_by="ExamCriterion.order_index",
                             cascade="all, delete-orphan", back_populates="exam")
    assignments = relationship("ExamAssignment", cascade="all, delete-orphan", back_populates="exam")


class ExamQuestion(Base):
    __tablename__ = "exam_questions"

    id = Column(Integer, primary_key=True)
    exam_id = Column(Integer, ForeignKey("exams.id", ondelete="CASCADE"), nullable=False)
    question_text = Column(Text, nullable=False)
    question_type = Column(String(20), nullable=False)  # single / multi
    choices = Column(Text, nullable=False, default="[]")          # JSON配列文字列
    correct_indices = Column(Text, nullable=False, default="[]")  # JSON配列文字列(0-based)
    points = Column(Integer, default=1, nullable=False)
    order_index = Column(Integer, default=0)

    exam = relationship("Exam", back_populates="questions")


class ExamCriterion(Base):
    __tablename__ = "exam_criteria"

    id = Column(Integer, primary_key=True)
    exam_id = Column(Integer, ForeignKey("exams.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    max_score = Column(Integer, default=10, nullable=False)
    order_index = Column(Integer, default=0)

    exam = relationship("Exam", back_populates="criteria")


class ExamAssignment(Base):
    __tablename__ = "exam_assignments"
    __table_args__ = (UniqueConstraint("exam_id", "user_id", name="uq_exam_assignment"),)

    id = Column(Integer, primary_key=True)
    exam_id = Column(Integer, ForeignKey("exams.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assigned_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    assigned_at = Column(DateTime, server_default=func.now())
    due_date = Column(Date, nullable=True)
    status = Column(String(20), default="assigned", nullable=False)
    started_at = Column(DateTime, nullable=True)
    written_submitted_at = Column(DateTime, nullable=True)  # 学科部分の提出（実技と分離して受験する場合の進行管理）
    submitted_at = Column(DateTime, nullable=True)
    graded_at = Column(DateTime, nullable=True)
    graded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    score = Column(Float, nullable=True)
    max_score = Column(Float, nullable=True)
    passed = Column(Boolean, nullable=True)
    feedback = Column(Text, nullable=True)

    exam = relationship("Exam", back_populates="assignments")
    user = relationship("User", foreign_keys=[user_id])
    assigner = relationship("User", foreign_keys=[assigned_by])
    grader = relationship("User", foreign_keys=[graded_by])
    answers = relationship("ExamAnswer", cascade="all, delete-orphan", back_populates="assignment")
    criterion_scores = relationship("ExamCriterionScore", cascade="all, delete-orphan", back_populates="assignment")
    evidences = relationship("ExamSubmissionEvidence", cascade="all, delete-orphan", back_populates="assignment")


class ExamAnswer(Base):
    __tablename__ = "exam_answers"

    id = Column(Integer, primary_key=True)
    assignment_id = Column(Integer, ForeignKey("exam_assignments.id", ondelete="CASCADE"), nullable=False)
    question_id = Column(Integer, ForeignKey("exam_questions.id", ondelete="CASCADE"), nullable=False)
    selected_indices = Column(Text, nullable=False, default="[]")  # JSON配列文字列
    is_correct = Column(Boolean, nullable=True)
    points_awarded = Column(Float, default=0, nullable=False)

    assignment = relationship("ExamAssignment", back_populates="answers")
    question = relationship("ExamQuestion")


class ExamCriterionScore(Base):
    __tablename__ = "exam_criterion_scores"

    id = Column(Integer, primary_key=True)
    assignment_id = Column(Integer, ForeignKey("exam_assignments.id", ondelete="CASCADE"), nullable=False)
    criterion_id = Column(Integer, ForeignKey("exam_criteria.id", ondelete="CASCADE"), nullable=False)
    score = Column(Float, nullable=True)
    comment = Column(Text, nullable=True)

    assignment = relationship("ExamAssignment", back_populates="criterion_scores")
    criterion = relationship("ExamCriterion")


class ExamSubmissionEvidence(Base):
    __tablename__ = "exam_submission_evidences"

    id = Column(Integer, primary_key=True)
    assignment_id = Column(Integer, ForeignKey("exam_assignments.id", ondelete="CASCADE"), nullable=False)
    evidence_type = Column(String(20), nullable=False)  # url / note / file
    title = Column(String(200), nullable=True)
    content = Column(Text, nullable=False, default="")
    file_path = Column(String(500), nullable=True)
    original_filename = Column(String(255), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    assignment = relationship("ExamAssignment", back_populates="evidences")


# ─── Wiki ────────────────────────────────────────────────────

WIKI_EDIT_MODES = {
    "owner": "作成者のみ編集可",
    "members": "共有メンバーも編集可",
}

WIKI_VISIBILITY = {
    "private": "個人（自分のみ）",
    "group":   "グループ",
    "all":     "全体（組織内全員）",
}


class WikiPage(Base):
    __tablename__ = "wiki_pages"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False, default="")
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True)
    visibility = Column(String(20), default="private", nullable=False)  # private / group / all
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    edit_mode = Column(String(20), default="owner", nullable=False)  # owner / members
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    group = relationship("Group")
    creator = relationship("User", foreign_keys=[created_by])


# ─── 年間育成計画 ───────────────────────────────────────────────

PLAN_TYPES = {
    "skill": "スキル習得",
    "business_area": "業務エリア完了",
    "certification": "資格取得",
    "exam": "試験合格",
}


class AnnualPlanItem(Base):
    """年間育成計画: 業務エリア完了・資格取得・試験合格の目標日を管理する
    （スキル習得の目標は既存の SkillGoal をそのまま利用するため、ここには含めない）"""
    __tablename__ = "annual_plan_items"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plan_type = Column(String(20), nullable=False)  # business_area / certification / exam
    business_map_area_id = Column(Integer, ForeignKey("business_map_areas.id", ondelete="CASCADE"), nullable=True)
    certification_catalog_id = Column(Integer, ForeignKey("certification_catalog.id", ondelete="CASCADE"), nullable=True)
    exam_id = Column(Integer, ForeignKey("exams.id", ondelete="CASCADE"), nullable=True)
    target_date = Column(Date, nullable=False)
    note = Column(String(200), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    user = relationship("User", foreign_keys=[user_id])
    business_map_area = relationship("BusinessMapArea", foreign_keys=[business_map_area_id])
    certification_catalog = relationship("CertificationCatalog", foreign_keys=[certification_catalog_id])
    exam = relationship("Exam", foreign_keys=[exam_id])
