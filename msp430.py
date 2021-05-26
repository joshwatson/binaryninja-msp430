from binaryninja import (Architecture, FlagRole, LowLevelILFlagCondition,
                         RegisterInfo, log_error, InstructionInfo, BranchType, InstructionTextToken, InstructionTextTokenType)

from .instructions import Instruction, Registers, TYPE3_INSTRUCTIONS
from .lifter import Lifter

class MSP430(Architecture):
    name = 'msp430'
    address_size = 2
    default_int_size = 2
    global_regs = ['sr']
    stack_pointer = 'sp'

    regs = {r: RegisterInfo(r, 2) for r in Registers}

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
        'c': FlagRole.CarryFlagRole,
        'n': FlagRole.NegativeSignFlagRole,
        'z': FlagRole.ZeroFlagRole,
        'v': FlagRole.OverflowFlagRole
    }

    flags_required_for_flag_condition = {
        LowLevelILFlagCondition.LLFC_UGE: ['c'],
        LowLevelILFlagCondition.LLFC_UGT: ['c'],
        LowLevelILFlagCondition.LLFC_ULT: ['c'],
        LowLevelILFlagCondition.LLFC_ULE: ['c'],
        LowLevelILFlagCondition.LLFC_SGE: ['n', 'v'],
        LowLevelILFlagCondition.LLFC_SLT: ['n', 'v'],
        LowLevelILFlagCondition.LLFC_E: ['z'],
        LowLevelILFlagCondition.LLFC_NE: ['z'],
        LowLevelILFlagCondition.LLFC_NEG: ['n'],
        LowLevelILFlagCondition.LLFC_POS: ['n']
    }

    def perform_get_instruction_info(self, data, addr):
        instr = Instruction.decode(data, addr)

        if instr is None:
            return None

        result = InstructionInfo()
        result.length = instr.length

        # Add branches
        if instr.mnemonic in ['ret', 'reti']:
            result.add_branch(BranchType.FunctionReturn)
        elif instr.mnemonic in ['jmp', 'br'] and instr.src.value is not None:
            result.add_branch(BranchType.UnconditionalBranch, instr.src.value)
        elif instr.type == 3:
            result.add_branch(BranchType.TrueBranch, instr.src.value)
            result.add_branch(BranchType.FalseBranch, addr + 2)
        elif instr.mnemonic == 'call' and instr.src.value is not None:
            result.add_branch(BranchType.CallDestination, instr.src.value)

        return result

    def perform_get_instruction_text(self, data, addr):
        instr = Instruction.decode(data, addr)

        if instr is None:
            return None

        tokens = instr.generate_tokens()

        return tokens, instr.length

    def perform_get_instruction_low_level_il(self, data, addr, il):
        instr = Instruction.decode(data, addr)

        if instr is None:
            return None

        # Halting the system means turning off interrupts and just looping
        # indefinitely
        if instr.mnemonic == 'dint':
            next_instr = Instruction.decode(
                data[instr.length:], addr + instr.length
            )
            if next_instr.mnemonic == 'jmp' and next_instr.src.value == addr:
                instr.mnemonic = 'hlt'
    
        Lifter.lift(il, instr)

        return instr.length
