from slug import slugify, suggest_product_slug


class State:
    def __init__(self, slug): self.values = {'dir': [slug]}
class Admin:
    def list(self, kind): return {'items': [{'id': 1}, {'id': 2}]}
    def read(self, kind, entity_id): return State('krasnoe-plate' if entity_id == 1 else 'other')


def test_slug_suggestion_collision():
    assert slugify('Красное платье!') == 'krasnoe-plate'
    result = suggest_product_slug(Admin(), 'Красное платье!')
    assert result['suggested_slug'] == 'krasnoe-plate-2'
