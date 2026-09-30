"""染缸状态与工坊改挂业务规则。"""

from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models import DipLot, User, Vat, Workshop


class VatRuleError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def assert_can_mark_ready(latest: Optional[DipLot]) -> None:
    """不能将染缸标为 ready，除非最新浸染批次 redoxMv 已填且 <= -500。"""
    if latest is None or latest.redoxMv is None or Decimal(latest.redoxMv) > Decimal("-500"):
        raise VatRuleError(
            "无法设为可染色：最新浸染批次的氧化还原电位为空或高于 -500 mV。"
        )


def validate_vat_status_change(vat: Vat, new_status: str, latest: Optional[DipLot]) -> None:
    if new_status == Vat.STATUS_READY:
        assert_can_mark_ready(latest)


def rehang_vat(
    db: Session,
    actor: User,
    vat: Vat,
    target_workshop_id: int,
    new_code: str,
) -> None:
    """校验并执行「改挂」：把染缸迁到目标坊并改缸号（本函数不 commit）。

    - 仅主管（is_superuser）可发起；染缸工一律拒绝；
    - 可染色（ready）缸禁止改挂；
    - 目标坊须存在且不同于原坊，缸号非空；
    - 目标坊已占用相同缸号则整笔拒绝（友好报错）；
      数据库 (workshop_id, code) 唯一约束再兜底近乎同时的并发撞号。

    浸染批次以外键挂在染缸主键上，迁移不改主键，历史自然随缸保留。
    """
    if not actor.is_superuser:
        raise VatRuleError("仅主管可发起改挂；染缸工无权迁移染缸所属工坊，整笔拒绝。")

    if vat.status == Vat.STATUS_READY:
        raise VatRuleError(
            "该缸当前为可染色状态，禁止改挂；请先改为闲置或还原中再迁移。"
        )

    target = db.get(Workshop, target_workshop_id)
    if target is None:
        raise VatRuleError("目标工坊不存在，改挂整笔拒绝。")
    if target.id == vat.workshop_id:
        raise VatRuleError("目标工坊与当前工坊相同，无需改挂。")

    code = (new_code or "").strip()
    if not code:
        raise VatRuleError("缸号不能为空，改挂整笔拒绝。")
    if len(code) > 40:
        raise VatRuleError("缸号过长（至多 40 个字符），改挂整笔拒绝。")

    clash = (
        db.query(Vat.id)
        .filter(Vat.workshop_id == target.id, Vat.code == code)
        .first()
    )
    if clash is not None:
        raise VatRuleError(
            f"目标工坊「{target.name}」已占用缸号 {code}，缸号冲突，整笔拒绝："
            "该缸仍留在原坊。"
        )

    vat.workshop_id = target.id
    vat.code = code
