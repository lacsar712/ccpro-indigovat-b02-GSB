from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional
import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2.utils import markupsafe
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user
from app.db import get_db
from app.models import DipLot, Vat, Workshop
from app.services.vat_rules import (
    VatRuleError,
    validate_vat_reassign,
    validate_vat_status_change,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _tojson(value):
    return markupsafe.Markup(json.dumps(value, ensure_ascii=False))


templates.env.filters["tojson"] = _tojson

STATUS_LABELS = {
    Vat.STATUS_IDLE: "闲置",
    Vat.STATUS_REDUCING: "还原中",
    Vat.STATUS_READY: "可染色",
}


def render(request: Request, name: str, context: dict, status_code: int = 200):
    ctx = {k: v for k, v in context.items() if k != "request"}
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def _need_login(request: Request, db: Session):
    return get_current_user(request, db)


def _spark_points(lots: list[DipLot], width: int = 72, height: int = 28) -> list[dict]:
    """把 redox 序列压成 sparkline 坐标（无有效读数则空）。"""
    vals = [float(l.redoxMv) for l in lots if l.redoxMv is not None]
    if not vals:
        return []
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    n = len(vals)
    pts = []
    for i, v in enumerate(vals):
        x = 0 if n == 1 else round(i * (width - 1) / (n - 1), 2)
        y = round(height - 1 - ((v - lo) / span) * (height - 1), 2)
        pts.append({"x": x, "y": y})
    return pts


def _vat_payload(vat: Vat) -> dict:
    lots = sorted(vat.lots, key=lambda x: (x.dippedAt, x.id))
    chronological = lots
    latest = lots[-1] if lots else None
    recent = list(reversed(lots[-8:]))  # 展开区展示近几笔
    return {
        "id": vat.id,
        "code": vat.code,
        "dyeType": vat.dyeType,
        "volumeL": float(vat.volumeL),
        "status": vat.status,
        "statusLabel": STATUS_LABELS.get(vat.status, vat.status),
        "workshopId": vat.workshop_id,
        "workshopName": vat.workshop.name if vat.workshop else "",
        "lastRedox": float(latest.redoxMv) if latest and latest.redoxMv is not None else None,
        "lastMeters": float(latest.clothMeters) if latest else None,
        "lastDippedAt": latest.dippedAt.strftime("%Y-%m-%d %H:%M") if latest else None,
        "spark": _spark_points(chronological),
        "recentLots": [
            {
                "id": l.id,
                "dippedAt": l.dippedAt.strftime("%Y-%m-%d %H:%M"),
                "clothMeters": float(l.clothMeters),
                "redoxMv": float(l.redoxMv) if l.redoxMv is not None else None,
            }
            for l in recent
        ],
    }


def _bay_context(
    request: Request,
    db: Session,
    user,
    workshop_id: Optional[int] = None,
    selected_vat: Optional[int] = None,
    error: Optional[str] = None,
):
    # 始终下发全部缸位；工坊仅作前端 chip 筛选，避免切回「全部」时缺数据
    workshops = db.query(Workshop).order_by(Workshop.name).all()
    vats = (
        db.query(Vat)
        .options(joinedload(Vat.workshop), joinedload(Vat.lots))
        .order_by(Vat.code)
        .all()
    )
    return {
        "request": request,
        "user": user,
        "is_superuser": bool(getattr(user, "is_superuser", False)),
        "workshops": [{"id": w.id, "name": w.name, "region": w.region} for w in workshops],
        "vats": [_vat_payload(v) for v in vats],
        "filter_workshop": workshop_id,
        "selected_vat": selected_vat,
        "error": error,
        "status_labels": STATUS_LABELS,
        "active": "bay",
    }


@router.get("/", response_class=HTMLResponse)
async def bay(
    request: Request,
    workshop: Optional[int] = None,
    vat: Optional[int] = None,
    db: Session = Depends(get_db),
):
    user = _need_login(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return render(request, "bay.html", _bay_context(request, db, user, workshop, vat))


@router.post("/bay/vats/{pk}/status", response_class=HTMLResponse)
async def bay_vat_status(
    pk: int,
    request: Request,
    status: str = Form(...),
    workshop: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _need_login(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    item = (
        db.query(Vat)
        .options(joinedload(Vat.workshop), joinedload(Vat.lots))
        .filter(Vat.id == pk)
        .first()
    )
    ws = int(workshop) if workshop.strip() else None
    if not item:
        return RedirectResponse("/", status_code=303)
    error = None
    try:
        latest = item.latest_lot()
        validate_vat_status_change(item, status, latest)
        item.status = status
        db.commit()
        return RedirectResponse(f"/?vat={pk}" + (f"&workshop={ws}" if ws else ""), status_code=303)
    except VatRuleError as exc:
        error = exc.message
        db.rollback()
    return render(
        request,
        "bay.html",
        _bay_context(request, db, user, ws, pk, error),
        status_code=400,
    )


@router.post("/bay/vats/{pk}/lots", response_class=HTMLResponse)
async def bay_log_lot(
    pk: int,
    request: Request,
    dippedAt: str = Form(...),
    clothMeters: str = Form(...),
    redoxMv: str = Form(""),
    workshop: str = Form(""),
    db: Session = Depends(get_db),
):
    user = _need_login(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    item = db.get(Vat, pk)
    ws = int(workshop) if workshop.strip() else None
    if not item:
        return RedirectResponse("/", status_code=303)
    error = None
    try:
        lot = DipLot(
            vat_id=pk,
            dippedAt=datetime.fromisoformat(dippedAt),
            clothMeters=Decimal(clothMeters),
            redoxMv=Decimal(redoxMv) if redoxMv.strip() else None,
        )
        db.add(lot)
        db.commit()
        return RedirectResponse(f"/?vat={pk}" + (f"&workshop={ws}" if ws else ""), status_code=303)
    except (ValueError, InvalidOperation) as exc:
        error = f"浸染记录无效：{exc}"
        db.rollback()
    return render(
        request,
        "bay.html",
        _bay_context(request, db, user, ws, pk, error),
        status_code=400,
    )


# 旧顶栏 CRUD 路径一律回到还原台，避免「换皮表页」残留入口
@router.get("/workshops")
@router.get("/vats")
@router.get("/lots")
@router.get("/home")
async def legacy_redirect():
    return RedirectResponse("/", status_code=303)


@router.post("/bay/vats/{pk}/reassign", response_class=HTMLResponse)
async def bay_vat_reassign(
    pk: int,
    request: Request,
    target_workshop_id: str = Form(...),
    workshop: str = Form(""),
    db: Session = Depends(get_db),
):
    """主管把染缸改挂到另一工坊。

    - 仅主管（is_superuser）可发起；染缸工一律拒绝且不踢登录；
    - 目标坊已占用相同缸号则整笔拒绝，唯一约束兜住两主管并发撞号；
    - 成功后 303 跳到目标坊 chip 并展开该缸，浸染历史仍挂原缸主键。
    """
    user = _need_login(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)

    # 当前筛选 chip：优先用表单回传，否则落到目标坊（成功时），失败渲染再校正
    ws = int(workshop) if workshop.strip() else None

    item = (
        db.query(Vat)
        .options(joinedload(Vat.workshop), joinedload(Vat.lots))
        .filter(Vat.id == pk)
        .first()
    )
    if not item:
        return RedirectResponse("/", status_code=303)

    # 染缸工发起改挂一律拒绝：保留登录会话，还原台仍正常打开
    if not user.is_superuser:
        db.rollback()
        return render(
            request,
            "bay.html",
            _bay_context(
                request, db, user, ws if ws is not None else item.workshop_id, pk,
                "权限不足：仅主管可改挂染缸所属工坊，染缸工发起改挂一律拒绝。",
            ),
            status_code=403,
        )

    try:
        target_id = int(target_workshop_id)
    except (TypeError, ValueError):
        db.rollback()
        return render(
            request,
            "bay.html",
            _bay_context(request, db, user, ws, pk, "未选择目标工坊，改挂未生效。"),
            status_code=400,
        )

    target = db.get(Workshop, target_id)
    if target is None:
        db.rollback()
        return render(
            request,
            "bay.html",
            _bay_context(request, db, user, ws, pk, "目标工坊不存在，改挂未生效。"),
            status_code=400,
        )

    try:
        validate_vat_reassign(db, item, target)
        item.workshop_id = target.id
        db.commit()
    except VatRuleError as exc:
        db.rollback()
        # 留在原坊 chip 并保持面板打开，chip 集合仍完整可数
        return render(
            request,
            "bay.html",
            _bay_context(request, db, user, item.workshop_id, pk, exc.message),
            status_code=400,
        )
    except IntegrityError:
        # 两主管近乎同时把同缸号缸改挂进同一目标坊：唯一约束保证至多一笔成功
        db.rollback()
        db.refresh(item)
        return render(
            request,
            "bay.html",
            _bay_context(
                request, db, user, item.workshop_id, pk,
                f"目标坊「{target.name}」缸号 {item.code} 刚被占用（并发冲突），"
                "整笔拒绝：本次改挂未生效。",
            ),
            status_code=409,
        )
    except OperationalError as exc:
        # 提交期序列化失败（Postgres 死锁/序列化异常、SQLite 库锁等）：按撞号回退处理
        db.rollback()
        db.refresh(item)
        pgcode = getattr(exc.orig, "pgcode", None)
        transient = pgcode in ("40001", "40P01", "55P03") or "locked" in str(exc.orig).lower()
        if not transient:
            raise
        return render(
            request,
            "bay.html",
            _bay_context(
                request, db, user, item.workshop_id, pk,
                f"目标坊「{target.name}」此刻并发改挂过多，缸号 {item.code} 未写入，"
                "整笔拒绝：请刷新后重试。",
            ),
            status_code=409,
        )

    # 成功：落到目标坊 chip 并展开该缸；缸位条工坊名由重渲染的 payload 同步
    return RedirectResponse(f"/?workshop={target.id}&vat={pk}", status_code=303)
