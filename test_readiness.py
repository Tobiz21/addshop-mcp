from readiness import ProductReadinessAudit


class State:
    def __init__(self, product_id, fields):
        self.values = {key: [str(value)] for key, value in fields.items()}
        self.revision = f'card-{product_id}'


class Admin:
    client = object()

    def __init__(self):
        self.fields = {
            1: {'title': 'Good Shoe', 'description': 'x' * 100, 'dir': 'same',
                'barcode': 'BAR', 'category_id': '9', 'seo_title': 'Good',
                'seo_description': 'Description', 'default_price': '100', 'active': '0'},
            2: {'title': 'Bad Shoe', 'description': 'short', 'dir': 'same',
                'barcode': 'bar', 'category_id': '', 'seo_title': '',
                'seo_description': '', 'default_price': '0', 'active': '1'},
        }

    def list(self, _kind):
        return {'items': [{'id': 1}, {'id': 2}]}

    def read(self, _kind, product_id):
        fields = dict(self.fields[product_id])
        active = fields.pop('active')
        state = State(product_id, fields)
        state.values['active'] = ['0', '1'] if active == '1' else ['0']
        return state


class Offers:
    def read(self, product_id):
        if product_id == 1:
            offers = [{'id': '10', 'vendor_code': 'SKU', 'price': '100',
                       'quantity': '5', 'infinitely': '0', 'size_id': '42', 'color_id': '7'}]
        else:
            offers = [{'id': '20', 'vendor_code': 'sku', 'price': '0',
                       'quantity': '0', 'infinitely': '0', 'size_id': '42', 'color_id': '7'}]
        return {'revision': f'offers-{product_id}', 'offers': offers}


class Media:
    def images(self, product_id):
        return {'revision': f'images-{product_id}',
                'images': [{'id': '1'}] if product_id == 1 else []}


class Components:
    def read(self, product_id):
        return {'revision': f'components-{product_id}',
                'components': {'item_properties': [{'id': '1'}] if product_id == 1 else []}}


def test_publication_readiness_routes_repairs_and_duplicates():
    audit = ProductReadinessAudit(Admin(), Offers(), Media(), Components()).audit(limit=10)
    assert audit['scanned'] == 2 and audit['ready_count'] == 0
    assert audit['duplicate_skus']['sku'][0]['product_id'] == 1
    assert audit['duplicate_slugs']['same'] == [1, 2]
    assert audit['duplicate_barcodes']['bar'] == [1, 2]
    bad = next(item for item in audit['products'] if item['product_id'] == 2)
    assert {'missing_category', 'no_images', 'no_positive_price', 'out_of_stock'} <= set(bad['blockers'])
    assert bad['published'] and 2 in audit['published_with_blockers']
    tools = {action.get('tool') for action in bad['repair_actions'] if action.get('tool')}
    assert 'addshop_update_entity' in tools
    assert 'addshop_batch_upload_product_images' in tools
    assert 'addshop_batch_update_offers' in tools
    assert bad['revisions'] == {'card': 'card-2', 'offers': 'offers-2',
                                'images': 'images-2', 'components': 'components-2'}
