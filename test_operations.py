import httpx
import json
import pytest

from addshop import Addshop, AddshopError
from admin import AddshopAdmin
from operations import AddshopOperations
from test_admin import Site, BASE


def test_snapshot_and_batch(tmp_path):
    site = Site()
    client = Addshop(BASE, transport=httpx.MockTransport(site))
    admin = AddshopAdmin(client, tmp_path / 'journals')
    ops = AddshopOperations(admin, tmp_path)
    snapshot = ops.snapshot(['category'], 10)
    assert snapshot['counts'] == {'category': 1}
    assert len(snapshot['sha256']) == 64
    assert ops.compare_snapshot(snapshot['snapshot_id'])['change_count'] == 0
    state = admin.read('category', 7)
    batch = [{'kind': 'category', 'entity_id': 7,
              'changes': {'title': 'Batch'}, 'expected_revision': state.revision}]
    assert ops.batch_update(batch)['mode'] == 'preview'
    result = ops.batch_update(batch, True)
    assert result['verified'] and result['run_id'] and site.title == 'Batch'
    record = json.loads((tmp_path / 'batches' / f"{result['run_id']}.json").read_text(encoding='utf-8'))
    assert record['status'] == 'verified'
    public_journal = ops.batch_journals()['journals'][0]
    assert public_journal['run_id'] == result['run_id']
    assert public_journal['completed_count'] == 1
    assert public_journal['targets'][0]['fields'] == ['title']
    assert 'changes' not in public_journal['targets'][0]
    diff = ops.compare_snapshot(snapshot['snapshot_id'])
    assert diff['summary'] == {'changed': 1}
    assert 'title' in diff['changes'][0]['fields']


def test_change_journal_listing(tmp_path):
    site = Site()
    client = Addshop(BASE, transport=httpx.MockTransport(site))
    folder = tmp_path / 'admin'
    folder.mkdir()
    run_id = 'a' * 32
    (folder / f'{run_id}.json').write_text(json.dumps({
        'run_id': run_id, 'kind': 'product', 'entity_id': 5, 'status': 'verified',
        'changes': {'title': ['New']}, 'revision_before': 'x', 'revision_after': 'y'}), encoding='utf-8')
    result = AddshopOperations(AddshopAdmin(client), tmp_path).change_journals('product', 5)
    assert result['journals'][0]['run_id'] == run_id
    assert result['journals'][0]['fields'] == ['title']


def test_batch_refuses_concurrent_change_after_preview(tmp_path):
    class State:
        def __init__(self, revision):
            self.revision = revision
            self.values = {'title': ['Before']}

    class ConcurrentAdmin:
        def preview_update(self, _kind, _entity_id, _changes, _expected):
            return State('preview-revision'), {'title': ['After']}, {
                'title': {'before': ['Before'], 'after': ['After']}}

        def read(self, _kind, _entity_id):
            return State('changed-by-someone-else')

        def update(self, *_args, **_kwargs):
            raise AssertionError('concurrent state must never be overwritten')

    ops = AddshopOperations(ConcurrentAdmin(), tmp_path)
    operation = [{'kind': 'product', 'entity_id': 7, 'changes': {'title': 'After'},
                  'expected_revision': 'preview-revision'}]
    with pytest.raises(AddshopError, match='changed after batch preview'):
        ops.batch_update(operation, True)
    journals = list((tmp_path / 'batches').glob('*.json'))
    assert len(journals) == 1
    record = json.loads(journals[0].read_text(encoding='utf-8'))
    assert record['status'] == 'failed_rolled_back'
    assert record['completed'] == []
