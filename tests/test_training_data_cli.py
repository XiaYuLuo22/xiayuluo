import json

from assistant.training_backup import snapshot
from assistant.training_data import main


def test_verify_and_restore_never_need_location_registration(tmp_path, capsys, monkeypatch):
    root = tmp_path / 'original'
    (root / 'models').mkdir(parents=True)
    (root / 'models/weights.npz').write_bytes(b'original weights')
    backup = snapshot(root)
    monkeypatch.setattr('assistant.training_data.TrainingStorage', lambda *args: (_ for _ in ()).throw(AssertionError('No registry needed for recovery')))
    assert main(['verify', backup['path']]) == 0
    assert json.loads(capsys.readouterr().out)['files'] == 1
    recovered = tmp_path / 'recovery-copy'
    assert main(['restore', backup['path'], '--to', str(recovered)]) == 0
    assert (recovered / 'models/weights.npz').read_bytes() == b'original weights'
    assert (root / 'models/weights.npz').read_bytes() == b'original weights'
    assert main(['restore', backup['path'], '--to', str(root)]) == 1
    assert (root / 'models/weights.npz').read_bytes() == b'original weights'


def test_restore_rejects_relative_target(tmp_path, capsys):
    assert main(['restore', str(tmp_path / 'unavailable.zip'), '--to', 'relative']) == 1
    assert '绝对目录' in capsys.readouterr().err
