"""MovieObject.bdmv: the HDMV navigation-command programs.

A movie object is a list of 12-byte commands: a 32-bit instruction word followed by two 32-bit
operands. Bit layout of the instruction word (most significant bit first):

    op_cnt(3) group(2) sub_group(3)  imm_op1(1) imm_op2(1) reserved(2) branch_opt(4)
    reserved(4) cmp_opt(4)           reserved(3) set_opt(5)

`op_cnt` is the number of operands used. An operand is a register number (r0..r4095) unless its
`imm` bit is set, in which case it is a constant. Encodings follow libbluray's decoder
(hdmv_insn.h, mobj_parse.c) and are checked against tsMuxeR's own files and `mobj_dump`.
"""

import struct
from dataclasses import dataclass
from enum import IntEnum

_SIGNATURE = b"MOBJ0200"
_DATA_START = 40  # the length field sits after the header, padded with zeros
_MAX_REGISTER = 4095
_MAX_OBJECTS = 0xFFFF
_MAX_COMMANDS = 0xFFFF


class Group(IntEnum):
    BRANCH = 0
    COMPARE = 1
    SET = 2


class Branch(IntEnum):
    GOTO = 0
    JUMP = 1
    PLAY = 2


class Goto(IntEnum):
    NOP = 0
    GOTO = 1
    BREAK = 2


class Jump(IntEnum):
    JUMP_OBJECT = 0
    JUMP_TITLE = 1
    CALL_OBJECT = 2
    CALL_TITLE = 3
    RESUME = 4


class Play(IntEnum):
    PLAY_PL = 0
    PLAY_PL_PI = 1
    PLAY_PL_MK = 2
    TERMINATE_PL = 3


class Compare(IntEnum):
    BC = 1
    EQ = 2
    NE = 3
    GE = 4
    GT = 5
    LE = 6
    LT = 7


class SetOp(IntEnum):
    MOVE = 1


class SetSubGroup(IntEnum):
    SET = 0
    SETSYSTEM = 1


class SetSystem(IntEnum):
    SET_BUTTON_PAGE = 3


@dataclass(frozen=True)
class Operand:
    value: int
    immediate: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.value <= 0xFFFFFFFF:
            raise ValueError(f"operand out of range: {self.value}")
        if not self.immediate and self.value > _MAX_REGISTER:
            raise ValueError(f"register out of range: r{self.value}")


def reg(number: int) -> Operand:
    """A general purpose register operand (r0..r4095)."""
    return Operand(number, immediate=False)


def imm(value: int) -> Operand:
    """A constant operand."""
    return Operand(value, immediate=True)


@dataclass(frozen=True)
class Instruction:
    """One command. `option` is branch_opt, cmp_opt or set_opt depending on `group`."""

    group: Group
    sub_group: int
    option: int
    dst: Operand | None = None
    src: Operand | None = None

    def __post_init__(self) -> None:
        if self.src is not None and self.dst is None:
            raise ValueError("an instruction with a source operand needs a destination operand")

    @property
    def op_cnt(self) -> int:
        return (self.dst is not None) + (self.src is not None)

    def pack(self) -> bytes:
        word = self.op_cnt << 29 | self.group << 27 | self.sub_group << 24
        word |= (self.dst.immediate if self.dst else 0) << 23
        word |= (self.src.immediate if self.src else 0) << 22
        if self.group == Group.BRANCH:
            word |= self.option << 16
        elif self.group == Group.COMPARE:
            word |= self.option << 8
        else:
            word |= self.option
        return struct.pack(
            ">III", word, self.dst.value if self.dst else 0, self.src.value if self.src else 0
        )


def _branch(sub_group: Branch, option: int, *operands: Operand) -> Instruction:
    dst = operands[0] if operands else None
    src = operands[1] if len(operands) > 1 else None
    return Instruction(Group.BRANCH, sub_group, option, dst, src)


def nop() -> Instruction:
    return _branch(Branch.GOTO, Goto.NOP)


