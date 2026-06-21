from typing import Optional
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

import models
import auth
from database import get_db
from template_engine import templates

router = APIRouter()


# ─── 一覧 ─────────────────────────────────────────────────────────

@router.get("/tickets", response_class=HTMLResponse)
def tickets_list(
    request: Request,
    status: str = "",
    ticket_type: str = "",
    db: Session = Depends(get_db),
):
    user = auth.require_approved(request, db)
    q = db.query(models.Ticket)

    if user.role == "admin":
        # Admin は全チケットを見る
        if status:
            q = q.filter(models.Ticket.status == status)
        if ticket_type:
            q = q.filter(models.Ticket.ticket_type == ticket_type)
        tickets = q.order_by(models.Ticket.updated_at.desc()).all()
    else:
        # 一般ユーザー・Managerは自分のチケットのみ
        q = q.filter(models.Ticket.created_by == user.id)
        if status:
            q = q.filter(models.Ticket.status == status)
        if ticket_type:
            q = q.filter(models.Ticket.ticket_type == ticket_type)
        tickets = q.order_by(models.Ticket.updated_at.desc()).all()

    return templates.TemplateResponse(request, "tickets.html", {
        "current_user": user,
        "tickets": tickets,
        "sel_status": status,
        "sel_type": ticket_type,
        "TICKET_TYPES": models.TICKET_TYPES,
        "TICKET_STATUS": models.TICKET_STATUS,
        "TICKET_STATUS_COLORS": models.TICKET_STATUS_COLORS,
        "TICKET_PRIORITY": models.TICKET_PRIORITY,
        "TICKET_PRIORITY_COLORS": models.TICKET_PRIORITY_COLORS,
    })


# ─── 作成（AJAX） ──────────────────────────────────────────────────

@router.post(
    "/api/tickets",
    tags=["Tickets"],
    operation_id="create_ticket",
    summary="問い合わせ・要望を新規作成",
    description="種別（問い合わせ/要望）・優先度・タイトル・本文を指定してチケットを作成します。\n\n**権限**: 全ロール。",
)
async def create_ticket(
    request: Request,
    title: str = Form(...),
    ticket_type: str = Form("inquiry"),
    priority: str = Form("medium"),
    message: str = Form(...),
    db: Session = Depends(get_db),
):
    user = auth.require_approved(request, db)
    if not title.strip() or not message.strip():
        return JSONResponse({"ok": False, "error": "タイトルとメッセージを入力してください"}, status_code=400)

    ticket = models.Ticket(
        title=title.strip(),
        ticket_type=ticket_type,
        priority=priority,
        created_by=user.id,
        unread_admin=True,
        unread_user=False,
    )
    db.add(ticket)
    db.flush()

    msg = models.TicketMessage(
        ticket_id=ticket.id,
        user_id=user.id,
        message=message.strip(),
    )
    db.add(msg)
    db.commit()
    return JSONResponse({"ok": True, "ticket_id": ticket.id})


# ─── 詳細（チャット画面） ──────────────────────────────────────────

