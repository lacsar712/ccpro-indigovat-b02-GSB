"""染缸状态与改挂业务规则。"""

from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models import DipLot, Vat, Workshop


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


def validate_vat_reassign(db: Session, vat: Vat, target_workshop: Workshop) -> None:
    """主管改挂染缸到目标工坊的业务规则。

    - 可染色（ready）染缸正在作业，禁止改挂；
    - 目标坊不得存在相同缸号 code，否则整笔拒绝（不迁、不改号）；
    - 并发兜底由 uniq_vat_code_per_workshop 唯一约束承担，端点捕获 IntegrityError。

    浸染历史挂在染缸主键上，改挂不动 DipLot，迁入后仍可在原缸上查询。
    """
    if vat.status == Vat.STATUS_READY:
        raise VatRuleError(
            f"该缸当前为「可染色」状态，正在作业，禁止改挂工坊；请先改回闲置或还原中再迁移。"
        )

    if vat.workshop_id == target_workshop.id:
        raise VatRuleError("该染缸已在本工坊，无需改挂。")

    clash = (
        db.query(Vat)
        .filter(Vat.workshop_id == target_workshop.id, Vat.code == vat.code)
        .first()
    )
    if clash is not None:
        raise VatRuleError(
            f"目标坊「{target_workshop.name}」已占用缸号 {vat.code}，整笔拒绝："
            "同坊缸号不可重复，本次改挂未生效。"
        )