def goto(target: Operand) -> Instruction:
    """Continue at command number `target` of the same object."""
    return _branch(Branch.GOTO, Goto.GOTO, target)


def break_() -> Instruction:
    return _branch(Branch.GOTO, Goto.BREAK)


def jump_object(target: Operand) -> Instruction:
    return _branch(Branch.JUMP, Jump.JUMP_OBJECT, target)


def jump_title(target: Operand) -> Instruction:
    """Title 0 is the top menu."""
    return _branch(Branch.JUMP, Jump.JUMP_TITLE, target)


def call_object(target: Operand) -> Instruction:
    return _branch(Branch.JUMP, Jump.CALL_OBJECT, target)


def call_title(target: Operand) -> Instruction:
    return _branch(Branch.JUMP, Jump.CALL_TITLE, target)


def resume() -> Instruction:
    return _branch(Branch.JUMP, Jump.RESUME)


def play_pl(playlist: Operand) -> Instruction:
    return _branch(Branch.PLAY, Play.PLAY_PL, playlist)


def play_pl_pi(playlist: Operand, play_item: Operand) -> Instruction:
    return _branch(Branch.PLAY, Play.PLAY_PL_PI, playlist, play_item)


def play_pl_mk(playlist: Operand, mark: Operand) -> Instruction:
    return _branch(Branch.PLAY, Play.PLAY_PL_MK, playlist, mark)


def terminate_pl() -> Instruction:
    return _branch(Branch.PLAY, Play.TERMINATE_PL)


def compare(op: Compare, left: Operand, right: Operand) -> Instruction:
    """If the comparison is false the player skips the next command."""
    return Instruction(Group.COMPARE, 0, op, left, right)


def move(dst: Operand, src: Operand) -> Instruction:
    """Copy `src` (a register or a constant) into the register `dst`."""
    if dst.immediate:
        raise ValueError("the destination of move must be a register")
    return Instruction(Group.SET, SetSubGroup.SET, SetOp.MOVE, dst, src)


def set_button_page(page_id: int, button_id: int) -> Instruction:
    """As an IG button's command: switch to `page_id` and select `button_id` on it, with no effect.

    Only meaningful inside a button's own navigation commands (an interactive composition), not in
    a plain MovieObject.bdmv program, where the player interprets it differently (10.4.3.4 (D)).
    """
    if not 0 <= page_id <= 0xFE:
        raise ValueError(f"page id out of range: {page_id}")
    if not 0 <= button_id <= 0xFFFF:
        raise ValueError(f"button id out of range: {button_id}")
    dst = imm(0x80000000 | button_id)  # bit 31: button flag
    src = imm(0x80000000 | page_id)  # bit 31: page flag; bit 30 (unset): play the page's effects
    return Instruction(Group.SET, SetSubGroup.SETSYSTEM, SetSystem.SET_BUTTON_PAGE, dst, src)


@dataclass(frozen=True)
class MovieObject:
    commands: tuple[Instruction, ...]
    resume_intention: bool = True
    menu_call_mask: bool = False
    title_search_mask: bool = False

    def pack(self) -> bytes:
        if len(self.commands) > _MAX_COMMANDS:
            raise ValueError(f"too many commands in one object: {len(self.commands)}")
        flags = self.resume_intention << 15 | self.menu_call_mask << 14 | self.title_search_mask << 13
        body = b"".join(command.pack() for command in self.commands)
        return struct.pack(">HH", flags, len(self.commands)) + body


def pack_movie_objects(objects: list[MovieObject] | tuple[MovieObject, ...]) -> bytes:
    """The complete MovieObject.bdmv file."""
    if len(objects) > _MAX_OBJECTS:
        raise ValueError(f"too many movie objects: {len(objects)}")
    body = b"".join(obj.pack() for obj in objects)
    header = _SIGNATURE + struct.pack(">I", 0)  # no extension data
    header = header.ljust(_DATA_START, b"\0")
    # the length counts everything after the length field: 4 reserved + 2 count + objects
    return header + struct.pack(">IIH", 4 + 2 + len(body), 0, len(objects)) + body
