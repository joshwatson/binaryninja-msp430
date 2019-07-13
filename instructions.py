from binaryninja import InstructionTextToken, InstructionTextTokenType
import struct

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

    width = 1 if (instruction & 0x40) >> 6 else 2

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

    return src, As, dst, Ad, width


OperandTokens = [
    lambda reg, value: [    # REGISTER_MODE
        InstructionTextToken(InstructionTextTokenType.RegisterToken, reg)
    ],
    lambda reg, value: [    # INDEXED_MODE
        InstructionTextToken(
            InstructionTextTokenType.IntegerToken, hex(value), value),
        InstructionTextToken(InstructionTextTokenType.TextToken, '('),
        InstructionTextToken(InstructionTextTokenType.RegisterToken, reg),
        InstructionTextToken(InstructionTextTokenType.TextToken, ')')
    ],
    lambda reg, value: [    # INDIRECT_REGISTER_MODE
        InstructionTextToken(InstructionTextTokenType.TextToken, '@'),
        InstructionTextToken(InstructionTextTokenType.RegisterToken, reg)
    ],
    lambda reg, value: [    # INDIRECT_AUTOINCREMENT_MODE
        InstructionTextToken(InstructionTextTokenType.TextToken, '@'),
        InstructionTextToken(InstructionTextTokenType.RegisterToken, reg),
        InstructionTextToken(InstructionTextTokenType.TextToken, '+')
    ],
    lambda reg, value: [    # SYMBOLIC_MODE
        InstructionTextToken(
            InstructionTextTokenType.CodeRelativeAddressToken, hex(value), value)
    ],
    lambda reg, value: [    # ABSOLUTE_MODE
        InstructionTextToken(InstructionTextTokenType.TextToken, '&'),
        InstructionTextToken(
            InstructionTextTokenType.PossibleAddressToken, hex(value), value)
    ],
    lambda reg, value: [    # IMMEDIATE_MODE
        InstructionTextToken(
            InstructionTextTokenType.PossibleAddressToken, hex(value), value)
    ],
    lambda reg, value: [    # CONSTANT_MODE0
        InstructionTextToken(InstructionTextTokenType.IntegerToken, str(0), 0)
    ],
    lambda reg, value: [    # CONSTANT_MODE1
        InstructionTextToken(InstructionTextTokenType.IntegerToken, str(1), 1)
    ],
    lambda reg, value: [    # CONSTANT_MODE2
        InstructionTextToken(InstructionTextTokenType.IntegerToken, str(2), 2)
    ],
    lambda reg, value: [    # CONSTANT_MODE4
        InstructionTextToken(InstructionTextTokenType.IntegerToken, str(4), 4)
    ],
    lambda reg, value: [    # CONSTANT_MODE8
        InstructionTextToken(InstructionTextTokenType.IntegerToken, str(8), 8)
    ],
    lambda reg, value: [    # CONSTANT_MODE_NEG1
        InstructionTextToken(
            InstructionTextTokenType.IntegerToken, str(-1), -1)
    ],
    lambda reg, value: [    # OFFSET
        InstructionTextToken(
            InstructionTextTokenType.PossibleAddressToken, hex(value), value)
    ]
]


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

class Operand:
    def __init__(
        self,
        mode,
        target=None,
        width=None,
        value=None,
        operand_length=0
    ):
        self._mode = mode
        self._width = width
        self._target = target
        self._value = value
        self._length = operand_length

    @property
    def mode(self):
        return self._mode
    
    @property
    def width(self):
        return self._width

    @property
    def target(self):
        return self._target

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, v):
        self._value = v

    @property
    def operand_length(self):
        return self._length

