import httpx
import pytest

from addshop import AddshopError
from catalog_seed import SeedProductError, TestProductSeeder as ProductSeeder


class FakeAdmin:
    def __init__(self):
        self.client = type('Client', (), {})()
        self.client.base = 'https://example.myaddshop.ru'
        self.client.http = type('Http', (), {
            'get': lambda _self, _url: httpx.Response(
                302, headers={'location': '/editor/item/77/edit/'})
        })()

    def read(self, _kind, _entity_id):
        raise AddshopError('temporary read failure')


def test_seed_failure_reports_allocated_orphan_id(tmp_path):
    seeder = ProductSeeder(FakeAdmin(), tmp_path)
    with pytest.raises(SeedProductError) as failure:
        seeder._create_one({'title': 'MCP SHOE 001'}, str(tmp_path / 'unused.png'))
    assert failure.value.product_id == 77
    assert 'temporary read failure' in str(failure.value)
