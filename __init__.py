from __future__ import print_function

import struct
import traceback
import os
import sys

from binaryninja import (
    Architecture, RegisterInfo, InstructionInfo,

    InstructionTextToken, TextToken, IntegerToken, PossibleAddressToken,
    RegisterToken,

    UnconditionalBranch, FunctionReturn, TrueBranch, FalseBranch,
    CallDestination,

    LLIL_TEMP, LLIL_CONST,

    LowLevelILLabel,

    CarryFlagRole, NegativeSignFlagRole, ZeroFlagRole, OverflowFlagRole,

    LLFC_UGE, LLFC_ULT, LLFC_E, LLFC_NE, LLFC_NEG, LLFC_POS, LLFC_SGE,
    LLFC_SLE, LLFC_SLT,

    log_error, log_info)

# Type 1 instructions are those that take two operands.
TYPE1_INSTRUCTIONS = [
    'mov', 'add', 'addc', 'subc', 'sub', 'cmp',
    'dadd', 'bit', 'bic', 'bis', 'xor', 'and'
]

# Type 2 instructions are those that take one operand.
TYPE2_INSTRUCTIONS = [
    'rrc', 'swpb', 'rra', 'sxt', 'push', 'call',
    'reti', 'br'
]

# Type 3 instructions are (un)conditional branches. They do not
# take any operands, as the branch targets are always immediates
# stored in the instruction itself.
TYPE3_INSTRUCTIONS = [
    'jnz', 'jz', 'jlo', 'jhs', 'jn', 'jge', 'jl',
    'jmp'
]

InstructionNames = [
    # No instructions use opcode 0
    None,

    # Type 2 instructions all start with 0x1 but then
    # differentiate by three more bits:
    # 0001 00 XXX .......
    ['rrc', 'swpb', 'rra', 'sxt', 'push', 'call', 'reti'],

    # Type 3 instructions start with either 0x2 or 0x3 and
    # then differentiate with the following three bits:
    # 0010 XXX ..........
    ['jnz', 'jz', 'jlo', 'jhs'],
    # 0011 XXX ..........
    ['jn', 'jge', 'jl', 'jmp'],

    # Type 1 instructions all use the top 4 bits
    # for their opcodes (0x4 - 0xf)
    'mov',
    'add',
    'addc',
    'subc',
    'sub',
    'cmp',
    'dadd',
    'bit',
    'bic',
    'bis',
    'xor',
    'and'
]

# InstructionMask and InstructionMaskShift are used to mask
# off the bits that are used for the opcode of type 2 and 3
# instructions.
InstructionMask = {
    1: 0x380,
    2: 0xc00,
    3: 0xc00,
}

InstructionMaskShift = {
    1: 7,
    2: 10,
    3: 10
}

# Some instructions can be either 2 byte (word) or 1 byte
# operations.
WORD_WIDTH = 0
BYTE_WIDTH = 1

# There are technically only four different operand modes, but
# certain mode/register combinations have different semantic
# meanings.
REGISTER_MODE = 0
INDEXED_MODE = 1
INDIRECT_REGISTER_MODE = 2
INDIRECT_AUTOINCREMENT_MODE = 3
SYMBOLIC_MODE = 4
ABSOLUTE_MODE = 5
IMMEDIATE_MODE = 6
CONSTANT_MODE0 = 7
CONSTANT_MODE1 = 8
CONSTANT_MODE2 = 9
CONSTANT_MODE4 = 10
CONSTANT_MODE8 = 11
CONSTANT_MODE_NEG1 = 12
OFFSET = 13
OperandLengths = [
    0,  # REGISTER_MODE
    2,  # INDEXED_MODE
    0,  # INDIRECT_REGISTER_MODE
    0,  # INDIRECT_AUTOINCREMENT_MODE
    2,  # SYMBOLIC_MODE
    2,  # ABSOLUTE_MODE
    2,  # IMMEDIATE_MODE
    0,  # CONSTANT_MODE0
    0,  # CONSTANT_MODE1
    0,  # CONSTANT_MODE2
    0,  # CONSTANT_MODE4
    0,  # CONSTANT_MODE8
    0,  # CONSTANT_MODE_NEG1
    0,  # OFFSET
]

