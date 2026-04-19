from datetime import timedelta
from fastapi.templating import Jinja2Templates
import models

templates = Jinja2Templates(directory="templates")


def _jst(dt, fmt: str = "%Y/%m/%d %H:%M") -> str:
    if dt is None:
        return "-"
    return (dt + timedelta(hours=9)).strftime(fmt)


templates.env.filters["jst"] = _jst
templates.env.globals["SKILL_LEVELS"] = models.SKILL_LEVELS
templates.env.globals["LEVEL_COLORS"] = models.LEVEL_COLORS
templates.env.globals["SKILL_TIERS"] = models.SKILL_TIERS
templates.env.globals["TIER_COLORS"] = models.TIER_COLORS
templates.env.globals["TIER_ICONS"] = models.TIER_ICONS
templates.env.globals["TIER_DESCRIPTIONS"] = models.TIER_DESCRIPTIONS
templates.env.globals["APPROVAL_STATUS"] = models.APPROVAL_STATUS
templates.env.globals["APPROVAL_STATUS_COLORS"] = models.APPROVAL_STATUS_COLORS
