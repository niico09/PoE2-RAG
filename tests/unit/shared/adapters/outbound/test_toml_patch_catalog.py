from pathlib import Path

import pytest

from poe2_rag.shared.adapters.outbound.toml_patch_catalog import (
    PatchCatalogLoadError,
    TomlPatchCatalog,
)
from poe2_rag.shared.domain import InvalidPatchCatalogError, PatchVersion

MINIMAL = """\
current = "0.1.1"

[[patches]]
version = "0.1.0"

[[patches]]
version = "0.1.1"
released_on = 2025-01-17
"""


def v(raw: str) -> PatchVersion:
    return PatchVersion.parse(raw)


def write_catalog(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "patches.toml"
    path.write_text(text, encoding="utf-8")
    return path


class TestLoad:
    def test_reads_versions_and_current(self, tmp_path: Path) -> None:
        catalog = TomlPatchCatalog(write_catalog(tmp_path, MINIMAL))

        assert catalog.current() == v("0.1.1")
        assert catalog.known() == (v("0.1.0"), v("0.1.1"))

    def test_release_date_is_optional(self, tmp_path: Path) -> None:
        text = 'current = "0.1.0"\n\n[[patches]]\nversion = "0.1.0"\n'

        assert TomlPatchCatalog(write_catalog(tmp_path, text)).known() == (v("0.1.0"),)

    def test_accepts_a_string_path(self, tmp_path: Path) -> None:
        assert TomlPatchCatalog(str(write_catalog(tmp_path, MINIMAL))).current() == v("0.1.1")

    def test_sample_config_loads(self, request: pytest.FixtureRequest) -> None:
        catalog = TomlPatchCatalog(request.config.rootpath / "config" / "patches.toml")

        assert catalog.known()
        assert catalog.current() in catalog.known()
        assert list(catalog.known()) == sorted(catalog.known())


class TestLoadErrors:
    def test_missing_file_is_a_load_error_naming_the_path(self, tmp_path: Path) -> None:
        path = tmp_path / "absent.toml"

        with pytest.raises(PatchCatalogLoadError, match="absent.toml"):
            TomlPatchCatalog(path)

    def test_invalid_toml_is_a_load_error(self, tmp_path: Path) -> None:
        with pytest.raises(PatchCatalogLoadError):
            TomlPatchCatalog(write_catalog(tmp_path, 'current = "0.1.0\n'))

    def test_missing_current_is_a_load_error(self, tmp_path: Path) -> None:
        with pytest.raises(PatchCatalogLoadError, match="current"):
            TomlPatchCatalog(write_catalog(tmp_path, '[[patches]]\nversion = "0.1.0"\n'))

    def test_non_string_current_is_a_load_error(self, tmp_path: Path) -> None:
        with pytest.raises(PatchCatalogLoadError, match="current"):
            TomlPatchCatalog(write_catalog(tmp_path, 'current = 112\n\n[[patches]]\nversion = "0.1.0"\n'))

    def test_malformed_current_version_is_a_load_error(self, tmp_path: Path) -> None:
        with pytest.raises(PatchCatalogLoadError, match="current"):
            TomlPatchCatalog(write_catalog(tmp_path, 'current = "0.1"\n\n[[patches]]\nversion = "0.1.0"\n'))

    def test_unknown_top_level_key_is_a_load_error(self, tmp_path: Path) -> None:
        text = 'current = "0.1.0"\nlatest = "0.1.0"\n\n[[patches]]\nversion = "0.1.0"\n'

        with pytest.raises(PatchCatalogLoadError, match="latest"):
            TomlPatchCatalog(write_catalog(tmp_path, text))

    def test_patches_as_a_plain_array_is_a_load_error(self, tmp_path: Path) -> None:
        text = 'current = "0.1.0"\npatches = ["0.1.0"]\n'

        with pytest.raises(PatchCatalogLoadError, match="patches"):
            TomlPatchCatalog(write_catalog(tmp_path, text))

    def test_entry_without_a_version_is_a_load_error(self, tmp_path: Path) -> None:
        text = 'current = "0.1.0"\n\n[[patches]]\nreleased_on = 2024-12-06\n'

        with pytest.raises(PatchCatalogLoadError, match="version"):
            TomlPatchCatalog(write_catalog(tmp_path, text))

    def test_non_string_version_is_a_load_error(self, tmp_path: Path) -> None:
        text = 'current = "0.1.0"\n\n[[patches]]\nversion = 1\n'

        with pytest.raises(PatchCatalogLoadError, match="version"):
            TomlPatchCatalog(write_catalog(tmp_path, text))

    def test_malformed_version_is_a_load_error(self, tmp_path: Path) -> None:
        text = 'current = "0.1.0"\n\n[[patches]]\nversion = "0.1.0-beta"\n'

        with pytest.raises(PatchCatalogLoadError, match="version"):
            TomlPatchCatalog(write_catalog(tmp_path, text))

    def test_non_date_release_is_a_load_error(self, tmp_path: Path) -> None:
        text = 'current = "0.1.0"\n\n[[patches]]\nversion = "0.1.0"\nreleased_on = "2024-12-06"\n'

        with pytest.raises(PatchCatalogLoadError, match="released_on"):
            TomlPatchCatalog(write_catalog(tmp_path, text))

    def test_unknown_entry_key_is_a_load_error(self, tmp_path: Path) -> None:
        text = 'current = "0.1.0"\n\n[[patches]]\nversion = "0.1.0"\nrelease_date = 2024-12-06\n'

        with pytest.raises(PatchCatalogLoadError, match="release_date"):
            TomlPatchCatalog(write_catalog(tmp_path, text))

    def test_never_yields_a_partial_catalog(self, tmp_path: Path) -> None:
        text = MINIMAL + '\n[[patches]]\nversion = "not-a-version"\n'

        with pytest.raises(PatchCatalogLoadError):
            TomlPatchCatalog(write_catalog(tmp_path, text))


class TestCatalogInvariants:
    def test_absent_patches_means_an_empty_catalog(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidPatchCatalogError, match="empty"):
            TomlPatchCatalog(write_catalog(tmp_path, 'current = "0.1.0"\n'))

    def test_repeated_version_is_rejected(self, tmp_path: Path) -> None:
        text = 'current = "0.1.0"\n\n[[patches]]\nversion = "0.1.0"\n\n[[patches]]\nversion = "0.1.0"\n'

        with pytest.raises(InvalidPatchCatalogError, match="duplicate"):
            TomlPatchCatalog(write_catalog(tmp_path, text))

    def test_current_absent_from_the_list_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidPatchCatalogError, match="current"):
            TomlPatchCatalog(write_catalog(tmp_path, 'current = "0.1.1"\n\n[[patches]]\nversion = "0.1.0"\n'))
