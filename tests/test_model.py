from fractions import Fraction
from pathlib import Path

from bdauthor.model import Finding, Media, Report, Severity, SubtitleStream, to_jsonable


def test_media_capacities_are_ordered_and_sector_aligned():
    order = [Media.DVD5, Media.DVD9, Media.BD25, Media.BD50]
    capacities = [m.capacity_bytes for m in order]
    assert capacities == sorted(capacities)
    assert all(c % 2048 == 0 for c in capacities)
    assert Media.BD25.capacity_bytes == 25_025_314_816


def test_media_from_cli_value_and_label():
    assert Media("dvd9") is Media.DVD9
    assert Media.BD50.label == "BD-50"


def test_report_passed_only_without_errors():
    ok = Finding("video", Severity.OK, "fine")
    warn = Finding("size", Severity.WARNING, "tight")
    err = Finding("audio", Severity.ERROR, "aac not allowed", stream_index=1)
    assert Report(Media.BD25, (ok, warn)).passed
    report = Report(Media.BD25, (ok, warn, err))
    assert not report.passed
    assert report.errors == (err,)
    assert report.warnings == (warn,)


def test_to_jsonable_handles_fraction_path_enum_and_dataclass():
    assert to_jsonable(Fraction(24000, 1001)) == "24000/1001"
    assert to_jsonable(Path("/x/y.mkv")) == "/x/y.mkv"
    assert to_jsonable(Media.BD25) == "bd25"
    assert to_jsonable(SubtitleStream(2, "subrip", "eng", None)) == {
        "index": 2,
        "codec": "subrip",
        "language": "eng",
        "title": None,
    }
    finding = Finding("r", Severity.ERROR, "m")
    assert to_jsonable(Report(Media.BD25, (finding,)))["findings"][0]["severity"] == "error"
