import os
import shutil
import uuid as _uuid
from datetime import datetime

from fastapi import APIRouter, Request, Form, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from sqlalchemy.orm import Session

import models
import auth
from database import get_db
from template_engine import templates
from routers.groups import _get_managed_groups

router = APIRouter(prefix="/certifications")

CERT_UPLOAD_DIR = "/app/data/uploads/certifications"


def _parse_date(value: str):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _save_upload(upload_file: UploadFile):
    os.makedirs(CERT_UPLOAD_DIR, exist_ok=True)
    original_name = upload_file.filename
    ext = os.path.splitext(original_name)[1] if "." in original_name else ""
    saved_name = f"{_uuid.uuid4()}{ext}"
    save_path = os.path.join(CERT_UPLOAD_DIR, saved_name)
    with open(save_path, "wb") as f:
        shutil.copyfileobj(upload_file.file, f)
    return save_path, original_name


# ════════════════════════════════════════════════════════════════
# 自分の資格情報
# ════════════════════════════════════════════════════════════════

@router.get("", response_class=HTMLResponse)
def my_certifications(request: Request, db: Session = Depends(get_db)):
    """自分の資格情報一覧"""
    user = auth.require_approved(request, db)
    certs = (
        db.query(models.Certification)
        .filter(models.Certification.user_id == user.id)
        .order_by(models.Certification.issued_date.desc().nullslast(), models.Certification.id.desc())
        .all()
    )
    catalog_options = (
        db.query(models.CertificationCatalog)
        .filter(models.CertificationCatalog.is_archived == False)
        .order_by(models.CertificationCatalog.category_name, models.CertificationCatalog.name)
        .all()
    )
    return templates.TemplateResponse(request, "certifications.html", {
        "current_user": user,
        "certifications": certs,
        "catalog_options": catalog_options,
        "is_readonly": False,
        "target_user": None,
    })


@router.post("/new")
async def certification_new(
    request: Request,
    catalog_id: int = Form(0),
    custom_name: str = Form(""),
    issuer: str = Form(""),
    issued_date: str = Form(""),
    expiry_date: str = Form(""),
    certificate_number: str = Form(""),
    score: str = Form(""),
    note: str = Form(""),
    upload_file: UploadFile = File(default=None),
    db: Session = Depends(get_db),
):
    """資格情報を登録（資格マスタから選択 or その他で名称を入力）"""
    user = auth.require_approved(request, db)

    catalog_item = None
    if catalog_id:
        catalog_item = (
            db.query(models.CertificationCatalog)
            .filter(models.CertificationCatalog.id == catalog_id)
            .first()
        )

    if catalog_item:
        name = catalog_item.name
        resolved_issuer = issuer.strip() or catalog_item.issuer
    else:
        name = custom_name.strip()
        resolved_issuer = issuer.strip() or None

    if not name:
        return RedirectResponse("/certifications", status_code=303)

    cert = models.Certification(
        user_id=user.id,
        catalog_id=catalog_item.id if catalog_item else None,
        name=name,
        issuer=resolved_issuer,
        issued_date=_parse_date(issued_date),
        expiry_date=_parse_date(expiry_date),
        certificate_number=certificate_number.strip() or None,
        score=int(score) if score.strip().isdigit() else None,
        note=note.strip() or None,
    )

    if upload_file and upload_file.filename:
        save_path, original_name = _save_upload(upload_file)
        cert.file_path = save_path
        cert.original_filename = original_name

    db.add(cert)
    db.commit()
    return RedirectResponse("/certifications", status_code=303)


@router.post("/{cert_id}/edit")
async def certification_edit(
    cert_id: int,
    request: Request,
    catalog_id: int = Form(0),
    custom_name: str = Form(""),
    issuer: str = Form(""),
    issued_date: str = Form(""),
    expiry_date: str = Form(""),
    certificate_number: str = Form(""),
    score: str = Form(""),
    note: str = Form(""),
    upload_file: UploadFile = File(default=None),
    db: Session = Depends(get_db),
):
    """資格情報を編集（本人のみ）"""
    user = auth.require_approved(request, db)
    cert = (
        db.query(models.Certification)
        .filter(models.Certification.id == cert_id, models.Certification.user_id == user.id)
        .first()
    )
    if not cert:
        return RedirectResponse("/certifications", status_code=303)

    catalog_item = None
    if catalog_id:
        catalog_item = (
            db.query(models.CertificationCatalog)
            .filter(models.CertificationCatalog.id == catalog_id)
            .first()
        )

    if catalog_item:
        name = catalog_item.name
        resolved_issuer = issuer.strip() or catalog_item.issuer
    else:
        name = custom_name.strip()
        resolved_issuer = issuer.strip() or None

    if not name:
        return RedirectResponse("/certifications", status_code=303)

    cert.catalog_id = catalog_item.id if catalog_item else None
    cert.name = name
    cert.issuer = resolved_issuer
    cert.issued_date = _parse_date(issued_date)
    cert.expiry_date = _parse_date(expiry_date)
    cert.certificate_number = certificate_number.strip() or None
    cert.score = int(score) if score.strip().isdigit() else None
    cert.note = note.strip() or None

    if upload_file and upload_file.filename:
        if cert.file_path and os.path.exists(cert.file_path):
            os.remove(cert.file_path)
        save_path, original_name = _save_upload(upload_file)
        cert.file_path = save_path
        cert.original_filename = original_name

    db.commit()
    return RedirectResponse("/certifications", status_code=303)