@router.get("/tickets/{tid}", response_class=HTMLResponse)
def ticket_detail(tid: int, request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    ticket = db.query(models.Ticket).filter(models.Ticket.id == tid).first()
    if not ticket:
        return RedirectResponse("/tickets", status_code=303)
    # アクセス制御: 作成者 or Admin
    if ticket.created_by != user.id and user.role != "admin":
        return RedirectResponse("/tickets", status_code=303)

    # 既読処理
    if user.role == "admin" and ticket.unread_admin:
        ticket.unread_admin = False
        db.commit()
    elif ticket.created_by == user.id and ticket.unread_user:
        ticket.unread_user = False
        db.commit()

    return templates.TemplateResponse(request, "ticket_detail.html", {
        "current_user": user,
        "ticket": ticket,
        "TICKET_TYPES": models.TICKET_TYPES,
        "TICKET_STATUS": models.TICKET_STATUS,
        "TICKET_STATUS_COLORS": models.TICKET_STATUS_COLORS,
        "TICKET_PRIORITY": models.TICKET_PRIORITY,
        "TICKET_PRIORITY_COLORS": models.TICKET_PRIORITY_COLORS,
    })


# ─── メッセージ送信（AJAX） ────────────────────────────────────────

@router.post(
    "/api/tickets/{tid}/message",
    tags=["Tickets"],
    operation_id="post_ticket_message",
    summary="チケットにメッセージを投稿",
    description="既存チケットのスレッドにメッセージを追加します。\n\n**権限**: 全ロール（自分のチケットのみ）/ Admin（全チケット）。",
)
def send_message(
    tid: int,
    request: Request,
    message: str = Form(...),
    db: Session = Depends(get_db),
):
    user = auth.require_approved(request, db)
    ticket = db.query(models.Ticket).filter(models.Ticket.id == tid).first()
    if not ticket:
        return JSONResponse({"ok": False, "error": "チケットが見つかりません"}, status_code=404)
    if ticket.created_by != user.id and user.role != "admin":
        return JSONResponse({"ok": False, "error": "権限がありません"}, status_code=403)
    if ticket.status in ("resolved", "closed"):
        return JSONResponse({"ok": False, "error": "クローズされたチケットには返信できません"}, status_code=400)

    msg = models.TicketMessage(
        ticket_id=tid,
        user_id=user.id,
        message=message.strip(),
    )
    db.add(msg)

    # 未読フラグ更新
    if user.role == "admin":
        ticket.unread_user  = True
        ticket.unread_admin = False
        if ticket.status == "open":
            ticket.status = "in_progress"
    else:
        ticket.unread_admin = True

    db.commit()

    return JSONResponse({
        "ok": True,
        "message_id": msg.id,
        "user_name": user.display_name or user.username,
        "avatar": user.avatar_path,
        "initial": (user.display_name or user.username)[0].upper(),
        "is_admin": user.role == "admin",
        "created_at": msg.created_at.strftime("%Y/%m/%d %H:%M") if msg.created_at else "",
    })


# ─── ステータス変更（Admin） ───────────────────────────────────────

@router.post(
    "/api/tickets/{tid}/status",
    tags=["Tickets"],
    operation_id="update_ticket_status",
    summary="チケットのステータスを更新",
    description="チケットのステータス（対応中・解決済み・クローズ等）を更新します。\n\n**権限**: Admin。",
)
def update_status(
    tid: int,
    request: Request,
    status: str = Form(...),
    db: Session = Depends(get_db),
):
    user = auth.require_approved(request, db)
    if user.role != "admin":
        return JSONResponse({"ok": False, "error": "Admin権限が必要です"}, status_code=403)
    ticket = db.query(models.Ticket).filter(models.Ticket.id == tid).first()
    if not ticket:
        return JSONResponse({"ok": False, "error": "チケットが見つかりません"}, status_code=404)
    if status not in models.TICKET_STATUS:
        return JSONResponse({"ok": False, "error": "無効なステータスです"}, status_code=400)
    ticket.status = status
    db.commit()
    return JSONResponse({
        "ok": True,
        "status": status,
        "status_label": models.TICKET_STATUS[status],
        "status_color": models.TICKET_STATUS_COLORS[status],
    })


# ─── 未読件数 API ─────────────────────────────────────────────────

@router.get(
    "/api/tickets/unread-count",
    tags=["Tickets"],
    operation_id="get_ticket_unread_count",
    summary="未読チケット件数を取得",
    description="サイドバーのバッジ表示用に、未対応・未読のチケット件数を返します。\n\n**権限**: 全ロール。",
)
def unread_count(request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    if user.role == "admin":
        # Admin: 解決済み・クローズ以外のチケットが残っていれば常に表示
        count = db.query(models.Ticket).filter(
            models.Ticket.status.notin_(["resolved", "closed"])
        ).count()
    else:
        # User/Manager: Admin からの未読返信がある場合のみ
        count = db.query(models.Ticket).filter(
            models.Ticket.created_by == user.id,
            models.Ticket.unread_user == True,
        ).count()
    return JSONResponse({"count": count})


# ─── ユーザーの最近のチケット（バブルメニュー用） ─────────────────

@router.get(
    "/api/tickets/my-recent",
    tags=["Tickets"],
    operation_id="get_my_recent_tickets",
    summary="自分の直近のチケットを取得",
    description="チャットウィジェットのバブルメニュー用に、自分の直近5件のチケットを返します。\n\n**権限**: 全ロール（自分のチケットのみ）。",
)
def my_recent_tickets(request: Request, db: Session = Depends(get_db)):
    user = auth.require_approved(request, db)
    tickets = db.query(models.Ticket).filter(
        models.Ticket.created_by == user.id
    ).order_by(models.Ticket.updated_at.desc()).limit(5).all()
    return JSONResponse({
        "tickets": [
            {
                "id": t.id,
                "title": t.title,
                "type": models.TICKET_TYPES.get(t.ticket_type, t.ticket_type),
                "status": models.TICKET_STATUS.get(t.status, t.status),
                "status_color": models.TICKET_STATUS_COLORS.get(t.status, "secondary"),
                "unread": t.unread_user,
            }
            for t in tickets
        ]
    })


@router.get(
    "/api/tickets/my-list",
    tags=["Tickets"],
    operation_id="get_my_ticket_list",
    summary="自分のチケット一覧を取得",
    description="チャットウィジェット用に、自分の全チケットと最新メッセージを返します。\n\n**権限**: 全ロール（自分のチケットのみ）。",
)
def my_ticket_list(request: Request, db: Session = Depends(get_db)):
    """チャットウィジェット用: 自分のチケット一覧 + 最新メッセージ"""
    user = auth.require_approved(request, db)
    tickets = db.query(models.Ticket).filter(
        models.Ticket.created_by == user.id
    ).order_by(models.Ticket.updated_at.desc()).all()

    result = []
    for t in tickets:
        last_msg = t.messages[-1] if t.messages else None
        result.append({
            "id": t.id,
            "title": t.title,
            "type_key": t.ticket_type,
            "type": models.TICKET_TYPES.get(t.ticket_type, t.ticket_type),
            "status_key": t.status,
            "status": models.TICKET_STATUS.get(t.status, t.status),
            "status_color": models.TICKET_STATUS_COLORS.get(t.status, "secondary"),
            "unread": t.unread_user,
            "last_message": last_msg.message[:60] + ("…" if len(last_msg.message) > 60 else "") if last_msg else "",
            "updated_at": t.updated_at.strftime("%m/%d %H:%M") if t.updated_at else "",
        })
    return JSONResponse({"tickets": result})


@router.get(
    "/api/tickets/{tid}/detail",
    tags=["Tickets"],
    operation_id="get_ticket_detail",
    summary="チケット詳細を取得",
    description="チャットウィジェット用に、チケット詳細とメッセージ一覧を返します。\n\n**権限**: 全ロール（自分のチケットのみ）/ Admin（全チケット）。",
)
def ticket_detail_json(tid: int, request: Request, db: Session = Depends(get_db)):
    """チャットウィジェット用: チケット詳細 + メッセージ一覧"""
    user = auth.require_approved(request, db)
    ticket = db.query(models.Ticket).filter(models.Ticket.id == tid).first()
    if not ticket:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    if ticket.created_by != user.id and user.role != "admin":
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)

    # 既読処理
    if user.role == "admin" and ticket.unread_admin:
        ticket.unread_admin = False
        db.commit()
    elif ticket.created_by == user.id and ticket.unread_user:
        ticket.unread_user = False
        db.commit()

    messages = [
        {
            "id": m.id,
            "user_name": m.user.display_name or m.user.username,
            "initial": (m.user.display_name or m.user.username)[0].upper(),
            "avatar": m.user.avatar_path,
            "is_admin": m.user.role == "admin",
            "message": m.message,
            "created_at": m.created_at.strftime("%m/%d %H:%M") if m.created_at else "",
        }
        for m in ticket.messages
    ]
    return JSONResponse({
        "ok": True,
        "id": ticket.id,
        "title": ticket.title,
        "type": models.TICKET_TYPES.get(ticket.ticket_type, ticket.ticket_type),
        "status_key": ticket.status,
        "status": models.TICKET_STATUS.get(ticket.status, ticket.status),
        "status_color": models.TICKET_STATUS_COLORS.get(ticket.status, "secondary"),
        "can_reply": ticket.status not in ("resolved", "closed"),
        "messages": messages,
    })


# ─── チケット削除 ─────────────────────────────────────────────────

@router.post(
    "/api/tickets/{tid}/delete",
    tags=["Tickets"],
    operation_id="delete_ticket",
    summary="チケットを削除",
    description="チケットを削除します。\n\n**権限**: Admin（全件）/ 投稿者本人（自分のチケットのみ）。",
)
def delete_ticket(tid: int, request: Request, db: Session = Depends(get_db)):
    """チケット削除: Admin は全件、投稿者本人は自分のチケットのみ削除可能"""
    user = auth.require_approved(request, db)
    ticket = db.query(models.Ticket).filter(models.Ticket.id == tid).first()
    if not ticket:
        return JSONResponse({"ok": False, "error": "チケットが見つかりません"}, status_code=404)
    # 権限チェック
    if user.role != "admin" and ticket.user_id != user.id:
        return JSONResponse({"ok": False, "error": "削除権限がありません"}, status_code=403)
    # メッセージも含めて削除
    for msg in ticket.messages:
        db.delete(msg)
    db.delete(ticket)
    db.commit()
    return JSONResponse({"ok": True})
