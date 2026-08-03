from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_specs_collect_companion_qr_runtime_dependencies():
    onedir = (PROJECT_ROOT / "Colorink.spec").read_text(encoding="utf-8")
    onefile = (PROJECT_ROOT / "Colorink Onefile.spec").read_text(encoding="utf-8")

    for spec in (onedir, onefile):
        assert "'mss'" in spec
        assert "'pyzbar'" in spec
        assert "'PIL'" in spec
        assert "libzbar-64.dll" in spec
        assert "libiconv.dll" in spec


def test_qr_dependencies_are_runtime_requirements():
    requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "pyzbar>=0.1.9" in requirements
    assert "Pillow>=10.0.0" in requirements
    assert "mss>=9.0.0" in requirements
