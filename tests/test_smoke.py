def test_imports_pure_modules():
    # These modules must import without touching hardware.
    import importlib
    for mod in ("local_tts.telemetry",):
        importlib.import_module(mod)
    assert True
