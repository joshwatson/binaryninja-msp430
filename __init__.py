import binaryninja.architecture
import binaryninja.binaryview
import binaryninja.enums

from .calling_convention import DefaultCallingConvention
from .msp430 import MSP430

MSP430.register()
arch = binaryninja.architecture.Architecture['msp430']
arch.register_calling_convention(DefaultCallingConvention(arch, 'default'))
standalone = arch.standalone_platform
standalone.default_calling_convention = arch.calling_conventions['default']
binaryninja.binaryview.BinaryViewType['ELF'].register_arch(
    105,
    binaryninja.enums.Endianness.LittleEndian,
    arch
)