OperandTokens = [
    lambda reg, value: [    # REGISTER_MODE
        InstructionTextToken(RegisterToken, reg)
    ],
    lambda reg, value: [    # INDEXED_MODE
        InstructionTextToken(IntegerToken, hex(value), value),
        InstructionTextToken(TextToken, '('),
        InstructionTextToken(RegisterToken, reg),
        InstructionTextToken(TextToken, ')')
    ],
    lambda reg, value: [    # INDIRECT_REGISTER_MODE
        InstructionTextToken(TextToken, '@'),
        InstructionTextToken(RegisterToken, reg)
    ],
    lambda reg, value: [    # INDIRECT_AUTOINCREMENT_MODE
        InstructionTextToken(TextToken, '@'),
        InstructionTextToken(RegisterToken, reg),
        InstructionTextToken(TextToken, '+')
    ],
    lambda reg, value: [    # SYMBOLIC_MODE
        InstructionTextToken(PossibleAddressToken, hex(value), value)
    ],
    lambda reg, value: [    # ABSOLUTE_MODE
        InstructionTextToken(TextToken, '&'),
        InstructionTextToken(PossibleAddressToken, hex(value), value)
    ],
    lambda reg, value: [    # IMMEDIATE_MODE
        InstructionTextToken(PossibleAddressToken, hex(value), value)
    ],
    lambda reg, value: [    # CONSTANT_MODE0
        InstructionTextToken(IntegerToken, str(0), 0)
    ],
    lambda reg, value: [    # CONSTANT_MODE1
        InstructionTextToken(IntegerToken, str(1), 1)
    ],
    lambda reg, value: [    # CONSTANT_MODE2
        InstructionTextToken(IntegerToken, str(2), 2)
    ],
    lambda reg, value: [    # CONSTANT_MODE4
        InstructionTextToken(IntegerToken, str(4), 4)
    ],
    lambda reg, value: [    # CONSTANT_MODE8
        InstructionTextToken(IntegerToken, str(8), 8)
    ],
    lambda reg, value: [    # CONSTANT_MODE_NEG1
        InstructionTextToken(IntegerToken, str(-1), -1)
    ],
    lambda reg, value: [    # OFFSET
        InstructionTextToken(PossibleAddressToken, hex(value), value)
    ]
]

Registers = [
    'pc',
    'sp',
    'sr',
    'cg',
    'r4',
    'r5',
    'r6',
    'r7',
    'r8',
    'r9',
    'r10',
    'r11',
    'r12',
    'r13',
    'r14',
    'r15'
]

def GetOperands(instr, instruction):
    if instr in TYPE3_INSTRUCTIONS:
        return None, OFFSET, None, None

    # As is in the same place for Type 1 and 2 instructions
    As = (instruction & 0x30) >> 4

    if instr in TYPE2_INSTRUCTIONS:
        src = Registers[instruction & 0xf]
        dst = None
        Ad = None

    elif instr in TYPE1_INSTRUCTIONS:
        src = Registers[(instruction & 0xf00) >> 8]
        dst = Registers[instruction & 0xf]
        Ad = (instruction & 0x80) >> 7

    if src == 'pc':
        if As == INDEXED_MODE:
            As = SYMBOLIC_MODE
        elif As == INDIRECT_AUTOINCREMENT_MODE:
            As = IMMEDIATE_MODE

    elif src == 'cg':
        if As == REGISTER_MODE:
            As = CONSTANT_MODE0
        elif As == INDEXED_MODE:
            As = CONSTANT_MODE1
        elif As == INDIRECT_REGISTER_MODE:
            As = CONSTANT_MODE2
        else:
            As = CONSTANT_MODE_NEG1

    elif src == 'sr':
        if As == INDEXED_MODE:
            As = ABSOLUTE_MODE
        elif As == INDIRECT_REGISTER_MODE:
            As = CONSTANT_MODE4
        elif As == INDIRECT_AUTOINCREMENT_MODE:
            As = CONSTANT_MODE8

    if dst and dst == 'sr':
        if Ad == INDEXED_MODE:
            Ad = ABSOLUTE_MODE

    return src, As, dst, Ad

