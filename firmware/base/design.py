"""Base reset image: a no-op Wishbone slave preloaded with "Garota de Ipanema".

This is the bitstream flashed on session reset / release. Its logic is
deliberately meaningless. The *only* purpose of a reset flash is to
reconfigure the FPGA fabric -- which wipes the previous user's design and any
data they left in block RAM -- so that the next user cannot read it. Nothing
ever talks to this design (the very next action is always a build_and_program
that flashes a fresh, correctly-addressed per-board bitstream), so what it
computes is irrelevant. That makes its BRAM the perfect place to hide an
easter egg: the lyrics of Tom Jobim & Vinicius de Moraes' bossa-nova classic
are baked into the 512x32-bit user region at build time. A JTAG readback of an
idle board would reveal the girl from Ipanema, and nothing else.

It is the same Wishbone B4 slave contract as examples/hello_wishbone's
EchoSlave (hardware-verified), so the standard firmware build pipeline
consumes it unchanged -- the only difference is the memory starts initialized
with the lyrics instead of empty. Build it once, with a sentinel FPGA_ID whose
MAC/IP falls outside the usable per-board range, and the resulting base.bit is
reused as the reset image for every board.

Requires Amaranth >= 0.5.
"""

from amaranth.hdl import Elaboratable, Module, Signal
from amaranth.lib.memory import Memory

# 512 words x 4 bytes = 2048 bytes: exactly the user design region.
DEPTH = 512

# Garota de Ipanema (The Girl from Ipanema) -- Jobim / de Moraes / Gimbel.
# Baked into BRAM purely as an easter egg; see the module docstring.
LYRICS = """Olha que coisa mais linda, mais cheia de graça
É ela, menina, que vem e que passa
Num doce balanço a caminho do mar
Moça do corpo dourado, do sol de Ipanema
O seu balançado é mais que um poema
É a coisa mais linda que eu já vi passar
Ah, por que estou tão sozinho?
Ah, por que tudo é tão triste?
Ah, a beleza que existe
A beleza que não é só minha
Que também passa sozinha
Ah, se ela soubesse que quando ela passa
O mundo sorrindo se enche de graça
E fica mais lindo por causa do amor
Tall and tan and young and lovely
The girl from Ipanema goes walking
And when she passes
Each one she passes goes "ah!"
When she walks she's like a samba that
Swings so cool and sways so gently
That when she passes
Each one she passes goes "ah!"
Oh, but he watches her so sadly
How can he tell her he loves her?
Yes, he would give his heart gladly
But each day when she walks to the sea
She looks straight ahead not at him
Tall and tan and young and lovely
The girl from Ipanema goes walking
And when she passes he smiles
But she doesn't see
Tall and tan and young and lovely
The girl from Ipanema goes walking
And when she passes
Each one she passes goes "ah!"
When she walks she's like a samba that
Swings so cool and sways so gently
That when she passes
Each one she passes goes "ah!"
Oh, but he watches her so sadly
How can he tell her he loves her?
Yes, he would give his heart gladly
But each day when she walks to the sea
She looks straight ahead not at him
Tall and tan and young and lovely
The girl from Ipanema goes walking
And when she passes he smiles
But she doesn't see
She just doesn't see
No, she doesn't see
She just doesn't see
"""


def pack_lyrics(text=LYRICS, depth=DEPTH):
    """Pack UTF-8 ``text`` into ``depth`` little-endian 32-bit words, zero-padded.

    Little-endian so a RISC-V (little-endian) word read reconstructs the bytes
    in order. Raises if the text does not fit the user region, so the build
    fails loudly rather than silently truncating the easter egg.
    """
    data = text.encode("utf-8")
    capacity = depth * 4
    if len(data) > capacity:
        raise ValueError(
            f"lyrics are {len(data)} bytes; user region holds {capacity}"
        )
    data = data.ljust(capacity, b"\x00")
    return [int.from_bytes(data[i : i + 4], "little") for i in range(0, capacity, 4)]


# Computed at import so an oversized lyric sheet is an immediate ImportError
# (resolve_top would otherwise silently skip a design that raises in __init__).
_INIT = pack_lyrics()


class IpanemaSlave(Elaboratable):
    """Wishbone target (slave); same contract/timing as EchoSlave.

    Identical to examples/hello_wishbone's EchoSlave except the backing BRAM is
    preloaded with the lyrics (see ``_INIT``). Reads return the lyric words;
    writes still work but no client ever issues them.

    sel is accepted but ignored; all accesses are full 32-bit words.
    """

    def __init__(self, depth=DEPTH):
        self.depth = depth

        # (depth - 1).bit_length() = 9 for depth=512.
        addr_bits = (depth - 1).bit_length()

        # Wishbone inputs (driven by the bus master / firmware).
        self.wb_cyc = Signal()
        self.wb_stb = Signal()
        self.wb_we = Signal()
        self.wb_adr = Signal(addr_bits)
        self.wb_dat_w = Signal(32)
        self.wb_sel = Signal(4)

        # Wishbone outputs (driven by this slave).
        self.wb_dat_r = Signal(32)
        self.wb_ack = Signal()

    def elaborate(self, platform):
        m = Module()

        # BRAM preloaded with the lyrics; ECP5 infers block RAM from the init.
        m.submodules.mem = mem = Memory(shape=32, depth=self.depth, init=_INIT)
        rd = mem.read_port(domain="sync", transparent_for=[])
        wr = mem.write_port(domain="sync")

        m.d.comb += [
            rd.addr.eq(self.wb_adr),
            wr.addr.eq(self.wb_adr),
            wr.data.eq(self.wb_dat_w),
            self.wb_dat_r.eq(rd.data),
        ]

        # Write enable fires the cycle stb arrives; the write completes
        # on the next clock edge, the same edge ack fires.
        m.d.comb += wr.en.eq(
            self.wb_cyc & self.wb_stb & self.wb_we & ~self.wb_ack
        )

        # Ack: assert one cycle after cyc+stb, then clear.
        with m.If(self.wb_cyc & self.wb_stb & ~self.wb_ack):
            m.d.sync += self.wb_ack.eq(1)
        with m.Else():
            m.d.sync += self.wb_ack.eq(0)

        return m
