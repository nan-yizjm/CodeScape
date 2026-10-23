import json

import pytest
from codescape.checkpoints import dump_checkpoint, load_checkpoint
from codescape.models import Architecture, Overview


def fixture():
    return Architecture(modules=[], overview=Overview(introduction='人工检查点样例',
        capabilities=[], principle='不调用模型', limitations=[]))


def test_range_metadata_survives_json_without_changing_model_or_api_contract():
    result = fixture()
    result._source_ranges = {'owned.py': (7, 18), 'package.json': (1, 1)}
    stored = json.loads(json.dumps(dump_checkpoint(result)))
    assert load_checkpoint(stored)._source_ranges == result._source_ranges
    assert result.model_dump() == fixture().model_dump()
    assert '_source_ranges' not in Architecture.model_json_schema()['properties']
    assert 'source_ranges' not in Architecture.model_json_schema()['properties']


def test_legacy_checkpoint_remains_explicitly_without_range_proof():
    result = fixture()
    assert load_checkpoint(result.model_dump())._source_ranges is None
    assert dump_checkpoint(result) == result.model_dump()


@pytest.mark.parametrize('ranges', [{}, None, {'owned.py': [0, 4]}, {'owned.py': [4, 2]},
    {'owned.py': [True, 4]}, {'owned.py': [1, '4']}, {'owned.py': [1, 2, 3]}])
def test_invalid_persisted_ranges_never_fall_back_to_legacy(ranges):
    with pytest.raises(ValueError):
        load_checkpoint({'checkpoint_format': 1, 'architecture': fixture().model_dump(), 'source_ranges': ranges})


@pytest.mark.parametrize('version', [True, 2, '1'])
def test_unknown_checkpoint_format_is_not_reinterpreted(version):
    with pytest.raises(ValueError):
        load_checkpoint({'checkpoint_format': version, 'architecture': fixture().model_dump(),
                         'source_ranges': {'owned.py': [1, 2]}})