def GetRegisterValues(instr, instruction):
    if instr in TYPE1_INSTRUCTIONS:
        src = (instruction & 0xf00) >> 8
        dst = (instruction & 0xf)
    elif instr in TYPE2_INSTRUCTIONS:
        src = instruction & 0xf
        dst = None
    else:
        src = None
        dst = None

    return src, dst

SourceOperandsIL = [
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
        width, il.add(2, il.reg(2, 'pc'), il.const(2, value))
    ),

    # ABSOLUTE_MODE
    lambda il, width, reg, value: il.load(width, il.const(2, value)),

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
    lambda il, width, reg, value: il.const(width, -1)
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
    lambda il, width, reg, value, src: il.store(width, il.const(2, value), src),

    # IMMEDIATE_MODE
    lambda il, width, reg, value, src: il.store(width, il.const(2, value), src),
]

def cond_branch(il, cond, dest):
    t = il.get_label_for_address(
        Architecture['msp430'],
        il[dest].value
    )

    if t is None:
        # t is not an address in the current function scope.
        t = LowLevelILLabel()
        indirect = True
    else:
        indirect = False

    f = LowLevelILLabel()

    il.append(il.if_expr(cond, t, f))

    if indirect:
        # If the destination is not in the current function,
        # then a jump, rather than a goto, needs to be added to
        # the IL.
        il.mark_label(t)
        il.append(il.jump(dest))

    il.mark_label(f)

def jump(il, dest):
    label = None

    if il[dest].operation == LLIL_CONST:
        label = il.get_label_for_address(
            Architecture['msp430'],
            il[dest].value
        )

    if label is None:
        return il.jump(dest)
    else:
        return il.goto(label)

def call(il, src_op, src, src_value):
    if src_op == INDIRECT_AUTOINCREMENT_MODE:
        # autoincrement mode is special in that prior to making the call,
        # the register needs to be incremented. This requires a temp register,
        # so that the original value of the register can be preserved while
        # the register is incremented prior to actually making the call.
        temp_expr = il.set_reg(
            2, LLIL_TEMP(0), il.reg(2, src)
        )

        call_expr = il.call(il.load(2, il.reg(2, LLIL_TEMP(0))))

        inc_expr = il.set_reg(
            2, src, il.add(
                2,
                il.reg(2, src),
                il.const(2, 2)
            )
        )

        il.append(temp_expr)
        il.append(inc_expr)

    else:
        call_expr = il.call(
            SourceOperandsIL[src_op](
                il, 2, src, src_value
            )
        )

    il.append(call_expr)

