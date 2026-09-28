import subprocess
import importlib

from typer.testing import CliRunner

from bdauthor.build import REQUIRED_FILES
from bdauthor.cli import app
from bdauthor.exitcodes import ExitCode

runner = CliRunner()


def test_build_creates_bdmv_and_prints_the_report(good_mkv, tmp_path, tsmuxer):
    out = tmp_path / "disc"
    result = runner.invoke(app, ["build", str(good_mkv), "-o", str(out)])
    assert result.exit_code == ExitCode.OK, result.output
    assert "PASSED" in result.stdout
    assert "Built" in result.stdout
    for name in REQUIRED_FILES:
        assert (out / "BDMV" / name).is_file(), name


def test_build_refuses_to_overwrite_then_force_replaces(good_mkv, tmp_path, tsmuxer):
    args = ["build", str(good_mkv), "-o", str(tmp_path / "disc")]
    assert runner.invoke(app, args).exit_code == ExitCode.OK

    again = runner.invoke(app, args)
    assert again.exit_code == ExitCode.BUILD_FAILED
    assert "--force" in again.output

    assert runner.invoke(app, [*args, "--force"]).exit_code == ExitCode.OK


def test_build_stops_on_incompatible_source_without_writing_anything(make_mkv, tmp_path, tsmuxer):
    out = tmp_path / "disc"
    result = runner.invoke(app, ["build", str(make_mkv("aac.mkv", acodec="aac")), "-o", str(out)])
    assert result.exit_code == ExitCode.CHECK_FAILED
    assert "FAILED" in result.stdout
    assert "codec aac is not allowed" in result.stdout
    assert not out.exists()


def test_build_reports_a_missing_tsmuxer(good_mkv, tmp_path, monkeypatch):
    # `bdauthor.cli.build` as an attribute is the command function, so fetch the module itself.
    build_module = importlib.import_module("bdauthor.cli.build")
    monkeypatch.setattr(build_module, "find_tsmuxer", lambda: None)
    result = runner.invoke(app, ["build", str(good_mkv), "-o", str(tmp_path / "disc")])
    assert result.exit_code == ExitCode.MISSING_DEPENDENCY
    assert "tsMuxeR not found" in result.output


def test_build_requires_an_output_directory(good_mkv):
    assert runner.invoke(app, ["build", str(good_mkv)]).exit_code == 2


def test_build_rejects_unknown_media(good_mkv, tmp_path):
    result = runner.invoke(app, ["build", str(good_mkv), "-o", str(tmp_path), "-m", "cd"])
    assert result.exit_code == 2


def test_build_transcode_audio_re_encodes_incompatible_audio(make_mkv, tmp_path, tsmuxer):
    source = str(make_mkv("aac.mkv", acodec="aac"))
    out = tmp_path / "disc"

    refused = runner.invoke(app, ["build", source, "-o", str(out)])
    assert refused.exit_code == ExitCode.CHECK_FAILED
    assert not out.exists()

    result = runner.invoke(app, ["build", source, "-o", str(out), "--transcode-audio"])
    assert result.exit_code == ExitCode.OK, result.output
    assert "will be re-encoded from aac" in result.stdout
    assert "Built" in result.stdout
    for name in REQUIRED_FILES:
        assert (out / "BDMV" / name).is_file(), name


def test_build_without_menu_has_no_menu_clip(good_mkv, tmp_path, tsmuxer):
    out = tmp_path / "disc"
    assert runner.invoke(app, ["build", str(good_mkv), "-o", str(out)]).exit_code == ExitCode.OK
    assert not (out / "BDMV" / "PLAYLIST" / "00001.mpls").exists()


def test_build_with_menu_adds_the_menu_and_the_play_button_starts_the_movie(
    good_mkv, tmp_path, tsmuxer, libbluray_tool
):
    out = tmp_path / "disc"
    result = runner.invoke(app, ["build", str(good_mkv), "-o", str(out), "--menu"])
    assert result.exit_code == ExitCode.OK, result.output
    for name in ("PLAYLIST/00001.mpls", "CLIPINF/00001.clpi", "STREAM/00001.m2ts"):
        assert (out / "BDMV" / name).is_file(), name

    played = subprocess.run(
        [str(libbluray_tool("bd_menu_test")), "-k", "enter", str(out)], capture_output=True, text=True, timeout=60
    ).stdout.splitlines()
    assert "EVENT MENU 1" in played  # the menu came up first
    assert "EVENT TITLE 1" in played[played.index("KEY enter") :]  # and Play starts the movie


def test_build_with_menu_refuses_unsupported_video_before_muxing(good_mkv, tmp_path, monkeypatch):
    import bdauthor.build as build_module
    from bdauthor.menu import MenuError

    def refuse(video):
        raise MenuError("menus for interlaced video are not supported yet")

    monkeypatch.setattr(build_module, "check_menu_supported", refuse)
    out = tmp_path / "disc"
    result = runner.invoke(app, ["build", str(good_mkv), "-o", str(out), "--menu"])
    assert result.exit_code == ExitCode.BUILD_FAILED
    assert "menu: menus for interlaced video" in result.output
    assert not out.exists()  # nothing was written