class SourceOperand(Operand):
    @classmethod
    def decode(cls, instr_type, instruction, address):
        if instr_type == 3:
            mode = OFFSET
            target = None
            width = None
        else:
            width = 1 if (instruction & 0x40) >> 6 else 2

            # As is in the same place for Type 1 and 2 instructions
            mode = (instruction & 0x30) >> 4

        if instr_type == 2:
            target = Registers[instruction & 0xf]
        elif instr_type == 1:
            target = Registers[(instruction & 0xf00) >> 8]
        
        if target == 'pc':
            if mode == INDEXED_MODE:
                mode = SYMBOLIC_MODE
            elif mode == INDIRECT_AUTOINCREMENT_MODE:
                mode = IMMEDIATE_MODE
        elif target == 'cg':
            if mode == REGISTER_MODE:
                mode = CONSTANT_MODE0
            elif mode == INDEXED_MODE:
                mode = CONSTANT_MODE1
            elif mode == INDIRECT_REGISTER_MODE:
                mode = CONSTANT_MODE2
            else:
                mode = CONSTANT_MODE_NEG1
        elif target == 'sr':
            if mode == INDEXED_MODE:
                mode = ABSOLUTE_MODE
            elif mode == INDIRECT_REGISTER_MODE:
                mode = CONSTANT_MODE4
            elif mode == INDIRECT_AUTOINCREMENT_MODE:
                mode = CONSTANT_MODE8

        operand_length = OperandLengths[mode]

        if instr_type == 3:
            branch_target = (instruction & 0x3ff) << 1

            # check if it's a negative offset
            if branch_target & 0x600:
                branch_target |= 0xf800
                branch_target -= 0x10000

            value = address + 2 + branch_target

            return cls(mode, target, width, value, operand_length)
        else:
            return cls(mode, target, width, operand_length=operand_length)

class DestOperand(Operand):
    @classmethod
    def decode(cls, instr_type, instruction, address):
        if instr_type != 1:
            return None

        width = 1 if (instruction & 0x40) >> 6 else 2
        target = Registers[instruction & 0xf]
        mode = (instruction & 0x80) >> 7

        if target == 'sr' and mode == INDEXED_MODE:
                mode = ABSOLUTE_MODE

        operand_length = OperandLengths[mode]

        return cls(mode, target, width, operand_length=operand_length)

class Instruction:
    @classmethod
    def decode(cls, data, address):
        if len(data) < 2:
            return None

        emulated = False

        instruction = struct.unpack('<H', data[0:2])[0]

        # emulated instructions
        if instruction == 0x4130:
            return cls('ret', emulated=True)

        opcode = (instruction & 0xf000) >> 12

        mask = InstructionMask.get(opcode)
        shift = InstructionMaskShift.get(opcode)

        if None not in (mask, shift):
            mnemonic = InstructionNames[opcode][(instruction & mask) >> shift]
        else:
            mnemonic = InstructionNames[opcode]

        if mnemonic is None:
            return None

        if mnemonic in TYPE1_INSTRUCTIONS:
            type_ = 1
        elif mnemonic in TYPE2_INSTRUCTIONS:
            type_ = 2
        elif mnemonic in TYPE3_INSTRUCTIONS:
            type_ = 3

        src = SourceOperand.decode(type_, instruction, address)

        dst = DestOperand.decode(type_, instruction, address)

        length = 2 + src.operand_length + (dst.operand_length if dst else 0)

        if len(data) < length:
            return None

        offset = 2
        if src.operand_length:
            src.value = struct.unpack('<H', data[offset:offset+2])[0]
            offset += 2
        if dst and dst.operand_length:
            dst.value = struct.unpack('<H', data[offset:offset+2])[0]

        # emulated instructions
        if mnemonic == 'mov' and dst.target == 'pc':
            mnemonic = 'br'
            emulated = True

        return cls(
            mnemonic,
            type_,
            src,
            dst,
            length,
            emulated
        )

    def generate_tokens(self):
        tokens = []

        mnemonic = self.mnemonic
        type_ = self.type
        src = self.src
        dst = self.dst

        if src is not None and src.width == 1:
            mnemonic += '.b'

        tokens = [
            InstructionTextToken(
                InstructionTextTokenType.TextToken, '{:7s}'.format(mnemonic))
        ]

        if type_ == 1:
            tokens += OperandTokens[src.mode](src.target, src.value)

            tokens += [InstructionTextToken(
                InstructionTextTokenType.TextToken, ',')]

            tokens += OperandTokens[dst.mode](dst.target, dst.value)

        elif type_ == 2:
            tokens += OperandTokens[src.mode](src.target, src.value)

        elif type_ == 3:
            tokens += OperandTokens[src.mode](src.target, src.value)

        return tokens

    def __init__(
        self,
        mnemonic,
        type_=None,
        src=None,
        dst=None,
        length=2,
        emulated=False
    ):
        self.mnemonic = mnemonic
        self.src = src
        self.dst = dst
        self.length = length
        self.emulated = emulated
        self.type = type_
