"""The navigation of a disc with a menu: which movie object does what.

    First Playback -> object 2 -> jump to the top menu
    Top Menu       -> object 1 -> play the menu playlist, then start it again
    Title 1        -> object 0 -> play the movie (same program tsMuxeR writes)
"""

from bdauthor.navigation.index import HdmvRef, IndexTable, PlaybackType, Title
from bdauthor.navigation.movie_object import MovieObject, break_, imm, jump_title, move, play_pl, play_pl_mk, reg

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
    # JUMP_TITLE(0) is the reserved "go to top menu" title number (BLURAY_TITLE_TOP_MENU in
    # libbluray), not a jump to a specific object: a real commercial disc's own Top Menu
    # object returns to itself exactly this way once its (equally long-looping) menu playlist
    # naturally ends, rather than a direct JUMP_OBJECT -- see docs/hdmv-ig-notes.md.
    #
    # Header flags on `show_menu`/`start` match the reference disc's own First Playback/Top Menu
    # objects exactly (its movie-title objects, like `play_movie` above, keep the class's own
    # defaults instead -- resume_intention=True, both masks False -- which already matched):
    # resume_intention=False (neither is a resumable "position", unlike a movie title), and
    # menu_call_mask=True on both (pressing the remote's Menu button does nothing extra while
    # already navigating HDMV menus) with title_search_mask additionally True only on `start`
    # (title search is blocked during First Playback, but allowed once at the Top Menu, where a
    # numeric remote button can jump straight to a title) -- see docs/hdmv-ig-notes.md.
    show_menu = MovieObject(
        (play_pl(imm(menu_playlist)), jump_title(imm(0))),
        resume_intention=False,
        menu_call_mask=True,
    )
    start = MovieObject(
        (jump_title(imm(0)),),  # title 0 is the top menu
        resume_intention=False,
        menu_call_mask=True,
        title_search_mask=True,
    )

    index = IndexTable(
        first_playback=HdmvRef(START_OBJECT),
        top_menu=HdmvRef(MENU_OBJECT),
        titles=(Title(HdmvRef(MOVIE_OBJECT, PlaybackType.MOVIE)),),
    )
    return index, [play_movie, show_menu, start]
