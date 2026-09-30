import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import DipLot, User, Vat, Workshop

_PWD_SALT = os.environ.get("PWD_SALT", "indigovat-dev-salt").encode("utf-8")


def hash_password(password: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), _PWD_SALT, 120000
    )
    return digest.hex()


def verify_password(plain: str, hashed: str) -> bool:
    return hmac.compare_digest(hash_password(plain), hashed)


def _get_or_create_workshop(db: Session, name: str, region: str, notes: str) -> Workshop:
    ws = db.query(Workshop).filter_by(name=name).first()
    if ws is None:
        ws = Workshop(name=name, region=region, notes=notes)
        db.add(ws)
        db.flush()
    return ws


def _get_or_create_vat(
    db: Session, workshop: Workshop, code: str, dye_type: str, volume: str, status: str
) -> Vat:
    """按 (工坊, 缸号) 幂等取缸；已被改挂走的缸不在本坊，按缺失补一口新缸。"""
    vat = (
        db.query(Vat)
        .filter(Vat.workshop_id == workshop.id, Vat.code == code)
        .first()
    )
    if vat is None:
        vat = Vat(
            workshop_id=workshop.id,
            code=code,
            dyeType=dye_type,
            volumeL=Decimal(volume),
            status=status,
        )
        db.add(vat)
        db.flush()
    return vat


def ensure_seed_data(db: Session) -> None:
    """幂等种子：账号 + 蓝靛湾/清水江样例缸位、电位序列与改挂演示缸。"""
    if not db.query(User).filter_by(username="admin").first():
        db.add(
            User(
                username="admin",
                password_hash=hash_password("123456"),
                is_superuser=True,
            )
        )
    if not db.query(User).filter_by(username="worker").first():
        db.add(
            User(
                username="worker",
                password_hash=hash_password("123456"),
                is_superuser=False,
            )
        )
    db.commit()

    w1 = _get_or_create_workshop(db, "蓝靛湾一号坊", "黔东南", "晨露还原较快")
    w2 = _get_or_create_workshop(db, "清水江二号坊", "黔南", "缸体较深，保温好")

    # 改挂演示缸位布局：
    # - w1.V-03 -> 想迁入 w2，但 w2 已占用同缸号 V-03（预留冲突号），整笔拒绝；
    # - w1.V-04 -> w2 的 V-04 号位预留为空（可迁号），可成功改挂，历史随缸保留；
    # - w2.V-12 为可染色（ready），演示可染色缸禁迁；
    # - w2.V-11 为还原中，反向迁入 w1 不撞号，可作成功改挂。
    v1 = _get_or_create_vat(db, w1, "V-01", "土靛", "800.00", Vat.STATUS_REDUCING)
    v2 = _get_or_create_vat(db, w1, "V-02", "合成靛", "600.00", Vat.STATUS_IDLE)
    v3 = _get_or_create_vat(db, w2, "V-11", "土靛", "900.00", Vat.STATUS_REDUCING)
    v4 = _get_or_create_vat(db, w2, "V-12", "板蓝根靛", "750.00", Vat.STATUS_READY)
    _get_or_create_vat(db, w1, "V-03", "土靛", "650.00", Vat.STATUS_IDLE)
    _get_or_create_vat(db, w2, "V-03", "合成靛", "700.00", Vat.STATUS_IDLE)
    v_movable = _get_or_create_vat(
        db, w1, "V-04", "蓼蓝靛", "720.00", Vat.STATUS_IDLE
    )

    now = datetime.now(timezone.utc)

    def seed_lots(vat: Vat, series):
        """series: (hours_ago, meters, redox or None)；该缸已有历史则不重复补。"""
        if db.query(DipLot).filter_by(vat_id=vat.id).first():
            return
        for hours, meters, redox in series:
            db.add(
                DipLot(
                    vat_id=vat.id,
                    dippedAt=now - timedelta(hours=hours),
                    clothMeters=Decimal(meters),
                    redoxMv=Decimal(redox) if redox is not None else None,
                )
            )

    seed_lots(
        v1,
        [
            (36, "18.00", "-410.00"),
            (28, "22.50", "-455.00"),
            (20, "30.00", "-490.00"),
            (12, "40.00", "-510.00"),
            (8, "45.00", "-520.00"),
        ],
    )
    seed_lots(
        v2,
        [
            (6, "8.00", None),
            (1, "12.00", None),
        ],
    )
    seed_lots(
        v3,
        [
            (40, "25.00", "-390.00"),
            (30, "35.00", "-430.00"),
            (22, "48.00", "-460.00"),
            (14, "60.00", "-480.00"),
        ],
    )
    seed_lots(
        v4,
        [
            (48, "20.00", "-420.00"),
            (32, "28.00", "-470.00"),
            (20, "33.00", "-505.00"),
            (10, "38.50", "-530.00"),
        ],
    )
    # 可迁缸 V-04 带浸染历史：改挂后这些记录仍挂在同一缸主键上可查
    seed_lots(
        v_movable,
        [
            (30, "14.00", "-430.00"),
            (18, "19.50", "-475.00"),
            (5, "24.00", None),
        ],
    )
    db.commit()
