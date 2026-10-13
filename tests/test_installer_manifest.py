from scripts.check_windows_installer import changed_application_files


def test_frontend_only_upgrade_is_a_real_application_change():
    before = {'CodeScape.exe': 'same', '_internal/build-info.json': 'old',
              '_internal/web/old.js': 'old-ui'}
    after = {'CodeScape.exe': 'same', '_internal/build-info.json': 'new',
             '_internal/web/new.js': 'new-ui'}
    assert changed_application_files(before, after) == ['_internal/web/new.js', '_internal/web/old.js']


def test_build_metadata_only_change_does_not_prove_an_application_upgrade():
    before = {'CodeScape.exe': 'same', '_internal/build-info.json': 'old'}
    after = {'CodeScape.exe': 'same', '_internal/build-info.json': 'new'}
    assert changed_application_files(before, after) == []
