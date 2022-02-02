from typing import Callable, List
from binaryninja import (
    LLIL_TEMP,
    Architecture,
    LowLevelILFlagCondition,
    LowLevelILLabel,
    LowLevelILOperation,
    LowLevelILFunction,
    LowLevelILInstruction,
    ExpressionIndex,
    RegisterType,
)

from .instructions import IMMEDIATE_MODE, INDIRECT_AUTOINCREMENT_MODE, REGISTER_MODE

SourceOperandsIL: List[
    Callable[
        [LowLevelILFunction, int | None, RegisterType | None, int | None],
        ExpressionIndex,
    ]
] = [
    # REGISTER_MODE
    lambda il, width, reg, value: il.reg(width, reg),
    # INDEXED_MODE
    lambda il, width, reg, value: il.load(
        width, il.add(2, il.reg(2, reg), il.const(2, value))
    ),
    # INDIRECT_REGISTER_MODE
    lambda il, width, reg, value: il.load(width, il.reg(2, reg)),
    # INDIRECT_AUTOINCREMENT_MODE
    lambda il, width, reg, value: il.load(width, il.reg(2, reg)),
    # SYMBOLIC_MODE
    lambda il, width, reg, value: il.load(
        width, il.add(2, il.reg(2, "pc"), il.const(2, value))
    ),
    # ABSOLUTE_MODE
    lambda il, width, reg, value: il.load(width, il.const_pointer(2, value)),
    # IMMEDIATE_MODE
    lambda il, width, reg, value: il.const(width, value),
    # CONSTANT_MODE0
    lambda il, width, reg, value: il.const(width, 0),
    # CONSTANT_MODE1
    lambda il, width, reg, value: il.const(width, 1),
    # CONSTANT_MODE2
    lambda il, width, reg, value: il.const(width, 2),
    # CONSTANT_MODE4
    lambda il, width, reg, value: il.const(width, 4),
    # CONSTANT_MODE8
    lambda il, width, reg, value: il.const(width, 8),
    # CONSTANT_MODE_NEG1
    lambda il, width, reg, value: il.const(width, -1),
]

DestOperandsIL = [
    # REGISTER_MODE
    lambda il, width, reg, value, src: il.set_reg(2, reg, src),
    # INDEXED_MODE
    lambda il, width, reg, value, src: il.store(
        width, il.add(2, il.reg(2, reg), il.const(2, value)), src
    ),
    # INDIRECT_REGISTER_MODE
    lambda il, width, reg, value, src: il.unimplemented(),
    # INDIRECT_AUTOINCREMENT_MODE
    lambda il, width, reg, value, src: il.unimplemented(),
    # SYMBOLIC_MODE
    lambda il, width, reg, value, src: il.unimplemented(),
    # ABSOLUTE_MODE
    lambda il, width, reg, value, src: il.store(width, il.const_pointer(2, value), src),
    # IMMEDIATE_MODE
    lambda il, width, reg, value, src: il.store(width, il.const_pointer(2, value), src),
]


def cond_branch(il: LowLevelILFunction, cond: ExpressionIndex, dest: ExpressionIndex):
    d = LowLevelILInstruction.create(il, dest)

    t = il.get_label_for_address(Architecture["msp430"], d.constant)

    if t is None:
        # t is not an address in the current function scope.
        t = LowLevelILLabel()
        indirect = True
    else:
        indirect = False

    f_label_found = True

    f = il.get_label_for_address(Architecture["msp430"], il.current_address + 2)

    if f is None:
        f = LowLevelILLabel()
        f_label_found = False

    il.append(il.if_expr(cond, t, f))

    if indirect:
        # If the destination is not in the current function,
        # then a jump, rather than a goto, needs to be added to
        # the IL.
        il.mark_label(t)
        il.append(il.jump(d.expr_index))

    if not f_label_found:
        il.mark_label(f)