@router.post("/{cert_id}/delete")
def certification_delete(cert_id: int, request: Request, db: Session = Depends(get_db)):
    """資格情報を削除（本人のみ）"""
    user = auth.require_approved(request, db)
    cert = (
        db.query(models.Certification)
        .filter(models.Certification.id == cert_id, models.Certification.user_id == user.id)
        .first()
    )
    if cert:
        if cert.file_path and os.path.exists(cert.file_path):
            os.remove(cert.file_path)
        db.delete(cert)
        db.commit()
    return RedirectResponse("/certifications", status_code=303)


@router.get("/{cert_id}/file")
def certification_file(cert_id: int, request: Request, db: Session = Depends(get_db)):
    """添付ファイルの表示・ダウンロード（本人 or Manager/Admin）"""
    user = auth.require_approved(request, db)
    q = db.query(models.Certification).filter(models.Certification.id == cert_id)
    if user.role not in ("admin", "manager"):
        q = q.filter(models.Certification.user_id == user.id)
    cert = q.first()
    if not cert or not cert.file_path or not os.path.exists(cert.file_path):
        raise HTTPException(status_code=404)

    if user.role == "manager" and cert.user_id != user.id:
        managed_ids = {g.id for g in _get_managed_groups(user, db)}
        is_member = (
            db.query(models.GroupMembership)
            .filter(
                models.GroupMembership.user_id == cert.user_id,
                models.GroupMembership.group_id.in_(managed_ids),
            )
            .first()
        )
        if not is_member:
            raise HTTPException(status_code=403)

    return FileResponse(cert.file_path, filename=cert.original_filename or "certificate")


# ════════════════════════════════════════════════════════════════
# メンバーの資格情報（Manager / Admin）
# ════════════════════════════════════════════════════════════════

@router.get("/members", response_class=HTMLResponse)
def certification_members(request: Request, db: Session = Depends(get_db)):
    """資格マトリクスはスキルマトリクスに統合されたため、そちらへリダイレクトする"""
    auth.require_manager_or_admin(request, db)
    return RedirectResponse("/skills/matrix?tab=cert", status_code=303)


@router.get("/members/{user_id}", response_class=HTMLResponse)
def certification_member_detail(user_id: int, request: Request, db: Session = Depends(get_db)):
    """指定メンバーの資格情報一覧（読み取り専用）"""
    user = auth.require_manager_or_admin(request, db)

    target = db.query(models.User).filter(models.User.id == user_id).first()
    if not target:
        return RedirectResponse("/skills/matrix?tab=cert", status_code=303)

    if user.role == "manager":
        managed_ids = {g.id for g in _get_managed_groups(user, db)}
        is_member = (
            db.query(models.GroupMembership)
            .filter(
                models.GroupMembership.user_id == user_id,
                models.GroupMembership.group_id.in_(managed_ids),
            )
            .first()
        )
        if not is_member:
            return RedirectResponse("/skills/matrix?tab=cert", status_code=303)

    certs = (
        db.query(models.Certification)
        .filter(models.Certification.user_id == user_id)
        .order_by(models.Certification.issued_date.desc().nullslast(), models.Certification.id.desc())
        .all()
    )
    return templates.TemplateResponse(request, "certifications.html", {
        "current_user": user,
        "certifications": certs,
        "catalog_options": [],
        "is_readonly": True,
        "target_user": target,
    })


# ════════════════════════════════════════════════════════════════
# 資格マスタ管理（Manager / Admin）
# ════════════════════════════════════════════════════════════════

@router.get("/catalog", response_class=HTMLResponse)
def certification_catalog_list(request: Request, db: Session = Depends(get_db)):
    """資格マスタ一覧（登録済みの資格を管理）"""
    user = auth.require_manager_or_admin(request, db)
    items = (
        db.query(models.CertificationCatalog)
        .order_by(
            models.CertificationCatalog.is_archived,
            models.CertificationCatalog.issuer,
            models.CertificationCatalog.name,
        )
        .all()
    )
    return templates.TemplateResponse(request, "certification_catalog.html", {
        "current_user": user,
        "items": items,
    })


