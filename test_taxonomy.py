class FakeState:
    def __init__(self, values): self.values = values


class FakeAdmin:
    def list(self, kind):
        values = {'category': [{'id': 1}, {'id': 2}], 'manufacturer': [], 'size': [], 'color': []}
        return {'items': values[kind]}
    def read(self, kind, entity_id):
        data = {1: {'title': ['Root'], 'parent_id': ['0'], 'dir': ['root']},
                2: {'title': ['Child'], 'parent_id': ['1'], 'dir': ['child']}}
        return FakeState(data[entity_id])


def test_taxonomy_paths():
    from taxonomy import catalog_taxonomy
    result = catalog_taxonomy(FakeAdmin())
    assert result['categories'][1]['path'] == 'Root / Child'
    assert not result['orphan_category_ids'] and not result['category_cycles']