InstructionIL = {
    'add': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value: [
        DestOperandsIL[dst_op](
            il, width, dst, dst_value,
            il.add(
                width,
                SourceOperandsIL[dst_op](
                    il, width, dst, dst_value
                ),
                SourceOperandsIL[src_op](
                    il, width, src, src_value
                ),
                flags='*'
            )
        ),
        (
            il.set_reg(
                2, src,
                il.add(
                    width,
                    il.reg(2, src),
                    il.const(2, width)
                )
            ) if src_op == INDIRECT_AUTOINCREMENT_MODE
            else il.nop()
        )
    ],
    'addc': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value: [
        DestOperandsIL[dst_op](
            il, width, dst, dst_value,
            il.add(
                width,
                il.add(
                    width,
                    SourceOperandsIL[dst_op](
                        il, width, dst, dst_value
                    ),
                    SourceOperandsIL[src_op](
                        il, width, src, src_value
                    ),
                    flags='*'
                ),
                il.flag('c'),
                flags='*'
            )
        ),
        (
            il.set_reg(
                2, src,
                il.add(
                    width,
                    il.reg(2, src),
                    il.const(2, width)
                )
            ) if src_op == INDIRECT_AUTOINCREMENT_MODE
            else il.nop()
        )
    ],
    'and': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value: [
        DestOperandsIL[dst_op](
            il, width, dst, dst_value,
            il.and_expr(
                width,
                SourceOperandsIL[dst_op](
                    il, width, dst, dst_value
                ),
                SourceOperandsIL[src_op](
                    il, width, src, src_value
                ),
            )
        ),
        (
            il.set_reg(
                2, src,
                il.add(
                    width,
                    il.reg(2, src),
                    il.const(2, width)
                )
            ) if src_op == INDIRECT_AUTOINCREMENT_MODE
            else il.nop()
        )
    ],
    'bic': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value: [
        DestOperandsIL[dst_op](
            il, width, dst, dst_value,
            il.and_expr(
                width,
                SourceOperandsIL[dst_op](
                    il, width, dst, dst_value
                ),
                il.not_expr(
                    2,
                    SourceOperandsIL[src_op](
                        il, width, src, src_value
                    )
                ),
            )
        ),
        (
            il.set_reg(
                2, src,
                il.add(
                    width,
                    il.reg(2, src),
                    il.const(2, width)
                )
            ) if src_op == INDIRECT_AUTOINCREMENT_MODE
            else il.nop()
        )
    ],
    'bis': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value: [
        DestOperandsIL[dst_op](
            il, width, dst, dst_value,
            il.or_expr(
                width,
                SourceOperandsIL[dst_op](
                    il, width, dst, dst_value
                ),
                SourceOperandsIL[src_op](
                    il, width, src, src_value
                ),
            )
        ),
        (
            il.set_reg(
                2, src,
                il.add(
                    width,
                    il.reg(2, src),
                    il.const(2, width)
                )
            ) if src_op == INDIRECT_AUTOINCREMENT_MODE
            else il.nop()
        )
    ],
    'bit': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value: [
        DestOperandsIL[dst_op](
            il, width, dst, dst_value,
            il.and_expr(
                width,
                SourceOperandsIL[dst_op](
                    il, width, dst, dst_value
                ),
                SourceOperandsIL[src_op](
                    il, width, src, src_value
                ),
            )
        ),
        (
            il.set_reg(
                2, src,
                il.add(
                    width,
                    il.reg(2, src),
                    il.const(2, width)
                )
            ) if src_op == INDIRECT_AUTOINCREMENT_MODE
            else il.nop()
        )
    ],
    'br': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value: [
        jump(il, SourceOperandsIL[src_op](il, width, src, src_value)),
        (
            il.set_reg(
                2, src,
                il.add(
                    width,
                    il.reg(2, src),
                    il.const(2, width)
                )
            ) if src_op == INDIRECT_AUTOINCREMENT_MODE
            else il.nop()
        )
    ],
    'call': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value:
        call(il, src_op, src, src_value),
    'cmp': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value: [
        il.sub(
            width,
            SourceOperandsIL[dst_op](
                il, width, dst, dst_value
            ),
            SourceOperandsIL[src_op](
                il, width, src, src_value
            ),
            flags='*'
        ),
        (
            il.set_reg(
                2, src,
                il.add(
                    width,
                    il.reg(2, src),
                    il.const(2, width)
                )
            ) if src_op == INDIRECT_AUTOINCREMENT_MODE
            else il.nop()
        )
    ],
    'dadd': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value:
        il.unimplemented(),
    'jge': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value:
        cond_branch(il, il.flag_condition(LLFC_SGE), il.const(2, src_value)),
    'jhs': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value:
        cond_branch(il, il.flag_condition(LLFC_UGE), il.const(2, src_value)),
    'jl': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value:
        cond_branch(il, il.flag_condition(LLFC_SLT), il.const(2, src_value)),
    'jlo': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value:
        cond_branch(il, il.flag_condition(LLFC_ULT), il.const(2, src_value)),
    'jmp': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value:
        jump(il, il.const(2, src_value)),
    'jn': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value:
        cond_branch(
            il,
            il.compare_equal(0, il.flag('n'), il.const(0, 1)),
            il.const(2, src_value)
        ),
    'jnz': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value:
        cond_branch(il, il.flag_condition(LLFC_NE), il.const(2, src_value)),
    'jz': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value:
        cond_branch(il, il.flag_condition(LLFC_E), il.const(2, src_value)),
    'mov': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value: [
        DestOperandsIL[dst_op](
            il, width, dst, dst_value,
            SourceOperandsIL[src_op](
                il, width, src, src_value)
            ),
        (
            il.set_reg(
                2, src,
                il.add(
                    width,
                    il.reg(2, src),
                    il.const(2, width)
                )
            ) if src_op == INDIRECT_AUTOINCREMENT_MODE
            else il.nop()
        )
    ],
    'pop': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value: [
        DestOperandsIL[src_op](
            il, width, src, src_value, il.pop(2)
        ),
        (
            il.set_reg(
                2, src,
                il.add(
                    width,
                    il.reg(2, src),
                    il.const(2, width)
                )
            ) if src_op == INDIRECT_AUTOINCREMENT_MODE
            else il.nop()
        )
    ],
    'push': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value: [
        il.push(
            2,
            SourceOperandsIL[src_op](
                il, width, src, src_value
            )
        ),
        (
            il.set_reg(
                2, src,
                il.add(
                    width,
                    il.reg(2, src),
                    il.const(2, width)
                )
            ) if src_op == INDIRECT_AUTOINCREMENT_MODE
            else il.nop()
        )
    ],
    'ret': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value:
        il.ret(il.pop(2)),
    'reti': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value: [
        il.set_reg(2, 'sr', il.pop(2)),
        il.ret(il.pop(2))
    ],
    'rra': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value: [
        DestOperandsIL[src_op](
            il, width, src, src_value,
            il.arith_shift_right(
                2,
                SourceOperandsIL[src_op](
                    il, width, src, src_value
                ),
                il.const(1, 1),
                flags='cnz'
            ),
        ),
        il.set_flag('v', il.const(0, 0)),
        (
            il.set_reg(
                2, src,
                il.add(
                    width,
                    il.reg(2, src),
                    il.const(2, width)
                )
            ) if src_op == INDIRECT_AUTOINCREMENT_MODE
            else il.nop()
        )
    ],
    'rrc': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value: [
        DestOperandsIL[src_op](
            il, width, src, src_value,
            il.rotate_right_carry(
                2,
                SourceOperandsIL[src_op](
                    il, width, src, src_value
                ),
                il.const(1, 1),
                flags='*'
            ),
        ),
        (
            il.set_reg(
                2, src,
                il.add(
                    width,
                    il.reg(2, src),
                    il.const(2, width)
                )
            ) if src_op == INDIRECT_AUTOINCREMENT_MODE
            else il.nop()
        )
    ],
    'sub': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value: [
        DestOperandsIL[dst_op](
            il, width, dst, dst_value,
            il.sub(
                width,
                SourceOperandsIL[dst_op](
                    il, width, dst, dst_value
                ),
                SourceOperandsIL[src_op](
                    il, width, src, src_value
                ),
                flags='*'
            )
        ),
        (
            il.set_reg(
                2, src,
                il.add(
                    width,
                    il.reg(2, src),
                    il.const(2, width)
                )
            ) if src_op == INDIRECT_AUTOINCREMENT_MODE
            else il.nop()
        )
    ],
    'subc': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value: [
        DestOperandsIL[dst_op](
            il, width, dst, dst_value,
            il.sub(
                width,
                il.sub(
                    width,
                    SourceOperandsIL[dst_op](
                        il, width, dst, dst_value
                    ),
                    SourceOperandsIL[src_op](
                        il, width, src, src_value
                    ),
                    flags='*'
                ),
                il.not_expr(2, il.flag('c')),
                flags='*'
            )
        ),
        (
            il.set_reg(
                2, src,
                il.add(
                    width,
                    il.reg(2, src),
                    il.const(2, width)
                )
            ) if src_op == INDIRECT_AUTOINCREMENT_MODE
            else il.nop()
        )
    ],
    'swpb': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value: [
        DestOperandsIL[src_op](
            il, 2, src, src_value,
            il.rotate_left(
                2,
                SourceOperandsIL[src_op](
                    il, 2, src, src_value
                ),
                il.const(1, 8)
            )
        ),
        (
            il.set_reg(
                2, src,
                il.add(
                    width,
                    il.reg(2, src),
                    il.const(2, width)
                )
            ) if src_op == INDIRECT_AUTOINCREMENT_MODE
            else il.nop()
        )
    ],
    'sxt': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value: [
        DestOperandsIL[src_op](
            il, 2, src, src_value,
            il.sign_extend(
                2,
                SourceOperandsIL[src_op](
                    il, 1, src, src_value
                ),
                flags='*'
            ),
        ),
        (
            il.set_reg(
                2, src,
                il.add(
                    width,
                    il.reg(2, src),
                    il.const(2, width)
                )
            ) if src_op == INDIRECT_AUTOINCREMENT_MODE
            else il.nop()
        )
    ],
    'xor': lambda il, src_op, dst_op, src, dst, width, src_value, dst_value: [
        DestOperandsIL[dst_op](
            il, width, dst, dst_value,
            il.xor_expr(
                width,
                SourceOperandsIL[dst_op](
                    il, width, dst, dst_value
                ),
                SourceOperandsIL[src_op](
                    il, width, src, src_value
                ),
                flags='*'
            )
        ),
        (
            il.set_reg(
                2, src,
                il.add(
                    width,
                    il.reg(2, src),
                    il.const(2, width)
                )
            ) if src_op == INDIRECT_AUTOINCREMENT_MODE
            else il.nop()
        )
    ],
}

