"""index.bdmv: the disc's title table (First Playback, Top Menu and the numbered titles).

Only HDMV objects are supported (BD-J is out of scope). Layout, as read by libbluray's
index_parse.c: a 40-byte header, a 34-byte application info block, then the index with the
First Playback and Top Menu entries followed by the numbered titles (12 bytes each).
"""

import struct
from dataclasses import dataclass
from enum import IntEnum

_SIGNATURE = b"INDX0200"
_APP_INFO_START = 40
_APP_INFO_LENGTH = 34
_USER_DATA_LENGTH = 32
_OBJECT_TYPE_HDMV = 1
_MAX_TITLES = 0xFFFF


class PlaybackType(IntEnum):
    MOVIE = 0  # the object only plays a playlist
    INTERACTIVE = 1  # the object shows a menu


class Access(IntEnum):
    PERMITTED = 0
    PROHIBITED = 1
    HIDDEN = 3  # not allowed and not shown in the player's UI


@dataclass(frozen=True)
class HdmvRef:
    """A reference to a movie object (its position in MovieObject.bdmv)."""

    movie_object: int
    playback_type: PlaybackType = PlaybackType.INTERACTIVE

    def pack(self) -> bytes:
        # playback_type(2) + 14 reserved bits, object id(16), 32 reserved bits
        return struct.pack(">HHI", self.playback_type << 14, self.movie_object, 0)


@dataclass(frozen=True)
class Title:
    ref: HdmvRef
    access: Access = Access.PERMITTED

    def pack(self) -> bytes:
        return struct.pack(">I", _OBJECT_TYPE_HDMV << 30 | self.access << 28) + self.ref.pack()


@dataclass(frozen=True)
class IndexTable:
    first_playback: HdmvRef
    top_menu: HdmvRef
    titles: tuple[Title, ...] = ()
    video_format: int = 0  # 0 = unspecified, as tsMuxeR writes it
    frame_rate: int = 0

    def pack(self) -> bytes:
        if len(self.titles) > _MAX_TITLES:
            raise ValueError(f"too many titles: {len(self.titles)}")
        if not (0 <= self.video_format <= 15 and 0 <= self.frame_rate <= 15):
            raise ValueError("video_format and frame_rate are 4-bit values")

        app_info = struct.pack(">BB", 0, self.video_format << 4 | self.frame_rate)
        app_info += bytes(_USER_DATA_LENGTH)
        assert len(app_info) == _APP_INFO_LENGTH

        first_play = struct.pack(">I", _OBJECT_TYPE_HDMV << 30) + self.first_playback.pack()
        top_menu = struct.pack(">I", _OBJECT_TYPE_HDMV << 30) + self.top_menu.pack()
        titles = b"".join(title.pack() for title in self.titles)
        index = first_play + top_menu + struct.pack(">H", len(self.titles)) + titles

        indexes_start = _APP_INFO_START + 4 + _APP_INFO_LENGTH
        header = _SIGNATURE + struct.pack(">II", indexes_start, 0)  # no extension data
        header = header.ljust(_APP_INFO_START, b"\0")
        return (
            header
            + struct.pack(">I", _APP_INFO_LENGTH)
            + app_info
            + struct.pack(">I", len(index))
            + index
        )