@router.post("/catalog/new")
def certification_catalog_new(
    request: Request,
    name: str = Form(...),
    issuer: str = Form(""),
    category_name: str = Form(""),
    description: str = Form(""),
    has_score: str = Form(""),
    tier: str = Form("basic"),
    db: Session = Depends(get_db),
):
    """資格マスタに新しい資格を追加"""
    user = auth.require_manager_or_admin(request, db)
    name = name.strip()
    if name:
        existing = (
            db.query(models.CertificationCatalog)
            .filter(models.CertificationCatalog.name == name)
            .first()
        )
        if not existing:
            db.add(models.CertificationCatalog(
                name=name,
                issuer=issuer.strip() or None,
                category_name=category_name.strip() or None,
                description=description.strip() or None,
                has_score=bool(has_score),
                tier=tier if tier in models.SKILL_TIERS else "basic",
                created_by=user.id,
            ))
            db.commit()
    return RedirectResponse("/certifications/catalog", status_code=303)


@router.post("/catalog/{item_id}/edit")
def certification_catalog_edit(
    item_id: int,
    request: Request,
    name: str = Form(...),
    issuer: str = Form(""),
    category_name: str = Form(""),
    description: str = Form(""),
    has_score: str = Form(""),
    tier: str = Form("basic"),
    db: Session = Depends(get_db),
):
    """資格マスタの内容を編集"""
    auth.require_manager_or_admin(request, db)
    item = db.query(models.CertificationCatalog).filter(models.CertificationCatalog.id == item_id).first()
    if item:
        item.name = name.strip()
        item.issuer = issuer.strip() or None
        item.category_name = category_name.strip() or None
        item.description = description.strip() or None
        item.has_score = bool(has_score)
        item.tier = tier if tier in models.SKILL_TIERS else "basic"
        db.commit()
    return RedirectResponse("/certifications/catalog", status_code=303)


@router.post("/catalog/{item_id}/archive")
def certification_catalog_archive(item_id: int, request: Request, db: Session = Depends(get_db)):
    """資格マスタの有効/無効（アーカイブ）を切り替える"""
    auth.require_manager_or_admin(request, db)
    item = db.query(models.CertificationCatalog).filter(models.CertificationCatalog.id == item_id).first()
    if item:
        item.is_archived = not item.is_archived
        db.commit()
    return RedirectResponse("/certifications/catalog", status_code=303)


# ── 資格マスタ 一括エクスポート / インポート（データ管理ページ用） ──────────────

@router.get("/catalog/export")
def certification_catalog_export(request: Request, db: Session = Depends(get_db)):
    """資格マスタを1つのJSONファイルで一括エクスポート"""
    from fastapi.responses import Response as _Response
    import json as _json
    from datetime import datetime as _dt
    auth.require_admin(request, db)

    items = (
        db.query(models.CertificationCatalog)
        .order_by(models.CertificationCatalog.category_name, models.CertificationCatalog.name)
        .all()
    )

    data = {
        "exported_at": _dt.now().isoformat(),
        "certification_catalog": [
            {
                "name": it.name,
                "issuer": it.issuer or "",
                "category_name": it.category_name or "",
                "description": it.description or "",
                "has_score": it.has_score,
                "tier": it.tier,
                "is_archived": it.is_archived,
            }
            for it in items
        ],
    }

    body = _json.dumps(data, ensure_ascii=False, indent=2)
    filename = f"skillmap_certification_catalog_{_dt.now().strftime('%Y%m%d')}.json"
    return _Response(
        content=body.encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post("/catalog/import")
async def certification_catalog_import(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """一括エクスポートJSONファイルから資格マスタを一括インポートする
    （資格名で照合し、既存のものは更新、新規は追加）"""
    import json as _json
    user = auth.require_admin(request, db)

    content = await file.read()
    try:
        data = _json.loads(content.decode("utf-8-sig"))
    except Exception:
        return JSONResponse({"ok": False, "error": "JSON の解析に失敗しました"}, status_code=400)

    added = updated = 0
    for item in data.get("certification_catalog", []):
        name = (item.get("name") or "").strip()
        if not name:
            continue

        existing = (
            db.query(models.CertificationCatalog)
            .filter(models.CertificationCatalog.name == name)
            .first()
        )
        tier = item.get("tier") or "basic"
        if tier not in models.SKILL_TIERS:
            tier = "basic"
        if existing:
            existing.issuer = (item.get("issuer") or "").strip() or None
            existing.category_name = (item.get("category_name") or "").strip() or None
            existing.description = (item.get("description") or "").strip() or None
            existing.has_score = bool(item.get("has_score", False))
            existing.tier = tier
            existing.is_archived = bool(item.get("is_archived", False))
            updated += 1
        else:
            db.add(models.CertificationCatalog(
                name=name,
                issuer=(item.get("issuer") or "").strip() or None,
                category_name=(item.get("category_name") or "").strip() or None,
                description=(item.get("description") or "").strip() or None,
                has_score=bool(item.get("has_score", False)),
                tier=tier,
                is_archived=bool(item.get("is_archived", False)),
                created_by=user.id,
            ))
            added += 1

    db.commit()
    return JSONResponse({"ok": True, "added": added, "updated": updated})