class MSP430(Architecture):
    name = 'msp430'
    address_size = 2
    default_int_size = 2

    regs = {
        'pc': RegisterInfo('pc', 2),
        'sp': RegisterInfo('sp', 2),
        'sr': RegisterInfo('sr', 2),
        'cg': RegisterInfo('cg', 2),
        'r4': RegisterInfo('r4', 2),
        'r5': RegisterInfo('r5', 2),
        'r6': RegisterInfo('r6', 2),
        'r7': RegisterInfo('r7', 2),
        'r8': RegisterInfo('r8', 2),
        'r9': RegisterInfo('r9', 2),
        'r10': RegisterInfo('r10', 2),
        'r11': RegisterInfo('r11', 2),
        'r12': RegisterInfo('r12', 2),
        'r13': RegisterInfo('r13', 2),
        'r14': RegisterInfo('r14', 2),
        'r15': RegisterInfo('r15', 2),
    }

    flags = ['v', 'n', 'c', 'z']

    # The first flag write type is ignored currently.
    # See: https://github.com/Vector35/binaryninja-api/issues/513
    flag_write_types = ['', '*', 'cnv', 'cnz']

    flags_written_by_flag_write_type = {
        '*': ['v', 'n', 'c', 'z'],
        'cnv': ['v', 'n', 'c'],
        'cnz': ['c', 'n', 'z']
    }
    flag_roles = {
        'c': CarryFlagRole,
        'n': NegativeSignFlagRole,
        'z': ZeroFlagRole,
        'v': OverflowFlagRole
    }

    flags_required_for_flag_condition = {
        LLFC_UGE: ['c'],
        LLFC_ULT: ['c'],
        LLFC_SGE: ['n', 'v'],
        LLFC_SLT: ['n', 'v'],
        LLFC_E: ['z'],
        LLFC_NE: ['z'],
        LLFC_NEG: ['n'],
        LLFC_POS: ['n']
    }

    stack_pointer = 'sp'

    def decode_instruction(self, data, addr):
        error_value = (None, None, None, None, None, None, None, None, None)
        if len(data) < 2:
            return error_value

        instruction = struct.unpack('<H', data[0:2])[0]

        # emulated instructions
        if instruction == 0x4130:
            return 'ret', None, None, None, None, None, 2, None, None

        opcode = (instruction&0xf000) >> 12

        mask = InstructionMask.get(opcode)
        shift = InstructionMaskShift.get(opcode)

        if mask and shift:
            instr = InstructionNames[opcode][(instruction&mask)>>shift]
        else:
            instr = InstructionNames[opcode]

        if instr is None:
            log_error('[{:x}] Bad opcode: {:x}'.format(addr, opcode))
            return error_value

        if instr not in TYPE3_INSTRUCTIONS:
            width = 1 if (instruction & 0x40) >> 6 else 2
        else:
            width = None

        src, src_operand, dst, dst_operand = GetOperands(instr, instruction)

        operand_length = 0
        if src_operand is not None:
            operand_length = OperandLengths[src_operand]
        if dst_operand is not None:
            operand_length += OperandLengths[dst_operand]

        length = 2 + operand_length

        if len(data) < length:
            return error_value

        src_value, dst_value = None, None

        if instr in TYPE3_INSTRUCTIONS:
            branch_target = (instruction & 0x3ff) << 1

            # check if it's a negative offset
            if branch_target & 0x700:
                branch_target = branch_target - (1 << 11)

            src_value = addr + 2 + branch_target

        elif operand_length == 2:
            value = struct.unpack('<H', data[2:4])[0]
            if OperandLengths[src_operand]:
                src_value = value
            else:
                dst_value = value

        elif operand_length == 4:
            src_value, dst_value = struct.unpack('<HH', data[2:6])

        if instr == 'mov' and dst == 'pc':
            instr = 'br'

        return instr, width, src_operand, dst_operand, src, dst, length, src_value, dst_value

    def perform_get_instruction_info(self, data, addr):
        instr, _, _, _, _, _, length, src_value, _ = self.decode_instruction(data, addr)

        if instr is None:
            return None

        result = InstructionInfo()
        result.length = length

        # Add branches
        if instr in ['ret', 'reti']:
            result.add_branch(FunctionReturn)
        elif instr in ['jmp', 'br'] and src_value is not None:
            result.add_branch(UnconditionalBranch, src_value)
        elif instr in TYPE3_INSTRUCTIONS:
            result.add_branch(TrueBranch, src_value)
            result.add_branch(FalseBranch, addr + 2)
        elif instr == 'call' and src_value is not None:
            result.add_branch(CallDestination, src_value)

        return result

    def perform_get_instruction_text(self, data, addr):
        (instr, width,
         src_operand, dst_operand,
         src, dst, length,
         src_value, dst_value) = self.decode_instruction(data, addr)

        if instr is None:
            return None

        tokens = []

        instruction_text = instr

        if width == 1:
            instruction_text += '.b'

        tokens = [
            InstructionTextToken(TextToken, '{:7s}'.format(instruction_text))
        ]

        if instr in TYPE1_INSTRUCTIONS:
            tokens += OperandTokens[src_operand](src, src_value)

            tokens += [InstructionTextToken(TextToken, ',')]

            tokens += OperandTokens[dst_operand](dst, dst_value)

        elif instr in TYPE2_INSTRUCTIONS:
            tokens += OperandTokens[src_operand](src, src_value)

        elif instr in TYPE3_INSTRUCTIONS:
            tokens += OperandTokens[src_operand](src, src_value)

        return tokens, length

    def perform_get_instruction_low_level_il(self, data, addr, il):
        (instr, width,
            src_operand, dst_operand,
            src, dst, length,
            src_value, dst_value) = self.decode_instruction(data, addr)

        if instr is None:
            return None

        if InstructionIL.get(instr) is None:
            log_error('[0x{:4x}]: {} not implemented'.format(addr, instr))
            il.append(il.unimplemented())
        else:
            il_instr = InstructionIL[instr](
                il, src_operand, dst_operand, src, dst, width, src_value, dst_value
            )
            if isinstance(il_instr, list):
                for i in il_instr:
                    il.append(i)
            elif il_instr is not None:
                il.append(il_instr)

        return length

MSP430.register()
