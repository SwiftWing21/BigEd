import sys, os, json, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'fleet'))


def test_focus_state_file_write_and_read():
    with tempfile.TemporaryDirectory() as tmpdir:
        focus_file = os.path.join(tmpdir, ".factorio_focus.json")
        state = {"on": True, "workers": ["coder_1", "coder_2"]}
        with open(focus_file, "w") as f:
            json.dump(state, f)
        with open(focus_file) as f:
            loaded = json.load(f)
        assert loaded["on"] is True
        assert loaded["workers"] == ["coder_1", "coder_2"]


def test_focus_state_missing_file():
    focus_file = "/tmp/nonexistent_focus_state_12345.json"
    try:
        with open(focus_file) as f:
            json.load(f)
        assert False, "Should have raised"
    except FileNotFoundError:
        pass  # expected


def test_read_focus_state_cached(tmp_path):
    focus_file = tmp_path / ".factorio_focus.json"
    focus_file.write_text(json.dumps({"on": True, "workers": ["coder_1"]}))

    from worker import _read_focus_state

    state = _read_focus_state(str(focus_file), _force=True)
    assert state is not None
    assert state["on"] is True
    assert state["workers"] == ["coder_1"]


def test_read_focus_state_missing_file():
    from worker import _read_focus_state
    state = _read_focus_state("/tmp/nonexistent_12345.json", _force=True)
    assert state is None
