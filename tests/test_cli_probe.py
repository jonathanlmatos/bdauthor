import json

from typer.testing import CliRunner

from bdauthor.cli import app
from bdauthor.exitcodes import ExitCode

runner = CliRunner()


def test_probe_table_lists_streams(good_mkv):
    result = runner.invoke(app, ["probe", str(good_mkv)])
    assert result.exit_code == ExitCode.OK
    assert "h264" in result.stdout
    assert "1920x1080" in result.stdout
    assert "23.976p" in result.stdout
    assert "ac3" in result.stdout


def test_probe_json_is_valid_and_machine_readable(good_mkv):
    result = runner.invoke(app, ["probe", str(good_mkv), "--json"])
    assert result.exit_code == ExitCode.OK
    data = json.loads(result.stdout)
    assert data["video"][0]["fps"] == "24000/1001"
    assert data["video"][0]["height"] == 1080
    assert data["audio"][0]["codec"] == "ac3"


def test_probe_missing_file_is_a_usage_error(tmp_path):
    result = runner.invoke(app, ["probe", str(tmp_path / "nope.mkv")])
    assert result.exit_code == 2


def test_probe_unreadable_media_exits_with_check_failed(tmp_path):
    path = tmp_path / "notes.mkv"
    path.write_text("not media")
    result = runner.invoke(app, ["probe", str(path)])
    assert result.exit_code == ExitCode.CHECK_FAILED
