from binaryninja import CallingConvention

class DefaultCallingConvention(CallingConvention):
    name = "Default"
    int_arg_regs = ['r15', 'r14', 'r13', 'r12']
    int_return_reg = 'r15'
    high_int_return_reg = 'r14'

class StackBasedCallingConvention(CallingConvention):
    name = "StackBased"
    int_arg_regs = []
    int_return_reg = 'r15'
    high_int_return_reg = 'r14'