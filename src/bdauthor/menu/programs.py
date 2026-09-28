"""The navigation of a disc with a menu: which movie object does what.

    First Playback -> object 2 -> jump to the top menu
    Top Menu       -> object 1 -> play the menu playlist, then start it again
    Title 1        -> object 0 -> play the movie (same program tsMuxeR writes)
"""

from bdauthor.navigation.index import HdmvRef, IndexTable, PlaybackType, Title
from bdauthor.navigation.movie_object import (
    MovieObject,
    break_,
    imm,
    jump_object,
    jump_title,
    move,
    play_pl,
    play_pl_mk,
    reg,
)

MOVIE_OBJECT = 0
MENU_OBJECT = 1
START_OBJECT = 2


def menu_navigation(*, movie_playlist: int = 0, menu_playlist: int = 1) -> tuple[IndexTable, list[MovieObject]]:
    """Index table and movie objects for a disc whose top menu plays `menu_playlist` in a loop."""
    play_movie = MovieObject(
        (
            move(reg(10), reg(0)),
            move(reg(0), imm(0)),
            move(reg(0), imm(0)),
            play_pl_mk(imm(movie_playlist), reg(10)),
            break_(),
        )
    )
    show_menu = MovieObject((play_pl(imm(menu_playlist)), jump_object(imm(MENU_OBJECT))))
    start = MovieObject((jump_title(imm(0)),))  # title 0 is the top menu

    index = IndexTable(
        first_playback=HdmvRef(START_OBJECT),
        top_menu=HdmvRef(MENU_OBJECT),
        titles=(Title(HdmvRef(MOVIE_OBJECT, PlaybackType.MOVIE)),),
    )
    return index, [play_movie, show_menu, start]