def jump(il: LowLevelILFunction, dest: ExpressionIndex) -> ExpressionIndex:
    d = LowLevelILInstruction.create(il, dest)
    label = None

    if d.operation == LowLevelILOperation.LLIL_CONST:
        label = il.get_label_for_address(Architecture["msp430"], d.constant)

    if label is None:
        return il.jump(d.expr_index)
    else:
        return il.goto(label)


class Lifter:
    @classmethod
    def lift(cls, il, instr):
        if hasattr(cls, "lift_" + instr.mnemonic):
            getattr(cls, "lift_" + instr.mnemonic)(il, instr)
        else:
            il.append(il.unimplemented())

    @staticmethod
    def lift_type1(il, op, src, dst, flags=None):
        left = SourceOperandsIL[src.mode](il, src.width, src.target, src.value)

        right = SourceOperandsIL[dst.mode](il, dst.width, dst.target, dst.value)

        operation = op(src.width, left, right, flags=flags)

        return operation

    @staticmethod
    def autoincrement(il, src):
        if src.mode == INDIRECT_AUTOINCREMENT_MODE:
            il.append(
                il.set_reg(
                    2,
                    src.target,
                    il.add(src.width, il.reg(2, src.target), il.const(2, src.width)),
                )
            )

    @staticmethod
    def lift_add(il, instr):
        add = Lifter.lift_type1(il, il.add, instr.src, instr.dst, flags="*")

        il.append(
            DestOperandsIL[instr.dst.mode](
                il, instr.dst.width, instr.dst.target, instr.dst.value, add
            )
        )

        Lifter.autoincrement(il, instr.src)

    @staticmethod
    def lift_addc(il, instr):
        add = Lifter.lift_type1(il, il.add, instr.src, instr.dst, flags="*")

        addc = il.add(instr.src.width, add, il.flag("c"), flags="*")

        il.append(
            DestOperandsIL[instr.dst.mode](
                il, instr.dst.width, instr.dst.target, instr.dst.value, addc
            )
        )

        Lifter.autoincrement(il, instr.src)

    @staticmethod
    def lift_and(il, instr):
        and_expr = Lifter.lift_type1(il, il.and_expr, instr.src, instr.dst)

        il.append(
            DestOperandsIL[instr.dst.mode](
                il, instr.dst.width, instr.dst.target, instr.dst.value, and_expr
            )
        )

        Lifter.autoincrement(il, instr.src)

    @staticmethod
    def lift_bic(il, instr):
        left = SourceOperandsIL[instr.dst.mode](
            il, instr.dst.width, instr.dst.target, instr.dst.value
        )

        right = il.not_expr(
            2,
            SourceOperandsIL[instr.src.mode](
                il, instr.src.width, instr.src.target, instr.src.value
            ),
        )

        and_expr = il.and_expr(instr.src.width, left, right)

        il.append(
            DestOperandsIL[instr.dst.mode](
                il, instr.dst.width, instr.dst.target, instr.dst.value, and_expr
            )
        )

        Lifter.autoincrement(il, instr.src)

    @staticmethod
    def lift_bis(il, instr):
        bis = Lifter.lift_type1(il, il.or_expr, instr.src, instr.dst)

        il.append(
            DestOperandsIL[instr.dst.mode](
                il, instr.dst.width, instr.dst.target, instr.dst.value, bis
            )
        )

        Lifter.autoincrement(il, instr.src)

    @staticmethod
    def lift_bit(il, instr):
        bit = Lifter.lift_type1(il, il.and_expr, instr.src, instr.dst)

        il.append(
            DestOperandsIL[instr.dst.mode](
                il, instr.dst.width, instr.dst.target, instr.dst.value, bit
            )
        )

        Lifter.autoincrement(il, instr.src)

    @staticmethod
    def lift_br(il, instr):
        target = SourceOperandsIL[instr.src.mode](
            il, instr.src.width, instr.src.target, instr.src.value
        )

        il.append(jump(il, target))

        Lifter.autoincrement(il, instr.src)

    @staticmethod
    def lift_call(il, instr):
        if instr.src.mode == INDIRECT_AUTOINCREMENT_MODE:
            # autoincrement mode is special in that prior to making the call,
            # the register needs to be incremented. This requires a temp register,
            # so that the original value of the register can be preserved while
            # the register is incremented prior to actually making the call.
            temp_expr = il.set_reg(2, LLIL_TEMP(0), il.reg(2, instr.src.target))

            call_expr = il.call(il.load(2, il.reg(2, LLIL_TEMP(0))))

            inc_expr = il.set_reg(
                2,
                instr.src.target,
                il.add(2, il.reg(2, instr.src.target), il.const(2, 2)),
            )

            il.append(temp_expr)
            il.append(inc_expr)

        elif instr.src.mode == IMMEDIATE_MODE:
            call_expr = il.call(il.const_pointer(2, instr.src.value))

        else:
            call_expr = il.call(
                SourceOperandsIL[instr.src.mode](
                    il, 2, instr.src.target, instr.src.value
                )
            )

        il.append(call_expr)

    @staticmethod
    def lift_cmp(il, instr):
        sub = Lifter.lift_type1(il, il.sub, instr.src, instr.dst, flags="*")

        il.append(sub)

        Lifter.autoincrement(il, instr.src)

    @staticmethod
    def lift_dadd(il, instr):
        il.append(il.unimplemented())

    @staticmethod
    def lift_dint(il, instr):
        pass

    @staticmethod
    def lift_hlt(il, instr):
        il.append(il.no_ret())

    @staticmethod
    def lift_jge(il, instr):
        cond_branch(
            il,
            il.flag_condition(LowLevelILFlagCondition.LLFC_SGE),
            il.const(2, instr.src.value),
        )

    @staticmethod
    def lift_jhs(il, instr):
        cond_branch(
            il,
            il.flag_condition(LowLevelILFlagCondition.LLFC_ULE),
            il.const(2, instr.src.value),
        )

    @staticmethod
    def lift_jl(il, instr):
        cond_branch(
            il,
            il.flag_condition(LowLevelILFlagCondition.LLFC_SLT),
            il.const(2, instr.src.value),
        )

    @staticmethod
    def lift_jlo(il, instr):
        cond_branch(
            il,
            il.flag_condition(LowLevelILFlagCondition.LLFC_UGT),
            il.const(2, instr.src.value),
        )

    @staticmethod
    def lift_jmp(il, instr):
        il.append(jump(il, il.const(2, instr.src.value)))

    @staticmethod
    def lift_jn(il, instr):
        cond_branch(
            il,
            il.compare_equal(0, il.flag("n"), il.const(0, 1)),
            il.const(2, instr.src.value),
        )

    @staticmethod
    def lift_jnz(il, instr):
        cond_branch(
            il,
            il.flag_condition(LowLevelILFlagCondition.LLFC_NE),
            il.const(2, instr.src.value),
        )

    @staticmethod
    def lift_jz(il, instr):
        cond_branch(
            il,
            il.flag_condition(LowLevelILFlagCondition.LLFC_E),
            il.const(2, instr.src.value),
        )

    @staticmethod
    def lift_mov(il, instr):
        # avoid setting stack pointer to a constant
        if (
            instr.src.mode == IMMEDIATE_MODE
            and instr.dst.target == "sp"
            and instr.dst.mode == REGISTER_MODE
        ):
            return

        src = SourceOperandsIL[instr.src.mode](
            il, instr.src.width, instr.src.target, instr.src.value
        )

        il.append(
            DestOperandsIL[instr.dst.mode](
                il, instr.dst.width, instr.dst.target, instr.dst.value, src
            )
        )

        Lifter.autoincrement(il, instr.src)

    @staticmethod
    def lift_pop(il, instr):
        il.append(
            DestOperandsIL[instr.src.mode](
                il, instr.src.width, instr.src.target, instr.src.value, il.pop(2)
            )
        )

        Lifter.autoincrement(il, instr.src)

    @staticmethod
    def lift_push(il, instr):
        il.append(
            il.push(
                2,
                SourceOperandsIL[instr.src.mode](
                    il, instr.src.width, instr.src.target, instr.src.value
                ),
            )
        )

        Lifter.autoincrement(il, instr.src)

    @staticmethod
    def lift_ret(il, instr):
        il.append(il.ret(il.pop(2)))

    @staticmethod
    def lift_reti(il, instr):
        il.append(il.set_reg(2, "sr", il.pop(2)))
        il.append(il.ret(il.pop(2)))

    @staticmethod
    def lift_rra(il, instr):
        left = SourceOperandsIL[instr.src.mode](
            il, instr.src.width, instr.src.target, instr.src.value
        )

        right = il.const(1, 1)

        rra = il.arith_shift_right(2, left, right, flags="cnz")

        dst = DestOperandsIL[instr.src.mode](
            il, instr.src.width, instr.src.target, instr.src.value, rra
        )

        il.append(dst)

        il.append(il.set_flag("v", il.const(0, 0)))

        Lifter.autoincrement(il, instr.src)

    @staticmethod
    def lift_rrc(il, instr):
        left = SourceOperandsIL[instr.src.mode](
            il, instr.src.width, instr.src.target, instr.src.value
        )

        right = il.const(1, 1)

        rrc = il.rotate_right_carry(2, left, right, il.flag("c"), flags="*")

        il.append(
            DestOperandsIL[instr.src.mode](
                il, instr.src.width, instr.src.target, instr.src.value, rrc
            )
        )

        Lifter.autoincrement(il, instr.src)

    @staticmethod
    def lift_sub(il, instr):
        sub = Lifter.lift_type1(il, il.sub, instr.dst, instr.src, flags="*")

        il.append(
            DestOperandsIL[instr.dst.mode](
                il, instr.dst.width, instr.dst.target, instr.dst.value, sub
            )
        )

        Lifter.autoincrement(il, instr.src)

    @staticmethod
    def lift_subc(il, instr):
        sub = Lifter.lift_type1(il, il.sub, instr.src, instr.dst, flags="*")

        subc = il.sub(
            instr.src.width, sub, il.not_expr(instr.src.width, il.flag("c")), flags="*"
        )

        il.append(
            DestOperandsIL[instr.dst.mode](
                il, instr.dst.width, instr.dst.target, instr.dst.value, subc
            )
        )

        Lifter.autoincrement(il, instr.src)

    @staticmethod
    def lift_swpb(il, instr):
        left = SourceOperandsIL[instr.src.mode](
            il, 2, instr.src.target, instr.src.value
        )

        right = il.const(1, 8)

        rotate = il.rotate_left(2, left, right)

        il.append(
            DestOperandsIL[instr.src.mode](
                il, 2, instr.src.target, instr.src.value, rotate
            )
        )

        Lifter.autoincrement(il, instr.src)

    @staticmethod
    def lift_sxt(il, instr):
        src = SourceOperandsIL[instr.src.mode](il, 1, instr.src.target, instr.src.value)

        sxt = il.sign_extend(2, src, flags="*")

        il.append(
            DestOperandsIL[instr.src.mode](
                il, 2, instr.src.target, instr.src.value, sxt
            )
        )

        Lifter.autoincrement(il, instr.src)

    @staticmethod
    def lift_xor(il, instr):
        xor = Lifter.lift_type1(il, il.xor_expr, instr.src, instr.dst, flags="*")

        il.append(
            DestOperandsIL[instr.dst.mode](
                il, instr.dst.width, instr.dst.target, instr.dst.value, xor
            )
        )

        Lifter.autoincrement(il, instr.src)
