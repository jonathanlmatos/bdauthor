/*
 * bd_menu_test: play a BDMV directory with libbluray and print what a player would see.
 *
 * Unlike libbluray's hdmv_test (which only reads the last few KB of each playlist), this reads
 * the playlists from the start, so the interactive graphics (the menu) are decoded and drawn.
 * Keys can be pressed once the menu is on screen, to check that a button does what it should.
 *
 *   bd_menu_test [-v] [-b MAX_BYTES] [-k KEY[,KEY...]] DISC_DIRECTORY
 *
 * KEY is one of: enter, up, down, left, right, popup, root.
 * -v shows libbluray's own log messages (silenced otherwise).
 * Output, one item per line: "OVERLAY ...", "EVENT NAME value", "KEY name",
 * "STATUS page=N button=N" (read directly from the player's registers, right after the key: no
 * guessing how long a navigation command takes to settle), "DONE ...".
 */

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "bluray.h"
#include "bluray_internal.h"  /* bdpriv_reg_read: the only way to read PSR10/PSR11 from a BLURAY* */
#include "decoders/overlay.h"
#include "keys.h"
#include "register.h"
#include "util/log_control.h"

#define IDLE_LIMIT 400  /* consecutive empty reads before giving up */
#define MAX_END_OF_TITLE 12 /* the menu playlist loops for ever: stop after a few rounds */
#define SETTLE_READS 60 /* cap while waiting for a key's redraw (a FLUSH) after sending it */

static int overlays_drawn;
static int end_of_titles;
static int flush_seen; /* set by overlay_cb; the signal that a key's navigation command finished */

static void silent_log(const char *message)
{
    (void)message;
}

static const char *plane_name(int plane)
{
    return plane == BD_OVERLAY_IG ? "IG" : plane == BD_OVERLAY_PG ? "PG" : "BG";
}

static const char *command_name(int cmd)
{
    static const char *const names[] = {"INIT", "CLOSE", "CLEAR", "DRAW", "WIPE", "HIDE", "FLUSH"};
    return cmd >= 0 && cmd < 7 ? names[cmd] : "?";
}

static void overlay_cb(void *handle, const struct bd_overlay_s *const ov)
{
    (void)handle;
    if (!ov) {
        printf("OVERLAY CLOSE\n");
        return;
    }
    if (ov->cmd == BD_OVERLAY_DRAW && ov->plane == BD_OVERLAY_IG) {
        overlays_drawn++;
    }
    if (ov->cmd == BD_OVERLAY_FLUSH && ov->plane == BD_OVERLAY_IG) {
        flush_seen = 1;
    }
    printf("OVERLAY plane=%s cmd=%s pts=%" PRId64 " x=%d y=%d w=%d h=%d\n", plane_name(ov->plane),
           command_name(ov->cmd), ov->pts, ov->x, ov->y, ov->w, ov->h);
}

