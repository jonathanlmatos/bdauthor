import json

from typer.testing import CliRunner

from bdauthor.cli import app
from bdauthor.exitcodes import ExitCode

runner = CliRunner()


def test_check_passes_for_compliant_file(good_mkv):
    result = runner.invoke(app, ["check", str(good_mkv)])
    assert result.exit_code == ExitCode.OK
    assert "PASSED" in result.stdout
    assert "BD-25" in result.stdout


def test_check_fails_with_exit_code_1_and_explains_why(make_mkv):
    result = runner.invoke(app, ["check", str(make_mkv("aac.mkv", acodec="aac"))])
    assert result.exit_code == ExitCode.CHECK_FAILED
    assert "FAILED" in result.stdout
    assert "codec aac is not allowed" in result.stdout


def test_check_reports_cropped_resolution(make_mkv):
    result = runner.invoke(app, ["check", str(make_mkv("scope.mkv", width=1920, height=800))])
    assert result.exit_code == ExitCode.CHECK_FAILED
    assert "1920x800" in result.stdout


def test_check_accepts_every_media_option(good_mkv):
    for media in ("dvd5", "dvd9", "bd25", "bd50"):
        result = runner.invoke(app, ["check", str(good_mkv), "--media", media])
        assert result.exit_code == ExitCode.OK, media


def test_check_rejects_unknown_media(good_mkv):
    result = runner.invoke(app, ["check", str(good_mkv), "--media", "cd"])
    assert result.exit_code == 2


def test_check_json_shape_for_passing_file(good_mkv):
    result = runner.invoke(app, ["check", str(good_mkv), "--json"])
    assert result.exit_code == ExitCode.OK
    data = json.loads(result.stdout)
    assert data["passed"] is True
    assert data["media"] == "bd25"
    assert {f["severity"] for f in data["findings"]} == {"ok"}


def test_check_json_for_failing_file_keeps_exit_code(make_mkv):
    result = runner.invoke(app, ["check", str(make_mkv("aac.mkv", acodec="aac")), "--json"])
    assert result.exit_code == ExitCode.CHECK_FAILED
    data = json.loads(result.stdout)
    assert data["passed"] is False
    assert any(f["severity"] == "error" and f["rule"] == "audio" for f in data["findings"])


def test_check_unreadable_file_exits_with_check_failed(tmp_path):
    path = tmp_path / "notes.mkv"
    path.write_text("not media")
    assert runner.invoke(app, ["check", str(path)]).exit_code == ExitCode.CHECK_FAILED


def test_check_transcode_audio_turns_the_audio_error_into_a_warning(make_mkv):
    path = str(make_mkv("aac.mkv", acodec="aac"))
    assert runner.invoke(app, ["check", path]).exit_code == ExitCode.CHECK_FAILED

    result = runner.invoke(app, ["check", path, "--transcode-audio"])
    assert result.exit_code == ExitCode.OK
    assert "will be re-encoded from aac" in result.stdout
    assert "PASSED: 0 error(s), 1 warning(s)" in result.stdout


def test_check_transcode_audio_does_not_hide_other_problems(make_mkv):
    path = make_mkv("scope-aac.mkv", acodec="aac", width=1920, height=800)
    result = runner.invoke(app, ["check", str(path), "--transcode-audio"])
    assert result.exit_code == ExitCode.CHECK_FAILED
    assert "1920x800" in result.stdout


def test_check_transcode_audio_json_lists_the_warning(make_mkv):
    path = str(make_mkv("aac.mkv", acodec="aac"))
    data = json.loads(runner.invoke(app, ["check", path, "--transcode-audio", "--json"]).stdout)
    assert data["passed"] is True
    assert [f["severity"] for f in data["findings"] if f["rule"] == "audio"] == ["warning", "ok"]
