"""Real stdio MCP handshake. Does not contact Addshop."""
import asyncio
import json
import sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).with_name('server.py'))])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            assert names == [
                'addshop_access_policy', 'addshop_add_offer', 'addshop_audit_log', 'addshop_audit_store',
                'addshop_batch_create_product_variants', 'addshop_batch_journals',
                'addshop_batch_set_product_image_colors', 'addshop_batch_update',
                'addshop_batch_update_offers', 'addshop_batch_update_product_properties',
                'addshop_batch_upload_product_images',
                'addshop_build_product', 'addshop_build_product_batch',
                'addshop_bulk_edit_offer_prices',
                'addshop_bulk_edit_offer_stock', 'addshop_bulk_edit_products',
                'addshop_bulk_reprice_offers', 'addshop_bulk_update_existing_property',
                'addshop_bulk_update_offer_skus',
                'addshop_capabilities', 'addshop_catalog_dashboard', 'addshop_catalog_taxonomy',
                'addshop_change_journals', 'addshop_change_product_component',
                'addshop_clone_product', 'addshop_compare_products', 'addshop_compare_snapshot',
                'addshop_contract_health', 'addshop_create_entity', 'addshop_create_snapshot',
                'addshop_delete_hidden_products', 'addshop_describe_entity_form',
                'addshop_duplicate_product_audit',
                'addshop_export_offers_csv', 'addshop_export_products_csv', 'addshop_get_entity',
                'addshop_import_offers_csv', 'addshop_import_products_csv',
                'addshop_integration_catalog', 'addshop_integration_configure',
                'addshop_integration_status', 'addshop_inventory_replenishment_report',
                'addshop_list_entities', 'addshop_local_doctor',
                'addshop_order_summary',
                'addshop_product_blueprint', 'addshop_product_property_matrix',
                'addshop_product_publication_readiness',
                'addshop_product_quality_audit',
                'addshop_product_variant_matrix',
                'addshop_read_offers', 'addshop_read_product',
                'addshop_read_product_components',
                'addshop_read_product_deletion_recovery',
                'addshop_read_product_images',
                'addshop_recover_test', 'addshop_scan_custom_code', 'addshop_search',
                'addshop_seed_hidden_test_products',
                'addshop_select_products', 'addshop_seo_audit', 'addshop_set_product_image_color',
                'addshop_set_product_visibility_batch', 'addshop_set_theme',
                'addshop_suggest_product_slug', 'addshop_test_roundtrip', 'addshop_theme_catalog',
                'addshop_undo_entity_update',
                'addshop_update_entity', 'addshop_update_offer',
                'addshop_upload_product_image', 'addshop_validate_product_changes',
                'addshop_write_safety_status'
            ]
            print(json.dumps({'stdio_handshake': 'passed', 'tools': names}))


if __name__ == '__main__':
    asyncio.run(main())