static void print_event(const BD_EVENT *ev)
{
#define EV(name)                                              \
    case BD_EVENT_##name:                                     \
        printf("EVENT " #name " %u\n", ev->param);            \
        break
    switch ((bd_event_e)ev->event) {
    case BD_EVENT_NONE:
        break;
        EV(ERROR);
        EV(READ_ERROR);
        EV(TITLE);
        EV(PLAYLIST);
        EV(PLAYITEM);
        EV(PLAYMARK);
        EV(CHAPTER);
        EV(END_OF_TITLE);
        EV(MENU);
        EV(POPUP);
        EV(IG_STREAM);
        EV(STILL);
        EV(SEEK);
    default:
        break; /* the rest is noise for our purposes */
    }
    fflush(stdout);
#undef EV
}

static int key_code(const char *name)
{
    static const struct {
        const char *name;
        int code;
    } keys[] = {{"enter", BD_VK_ENTER}, {"up", BD_VK_UP},       {"down", BD_VK_DOWN},
                {"left", BD_VK_LEFT},   {"right", BD_VK_RIGHT}, {"popup", BD_VK_POPUP},
                {"root", BD_VK_ROOT_MENU}};
    for (size_t i = 0; i < sizeof keys / sizeof keys[0]; i++) {
        if (!strcmp(name, keys[i].name)) {
            return keys[i].code;
        }
    }
    return -1;
}

int main(int argc, char *argv[])
{
    long max_bytes = 16L << 20;
    char *keys = NULL;
    int verbose = 0;
    const char *disc = NULL;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-b") && i + 1 < argc) {
            max_bytes = atol(argv[++i]);
        } else if (!strcmp(argv[i], "-v")) {
            verbose = 1;
        } else if (!strcmp(argv[i], "-k") && i + 1 < argc) {
            keys = argv[++i];
        } else {
            disc = argv[i];
        }
    }
    if (!disc) {
        fprintf(stderr, "usage: %s [-v] [-b MAX_BYTES] [-k KEY[,KEY...]] DISC_DIRECTORY\n", argv[0]);
        return 2;
    }

    if (!verbose) {
        bd_set_debug_handler(silent_log);
    }
    BLURAY *bd = bd_open(disc, NULL);
    if (!bd) {
        printf("DONE error=cannot-open\n");
        return 1;
    }
    bd_set_player_setting(bd, BLURAY_PLAYER_SETTING_PARENTAL, 99);
    bd_set_player_setting_str(bd, BLURAY_PLAYER_SETTING_AUDIO_LANG, "eng");
    bd_set_player_setting_str(bd, BLURAY_PLAYER_SETTING_PG_LANG, "eng");
    bd_set_player_setting_str(bd, BLURAY_PLAYER_SETTING_MENU_LANG, "eng");
    bd_register_overlay_proc(bd, NULL, overlay_cb);

    if (!bd_play(bd)) {
        printf("DONE error=cannot-play\n");
        bd_close(bd);
        return 1;
    }

    char *next_key = keys ? strtok(keys, ",") : NULL;
    long total = 0;
    int idle = 0;
    uint8_t buf[6144];
    BD_EVENT ev;
    int stop = 0;

    /* One read, with the usual bookkeeping. Returns 0 to keep going, 1 to stop. */
#define READ_STEP()                                                                     \
    do {                                                                                \
        int n = bd_read_ext(bd, buf, sizeof buf, &ev);                                  \
        if (n < 0) {                                                                    \
            stop = 1;                                                                   \
            break;                                                                      \
        }                                                                               \
        total += n;                                                                     \
        print_event(&ev);                                                              \
        if (ev.event == BD_EVENT_END_OF_TITLE && ++end_of_titles >= MAX_END_OF_TITLE) { \
            stop = 1;                                                                   \
            break;                                                                      \
        }                                                                               \
        idle = (n == 0 && ev.event == BD_EVENT_NONE) ? idle + 1 : 0;                    \
        if (idle > IDLE_LIMIT || total >= max_bytes) {                                  \
            stop = 1;                                                                   \
        }                                                                               \
    } while (0)

    while (!stop && !overlays_drawn) {  /* wait for the menu to appear */
        READ_STEP();
    }

    while (!stop && next_key) {
        int code = key_code(next_key);
        if (code < 0) {
            fprintf(stderr, "unknown key: %s\n", next_key);
            break;
        }
        printf("KEY %s\n", next_key);
        fflush(stdout);
        bd_user_input(bd, -1, code | BD_VK_KEY_PRESSED);
        bd_user_input(bd, -1, code | BD_VK_KEY_TYPED);
        bd_user_input(bd, -1, code | BD_VK_KEY_RELEASED);

        /* SET_BUTTON_PAGE (and similar) is queued as an HDMV event, not applied inside
         * bd_user_input(): read until the resulting redraw (a FLUSH) is seen, so the PSRs below
         * are the settled result of this key, not a stale value from before it. */
        flush_seen = 0;
        for (int reads = 0; !stop && !flush_seen && reads < SETTLE_READS; reads++) {
            READ_STEP();
        }
        printf(
            "STATUS page=%u button=%u\n", bdpriv_reg_read(bd, 1, PSR_MENU_PAGE_ID),
            bdpriv_reg_read(bd, 1, PSR_SELECTED_BUTTON_ID));
        fflush(stdout);
        next_key = strtok(NULL, ",");
    }

    while (!stop) {
        READ_STEP();
    }
#undef READ_STEP

    printf("DONE bytes=%ld overlays=%d\n", total, overlays_drawn);
    bd_close(bd);
    return 0;
}
