import av


def test_good_mkv_fixture_is_a_readable_1080p_h264_ac3_file(good_mkv):
    with av.open(str(good_mkv)) as container:
        kinds = {s.type: s.codec_context for s in container.streams}
    assert kinds["video"].name == "h264"
    assert (kinds["video"].width, kinds["video"].height) == (1920, 1080)
    assert kinds["audio"].name == "ac3"
