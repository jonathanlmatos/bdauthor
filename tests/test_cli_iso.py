from typer.testing import CliRunner

from bdauthor.cli import app
from bdauthor.exitcodes import ExitCode

runner = CliRunner()


def test_iso_writes_the_image_and_reports_it(make_disc, tmp_path):
    out = tmp_path / "disc.iso"
    result = runner.invoke(app, ["iso", str(make_disc()), "-o", str(out)])
    assert result.exit_code == ExitCode.OK, result.output
    assert out.is_file()
    assert "Wrote" in result.stdout
    assert "8 files" in result.stdout
    assert "label MY_DISC" in result.stdout


def test_iso_label_option(make_disc, tmp_path):
    result = runner.invoke(
        app, ["iso", str(make_disc()), "-o", str(tmp_path / "d.iso"), "--label", "Sample"]
    )
    assert result.exit_code == ExitCode.OK
    assert "label SAMPLE" in result.stdout


def test_iso_refuses_to_overwrite_then_force_replaces(make_disc, tmp_path):
    args = ["iso", str(make_disc()), "-o", str(tmp_path / "disc.iso")]
    assert runner.invoke(app, args).exit_code == ExitCode.OK

    again = runner.invoke(app, args)
    assert again.exit_code == ExitCode.BUILD_FAILED
    assert "--force" in again.output

    assert runner.invoke(app, [*args, "--force"]).exit_code == ExitCode.OK


def test_iso_reports_a_source_that_is_not_a_disc(tmp_path):
    (tmp_path / "empty").mkdir()
    result = runner.invoke(app, ["iso", str(tmp_path / "empty"), "-o", str(tmp_path / "d.iso")])
    assert result.exit_code == ExitCode.BUILD_FAILED
    assert "does not contain a BDMV" in result.output


def test_iso_checks_the_capacity_of_the_chosen_media(make_disc, tmp_path):
    root = make_disc()
    with open(root / "BDMV" / "STREAM" / "00000.m2ts", "wb") as f:
        f.truncate(5 * 2**30)
    result = runner.invoke(app, ["iso", str(root), "-o", str(tmp_path / "d.iso"), "-m", "dvd5"])
    assert result.exit_code == ExitCode.BUILD_FAILED
    assert "exceeds DVD-5 capacity" in result.output


def test_iso_requires_an_output_and_an_existing_directory(make_disc, tmp_path):
    assert runner.invoke(app, ["iso", str(make_disc())]).exit_code == 2
    assert runner.invoke(app, ["iso", str(tmp_path / "nope"), "-o", "x.iso"]).exit_code == 2
