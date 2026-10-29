"""Run with a dedicated shop session supplied through environment variables."""
import os
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from addshop import Addshop, AddshopError
from admin import AddshopAdmin, ENTITY_REGISTRY
from offers import AddshopOffers
from offer_batch import OfferBatch
from variant_matrix import VariantMatrix
from inventory import InventoryReport
from themes import AddshopThemes
from catalog_seed import TestProductSeeder
from visibility import ProductVisibilityBatch
from integrations import AddshopIntegrations, integration_catalog
from audit import seo_audit
from media import AddshopMedia
from media_batch import MediaBatch
from operations import AddshopOperations
from components import AddshopComponents
from privacy import redact_fields
from commerce import order_summary
from policy import require_write, public_policy
from auditlog import AuditLog, audit_target
from security import scan_custom_code
from safety import WriteGate
from doctor import local_doctor
from catalog import CatalogTools
from product_builder import ProductBuilder
from product_batch_builder import ProductBatchBuilder
from product_deletion import ProductDeletion
from property_batch import ProductPropertyBatch
from offer_create_batch import OfferCreateBatch
from readiness import ProductReadinessAudit
from product_clone import ProductCloner
from product_inspection import ProductInspection
from catalog_csv import CatalogCsv
from offer_csv import OfferCsv
from product_validation import validate_product_changes
from taxonomy import catalog_taxonomy
from slug import suggest_product_slug

mcp = FastMCP('Addshop prototype')


def main():
    mcp.run(transport='stdio')


def _audit(event, target, result):
    if result.get('mode') != 'applied' and not result.get('applied'):
        return result
    state_dir = os.getenv('ADDSHOP_STATE_DIR', 'state')
    result['audit_hash'] = AuditLog(state_dir).record(
        event, target, 'verified' if result.get('verified', True) else 'unknown',
        {'run_id': result.get('run_id')})
    return result


def _gate(event, target):
    return WriteGate(os.getenv('ADDSHOP_STATE_DIR', 'state')).allow(event, target)


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_read_product(product_id: int) -> dict:
    """Read product form over authenticated HTTPS; return fields and revision, never cookies."""
    client = Addshop.from_env()
    try:
        return client.read(product_id).public()
    finally:
        client.http.close()


@mcp.tool()
def addshop_test_roundtrip(product_id: int, expected_revision: str, apply: bool = False) -> dict:
    """Preview or test short_description and restore it. Only an allowlisted unpublished MCP TEST product."""
    client = Addshop.from_env()
    try:
        if apply:
            require_write(special='test')
            _gate('test_roundtrip', {'product_id': product_id})
        return client.roundtrip(product_id, expected_revision, apply)
    finally:
        client.http.close()


@mcp.tool()
def addshop_recover_test(run_id: str) -> dict:
    """Reconcile an interrupted test and restore its original description without overwriting third-party edits."""
    client = Addshop.from_env()
    try:
        require_write(special='test')
        _gate('recover_test', {'run_id': run_id})
        return client.recover(run_id)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_list_entities(kind: str) -> dict:
    """List one discovered entity kind. Supported kinds are in addshop_capabilities."""
    client = Addshop.from_env()
    try:
        return AddshopAdmin(client).list(kind)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_audit_store() -> dict:
    """Read all supported entity lists and report counts/errors without changing the shop."""
    client = Addshop.from_env()
    try:
        admin = AddshopAdmin(client)
        result = {}
        for kind in ENTITY_REGISTRY:
            try:
                listing = admin.list(kind)
                result[kind] = {'ok': True, 'count': listing['count']}
            except Exception as exc:
                result[kind] = {'ok': False, 'error': type(exc).__name__}
        try:
            settings = admin.read('settings')
            result['settings'] = {'ok': True, 'revision': settings.revision,
                                  'field_count': len(settings.safe_values)}
        except Exception as exc:
            result['settings'] = {'ok': False, 'error': type(exc).__name__}
        return {'base_url': client.base, 'coverage': result,
                'all_ok': all(item['ok'] for item in result.values())}
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_seo_audit(kind: str = 'product', limit: int = 100) -> dict:
    """Audit missing SEO fields, thin descriptions and duplicate slugs/titles."""
    client = Addshop.from_env()
    try:
        return seo_audit(AddshopAdmin(client), kind, limit)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_contract_health() -> dict:
    """Verify all known list/detail form contracts without sending writes."""
    client = Addshop.from_env()
    try:
        return AddshopOperations(AddshopAdmin(client)).contract_health()
    finally:
        client.http.close()


@mcp.tool()
def addshop_create_snapshot(kinds: list[str] | None = None, max_each: int = 500) -> dict:
    """Save a local redacted JSON snapshot before mass operations."""
    client = Addshop.from_env()
    try:
        state_dir = os.getenv('ADDSHOP_STATE_DIR', 'state')
        return AddshopOperations(AddshopAdmin(client), state_dir).snapshot(kinds, max_each)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_compare_snapshot(snapshot_id: str, limit: int = 1000) -> dict:
    """Verify a snapshot checksum and report current new/missing/changed entities."""
    client = Addshop.from_env()
    try:
        state_dir = os.getenv('ADDSHOP_STATE_DIR', 'state')
        return AddshopOperations(AddshopAdmin(client), state_dir).compare_snapshot(snapshot_id, limit)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_change_journals(kind: str | None = None, entity_id: int | None = None,
                            limit: int = 100) -> dict:
    """List value-free generic update journals so an exact run can be previewed for undo."""
    return AddshopOperations(None, os.getenv('ADDSHOP_STATE_DIR', 'state')).change_journals(
        kind, entity_id, limit)


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_batch_journals(limit: int = 100) -> dict:
    """List value-free transactional batch journals, completion and rollback status."""
    return AddshopOperations(None, os.getenv('ADDSHOP_STATE_DIR', 'state')).batch_journals(limit)


@mcp.tool()
def addshop_batch_update(operations: list[dict], apply: bool = False,
                         confirm_publish: bool = False) -> dict:
    """Preview/apply up to 50 revision-checked updates; roll back completed items after failure."""
    publishes = False
    for op in operations:
        active = op.get('changes', {}).get('active') if isinstance(op, dict) else None
        values = active if isinstance(active, list) else [active]
        publishes = publishes or any(str(value) == '1' for value in values)
    if apply and publishes and not confirm_publish:
        raise AddshopError('A batch containing publication requires confirm_publish=true.')
    validations = []
    for op in operations:
        if isinstance(op, dict) and op.get('kind') == 'product':
            validation = validate_product_changes(op.get('changes', {}))
            if not validation['valid']:
                raise AddshopError(f'Invalid product changes: {validation["errors"]}')
            validations.extend(validation['warnings'])
    if apply:
        for op in operations:
            require_write(kind=op.get('kind') if isinstance(op, dict) else None)
        _gate('batch_update', {'count': len(operations)})
    client = Addshop.from_env()
    try:
        state_dir = os.getenv('ADDSHOP_STATE_DIR', 'state')
        result = AddshopOperations(AddshopAdmin(client, Path(state_dir) / 'admin'), state_dir).batch_update(
            operations, apply)
        result['validation_warnings'] = sorted(set(validations))
        fields = {field for op in operations for field in op.get('changes', {})}
        return _audit('batch_update', audit_target(kinds=sorted({op.get('kind') for op in operations}),
                                                   fields=fields, count=len(operations)), result)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_get_entity(kind: str, entity_id: int | None = None,
                       include_personal_data: bool = False) -> dict:
    """Read an entity; order/customer personal data is redacted unless explicitly requested."""
    client = Addshop.from_env()
    try:
        result = AddshopAdmin(client).read(kind, entity_id).public()
        result['fields'] = redact_fields(kind, result['fields'], include_personal_data)
        result['personal_data_included'] = bool(include_personal_data and kind in ('order', 'customer'))
        return result
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_describe_entity_form(kind: str, entity_id: int | None = None) -> dict:
    """Describe editable fields, types and select options without exposing secrets."""
    client = Addshop.from_env()
    try:
        return AddshopAdmin(client).describe(kind, entity_id)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_search(query: str, kinds: list[str] | None = None, limit: int = 50) -> dict:
    """Search supported entity lists by title or exact numeric ID."""
    client = Addshop.from_env()
    try:
        return AddshopAdmin(client).search(query, kinds, limit)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_select_products(selector: dict | None = None, limit: int = 100) -> dict:
    """Select products by IDs, title, category, manufacturer or publication state."""
    client = Addshop.from_env()
    try:
        return CatalogTools(AddshopAdmin(client)).select(selector, limit)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_validate_product_changes(changes: dict) -> dict:
    """Validate product titles, slugs, numeric values, IDs and SEO lengths before preview."""
    return validate_product_changes(changes)


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_suggest_product_slug(title: str, exclude_product_id: int | None = None) -> dict:
    """Transliterate a title and find a unique product slug against the live catalog."""
    client = Addshop.from_env()
    try:
        return suggest_product_slug(AddshopAdmin(client), title, exclude_product_id)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_product_quality_audit(selector: dict | None = None, limit: int = 50) -> dict:
    """Audit selected products for content, SEO, images, offers, stock, properties and duplicate SKUs."""
    client = Addshop.from_env()
    try:
        return CatalogTools(AddshopAdmin(client)).quality_audit(selector, limit)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_product_publication_readiness(selector: dict | None = None,
                                          limit: int = 100) -> dict:
    """Audit publication blockers, duplicates and route each repair to an existing MCP tool."""
    client = Addshop.from_env()
    try:
        return ProductReadinessAudit(AddshopAdmin(client)).audit(selector, limit)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_catalog_dashboard(selector: dict | None = None, limit: int = 200) -> dict:
    """Aggregate publication, price, stock, variants, images, categories and manufacturers."""
    client = Addshop.from_env()
    try:
        return CatalogTools(AddshopAdmin(client)).dashboard(selector, limit)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_inventory_replenishment_report(selector: dict | None = None,
                                           low_stock_threshold: float = 5,
                                           target_stock: float = 10,
                                           include_zero: bool = True,
                                           limit: int = 200) -> dict:
    """Plan replenishment for finite-stock variants below a threshold without changing stock."""
    client = Addshop.from_env()
    try:
        return InventoryReport(AddshopAdmin(client)).replenishment(
            selector, low_stock_threshold, target_stock, include_zero, limit)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_product_property_matrix(selector: dict | None = None, limit: int = 100) -> dict:
    """Read product characteristics as a matrix with definitions and distinct-value indexes."""
    client = Addshop.from_env()
    try:
        return CatalogTools(AddshopAdmin(client)).property_matrix(selector, limit)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_duplicate_product_audit(selector: dict | None = None, limit: int = 200) -> dict:
    """Find duplicate normalized titles, slugs, barcodes and variant SKUs."""
    client = Addshop.from_env()
    try:
        return CatalogTools(AddshopAdmin(client)).duplicate_audit(selector, limit)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_catalog_taxonomy() -> dict:
    """Read category paths plus manufacturer/size/color dictionaries and detect taxonomy errors."""
    client = Addshop.from_env()
    try:
        return catalog_taxonomy(AddshopAdmin(client))
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_theme_catalog() -> dict:
    """Read available same-origin theme controls without selecting a theme."""
    client = Addshop.from_env()
    try:
        return AddshopThemes(client).catalog()
    finally:
        client.http.close()


@mcp.tool()
def addshop_set_theme(theme_id: int, expected_revision: str,
                      apply: bool = False) -> dict:
    """Preview/apply one discovered Addshop theme with revision and read-after-write verification."""
    if apply:
        require_write(kind='settings')
        _gate('set_theme', {'theme_id': theme_id})
    client = Addshop.from_env()
    try:
        result = AddshopThemes(client).select(theme_id, expected_revision, apply)
        return _audit('set_theme', audit_target('settings', fields=['theme_id']), result)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_product_blueprint(product_id: int) -> dict:
    """Create a reusable unpublished product specification without hidden fields or image downloads."""
    client = Addshop.from_env()
    try:
        return ProductInspection(AddshopAdmin(client)).blueprint(product_id)
    finally:
        client.http.close()


@mcp.tool()
def addshop_export_products_csv(selector: dict | None = None, limit: int = 500) -> dict:
    """Export selected product fields and revisions to a local UTF-8 CSV with SHA-256."""
    client = Addshop.from_env()
    try:
        return CatalogCsv(AddshopAdmin(client), os.getenv('ADDSHOP_STATE_DIR', 'state')).export(selector, limit)
    finally:
        client.http.close()


@mcp.tool()
def addshop_export_offers_csv(selector: dict | None = None, limit: int = 200) -> dict:
    """Export existing variant fields and per-product revisions to a local UTF-8 CSV."""
    client = Addshop.from_env()
    try:
        return OfferCsv(AddshopAdmin(client), os.getenv('ADDSHOP_STATE_DIR', 'state')).export(selector, limit)
    finally:
        client.http.close()


@mcp.tool()
def addshop_import_products_csv(csv_path: str, expected_plan_hash: str = '',
                                apply: bool = False, confirm_publish: bool = False) -> dict:
    """Preview/apply a strict revision-bound CSV update for up to 50 existing products."""
    if apply:
        require_write(kind='product')
        _gate('import_products_csv', {'file': Path(csv_path).name})
    client = Addshop.from_env()
    try:
        state_dir = os.getenv('ADDSHOP_STATE_DIR', 'state')
        result = CatalogCsv(AddshopAdmin(client, Path(state_dir) / 'admin'), state_dir).apply_import(
            csv_path, expected_plan_hash, confirm_publish, apply)
        return _audit('import_products_csv', audit_target('product', fields=['csv'],
                                                           count=result.get('changed_count')), result)
    finally:
        client.http.close()


@mcp.tool()
def addshop_import_offers_csv(csv_path: str, expected_plan_hash: str = '',
                              apply: bool = False, confirm_sensitive: bool = False) -> dict:
    """Preview/apply a strict revision-bound CSV update for up to 200 existing variants."""
    if apply:
        require_write(special='offer')
        _gate('import_offers_csv', {'file': Path(csv_path).name})
    client = Addshop.from_env()
    try:
        state_dir = os.getenv('ADDSHOP_STATE_DIR', 'state')
        result = OfferCsv(AddshopAdmin(client), state_dir).apply_import(
            csv_path, expected_plan_hash, apply, confirm_sensitive)
        return _audit('import_offers_csv', audit_target('product', fields=['offer.csv'],
                                                         count=result.get('changed_offer_count')), result)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_compare_products(left_product_id: int, right_product_id: int) -> dict:
    """Compare two products across card fields, default offer, properties and image count."""
    client = Addshop.from_env()
    try:
        return ProductInspection(AddshopAdmin(client)).compare(left_product_id, right_product_id)
    finally:
        client.http.close()


@mcp.tool()
def addshop_bulk_edit_products(selector: dict, transform: dict,
                               expected_revisions: dict | None = None,
                               apply: bool = False, confirm_publish: bool = False,
                               limit: int = 100) -> dict:
    """Preview/apply product price/content/category/SEO/publication transformations with rollback."""
    if apply:
        require_write(kind='product')
        _gate('bulk_edit_products', {'selector_keys': sorted(selector),
                                     'transform_keys': sorted(transform), 'limit': limit})
    client = Addshop.from_env()
    try:
        state_dir = Path(os.getenv('ADDSHOP_STATE_DIR', 'state'))
        result = CatalogTools(AddshopAdmin(client, state_dir / 'admin'),
                              state_dir / 'catalog').bulk_edit(
            selector, transform, apply, confirm_publish, expected_revisions, limit)
        return _audit('bulk_edit_products', audit_target('product', fields=transform.keys(),
                                                          count=result.get('matched')), result)
    finally:
        client.http.close()


@mcp.tool()
def addshop_bulk_edit_offer_prices(selector: dict, percent: float,
                                   expected_revisions: dict | None = None,
                                   apply: bool = False, confirm_large_change: bool = False,
                                   limit: int = 50) -> dict:
    """Preview/apply a percentage change to every variant price in selected products."""
    if apply:
        require_write(special='offer')
        _gate('bulk_edit_offer_prices', {'selector_keys': sorted(selector), 'limit': limit})
    client = Addshop.from_env()
    try:
        state_dir = Path(os.getenv('ADDSHOP_STATE_DIR', 'state'))
        result = CatalogTools(AddshopAdmin(client), state_dir / 'catalog').bulk_offer_prices(
            selector, percent, expected_revisions, apply, confirm_large_change, limit)
        return _audit('bulk_edit_offer_prices', audit_target('product', fields=['offer.price'],
                                                              count=result.get('matched_offers')), result)
    finally:
        client.http.close()


@mcp.tool()
def addshop_bulk_reprice_offers(selector: dict, operation: str, value: float,
                                rounding: str = 'none', min_price: float | None = None,
                                max_price: float | None = None,
                                expected_revisions: dict | None = None,
                                apply: bool = False, confirm_large_change: bool = False,
                                limit: int = 50) -> dict:
    """Preview/apply set/add/percent variant repricing with rounding, bounds and rollback."""
    if apply:
        require_write(special='offer')
        _gate('bulk_reprice_offers', {'selector_keys': sorted(selector),
                                      'operation': operation, 'limit': limit})
    client = Addshop.from_env()
    try:
        state_dir = Path(os.getenv('ADDSHOP_STATE_DIR', 'state'))
        result = CatalogTools(AddshopAdmin(client), state_dir / 'catalog').bulk_reprice_offers(
            selector, operation, value, rounding, min_price, max_price,
            expected_revisions, apply, confirm_large_change, limit)
        return _audit('bulk_reprice_offers', audit_target('product', fields=['offer.price'],
                                                           count=result.get('matched_offers')), result)
    finally:
        client.http.close()


@mcp.tool()
def addshop_bulk_update_offer_skus(selector: dict, template: str,
                                   expected_revisions: dict | None = None,
                                   apply: bool = False, confirm_overwrite: bool = False,
                                   limit: int = 50) -> dict:
    """Preview/apply template-generated variant SKUs with catalog collision checks and rollback."""
    if apply:
        require_write(special='offer')
        _gate('bulk_update_offer_skus', {'selector_keys': sorted(selector), 'limit': limit})
    client = Addshop.from_env()
    try:
        state_dir = Path(os.getenv('ADDSHOP_STATE_DIR', 'state'))
        result = CatalogTools(AddshopAdmin(client), state_dir / 'catalog').bulk_update_offer_skus(
            selector, template, expected_revisions, apply, confirm_overwrite, limit)
        return _audit('bulk_update_offer_skus', audit_target('product', fields=['offer.vendor_code'],
                                                              count=result.get('changed_offers')), result)
    finally:
        client.http.close()


@mcp.tool()
def addshop_batch_update_offers(operations: list[dict],
                                expected_revisions: dict | None = None,
                                apply: bool = False,
                                confirm_sensitive: bool = False) -> dict:
    """Preview/apply heterogeneous edits to existing offers with batch rollback."""
    if apply:
        require_write(special='offer')
        _gate('batch_update_offers', {'operation_count': len(operations)})
    client = Addshop.from_env()
    try:
        state_dir = Path(os.getenv('ADDSHOP_STATE_DIR', 'state'))
        result = OfferBatch(client, state_dir / 'offers').update(
            operations, expected_revisions, apply, confirm_sensitive)
        return _audit('batch_update_offers', audit_target('product', fields=['offers'],
                                                           count=result.get('changed_fields')), result)
    finally:
        client.http.close()


@mcp.tool()
def addshop_bulk_edit_offer_stock(selector: dict, operation: str, value: float,
                                  expected_revisions: dict | None = None,
                                  apply: bool = False, confirm_zero_stock: bool = False,
                                  limit: int = 50) -> dict:
    """Preview/apply set/add stock operations across all variants in selected products."""
    if apply:
        require_write(special='offer')
        _gate('bulk_edit_offer_stock', {'selector_keys': sorted(selector),
                                        'operation': operation, 'limit': limit})
    client = Addshop.from_env()
    try:
        state_dir = Path(os.getenv('ADDSHOP_STATE_DIR', 'state'))
        result = CatalogTools(AddshopAdmin(client), state_dir / 'catalog').bulk_offer_stock(
            selector, operation, value, expected_revisions, apply, confirm_zero_stock, limit)
        return _audit('bulk_edit_offer_stock', audit_target('product', fields=['offer.quantity'],
                                                             count=result.get('matched_offers')), result)
    finally:
        client.http.close()


@mcp.tool()
def addshop_bulk_update_existing_property(selector: dict, property_id: int, value: str,
                                          expected_revisions: dict | None = None,
                                          apply: bool = False, limit: int = 100) -> dict:
    """Preview/apply one value to an existing characteristic only, with revision checks and rollback."""
    if apply:
        require_write(special='component')
        _gate('bulk_update_existing_property', {'selector_keys': sorted(selector),
                                                 'property_id': property_id, 'limit': limit})
    client = Addshop.from_env()
    try:
        state_dir = Path(os.getenv('ADDSHOP_STATE_DIR', 'state'))
        result = CatalogTools(AddshopAdmin(client), state_dir / 'catalog').bulk_update_existing_property(
            selector, property_id, value, expected_revisions, apply, limit)
        return _audit('bulk_update_existing_property',
                      audit_target('product', fields=[f'property.{property_id}'],
                                   count=len(result.get('changes', []))), result)
    finally:
        client.http.close()


@mcp.tool()
def addshop_build_product(specification: dict, expected_plan_hash: str = '',
                          apply: bool = False, confirm_publish: bool = False) -> dict:
    """Preview/build a product with card fields, default offer, properties and images; publish last."""
    if apply:
        require_write(kind='product')
        _gate('build_product', {'sections': sorted(specification),
                                'image_count': len(specification.get('image_paths', [])),
                                'property_count': len(specification.get('properties', []))})
    client = Addshop.from_env()
    try:
        state_dir = Path(os.getenv('ADDSHOP_STATE_DIR', 'state'))
        result = ProductBuilder(AddshopAdmin(client, state_dir / 'admin'),
                                state_dir / 'product-builds').create(
            specification, expected_plan_hash, apply, confirm_publish)
        return _audit('build_product', audit_target('product', result.get('product_id'),
                                                     specification.get('fields', {}).keys()), result)
    finally:
        client.http.close()


@mcp.tool()
def addshop_seed_hidden_test_products(products: list[dict], image_path: str,
                                      expected_plan_hash: str = '', apply: bool = False,
                                      confirmation: str = '', workers: int = 4) -> dict:
    """Plan/seed up to 150 unpublished MCP SHOE products with one image and a checkpoint journal."""
    if apply:
        require_write(kind='product')
        require_write(special='media')
        _gate('seed_hidden_test_products', {'count': len(products), 'workers': workers})
    client = Addshop.from_env()
    try:
        state_dir = Path(os.getenv('ADDSHOP_STATE_DIR', 'state'))
        result = TestProductSeeder(AddshopAdmin(client, state_dir / 'admin'),
                                   state_dir / 'test-seeds').seed(
            products, image_path, expected_plan_hash, apply, confirmation, workers)
        return _audit('seed_hidden_test_products',
                      audit_target('product', fields=['test_seed'], count=result.get('created_count')), result)
    finally:
        client.http.close()


@mcp.tool()
def addshop_clone_product(source_product_id: int, field_overrides: dict,
                          offer_overrides: dict | None = None,
                          copy_properties: bool = True,
                          image_paths: list[str] | None = None,
                          publish: bool = False,
                          expected_source_revision: str = '', expected_plan_hash: str = '',
                          apply: bool = False, confirm_publish: bool = False) -> dict:
    """Preview/clone a product while dropping unique fields and creating it unpublished first."""
    if apply:
        require_write(kind='product')
        _gate('clone_product', {'source_product_id': source_product_id,
                                'field_override_keys': sorted(field_overrides),
                                'copy_properties': copy_properties,
                                'image_count': len(image_paths or [])})
    client = Addshop.from_env()
    try:
        state_dir = Path(os.getenv('ADDSHOP_STATE_DIR', 'state'))
        result = ProductCloner(AddshopAdmin(client, state_dir / 'admin'),
                               state_dir / 'product-builds').clone(
            source_product_id, field_overrides, offer_overrides, copy_properties,
            image_paths, publish, expected_source_revision, expected_plan_hash,
            apply, confirm_publish)
        return _audit('clone_product', audit_target('product', result.get('product_id'),
                                                     field_overrides.keys()), result)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_order_summary(order_id: int, include_personal_data: bool = False) -> dict:
    """Read an order with status/payment/delivery labels; personal data is redacted by default."""
    client = Addshop.from_env()
    try:
        return order_summary(AddshopAdmin(client), order_id, include_personal_data)
    finally:
        client.http.close()


@mcp.tool()
def addshop_update_entity(kind: str, changes: dict, expected_revision: str,
                          entity_id: int | None = None, apply: bool = False,
                          confirm_publish: bool = False) -> dict:
    """Preview/apply exact field changes with revision check and post-write verification."""
    active = changes.get('active') if isinstance(changes, dict) else None
    active_values = active if isinstance(active, list) else [active]
    if apply and any(str(value) == '1' for value in active_values) and not confirm_publish:
        raise AddshopError('Publishing requires confirm_publish=true after reviewing the preview.')
    validation = validate_product_changes(changes) if kind == 'product' else {'valid': True, 'warnings': []}
    if not validation['valid']:
        raise AddshopError(f'Invalid product changes: {validation["errors"]}')
    if apply:
        require_write(kind=kind)
        _gate('update_entity', {'kind': kind, 'entity_id': entity_id, 'fields': sorted(changes)})
    client = Addshop.from_env()
    try:
        state_dir = os.getenv('ADDSHOP_STATE_DIR', 'state')
        result = AddshopAdmin(client, Path(state_dir) / 'admin').update(
            kind, entity_id, changes, expected_revision, apply)
        result['validation_warnings'] = validation['warnings']
        return _audit('update_entity', audit_target(kind, entity_id, changes.keys()), result)
    finally:
        client.http.close()


@mcp.tool()
def addshop_set_product_visibility_batch(product_ids: list[int], publish: bool,
                                         expected_plan_hash: str = '', apply: bool = False,
                                         confirmation: str = '') -> dict:
    """Preview/apply resumable publication or hiding for an exact product-ID list."""
    if apply:
        require_write(kind='product')
        _gate('set_product_visibility_batch', {'count': len(product_ids), 'publish': publish})
    client = Addshop.from_env()
    try:
        state_dir = Path(os.getenv('ADDSHOP_STATE_DIR', 'state'))
        result = ProductVisibilityBatch(AddshopAdmin(client, state_dir / 'admin'),
                                        state_dir / 'visibility').run(
            product_ids, publish, expected_plan_hash, apply, confirmation)
        return _audit('set_product_visibility_batch',
                      audit_target('product', fields=['active'], count=len(product_ids)), result)
    finally:
        client.http.close()


@mcp.tool()
def addshop_build_product_batch(specifications: list[dict], expected_plan_hash: str = '',
                                apply: bool = False, confirmation: str = '') -> dict:
    """Preview/create up to 25 complete unpublished products with resumable partial results."""
    if apply:
        require_write(kind='product')
        _gate('build_product_batch', {'count': len(specifications)})
    client = Addshop.from_env()
    try:
        state_dir = Path(os.getenv('ADDSHOP_STATE_DIR', 'state'))
        result = ProductBatchBuilder(AddshopAdmin(client, state_dir / 'admin'),
                                     state_dir / 'product-batches').create(
            specifications, expected_plan_hash, apply, confirmation)
        return _audit('build_product_batch',
                      audit_target('product', fields=['card', 'offer', 'properties', 'images'],
                                   count=len(specifications)), result)
    finally:
        client.http.close()


@mcp.tool()
def addshop_delete_hidden_products(product_ids: list[int], expected_plan_hash: str = '',
                                   apply: bool = False, confirmation: str = '') -> dict:
    """Preview/delete exact hidden product IDs with revision guards; irreversible."""
    if apply:
        require_write(kind='product')
        _gate('delete_hidden_products', {'count': len(product_ids)})
    client = Addshop.from_env()
    try:
        state_dir = Path(os.getenv('ADDSHOP_STATE_DIR', 'state'))
        result = ProductDeletion(AddshopAdmin(client, state_dir / 'admin'),
                                 state_dir / 'product-deletions').delete(
            product_ids, expected_plan_hash, apply, confirmation)
        return _audit('delete_hidden_products',
                      audit_target('product', count=len(product_ids)), result)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_read_product_deletion_recovery(run_id: str) -> dict:
    """Verify and read a checksummed product blueprint package saved before deletion."""
    state_dir = Path(os.getenv('ADDSHOP_STATE_DIR', 'state'))
    return ProductDeletion(None, state_dir / 'product-deletions').read_recovery(run_id)


@mcp.tool()
def addshop_batch_update_product_properties(operations: list[dict],
                                            expected_plan_hash: str = '',
                                            apply: bool = False) -> dict:
    """Preview/apply heterogeneous existing-property values with rollback and journal."""
    if apply:
        require_write(special='component')
        _gate('batch_update_product_properties', {'count': len(operations)})
    client = Addshop.from_env()
    try:
        state_dir = Path(os.getenv('ADDSHOP_STATE_DIR', 'state'))
        result = ProductPropertyBatch(client, state_dir / 'property-batches').apply(
            operations, expected_plan_hash, apply)
        return _audit('batch_update_product_properties',
                      audit_target('product', fields=['properties'], count=len(operations)), result)
    finally:
        client.http.close()


@mcp.tool()
def addshop_batch_create_product_variants(operations: list[dict],
                                          expected_plan_hash: str = '',
                                          apply: bool = False,
                                          confirmation: str = '') -> dict:
    """Preview/create up to 100 exact size/color/SKU variants with partial-result journal."""
    if apply:
        require_write(special='offer')
        _gate('batch_create_product_variants', {'count': len(operations)})
    client = Addshop.from_env()
    try:
        state_dir = Path(os.getenv('ADDSHOP_STATE_DIR', 'state'))
        result = OfferCreateBatch(client, state_dir / 'offer-creates').create(
            operations, expected_plan_hash, apply, confirmation)
        return _audit('batch_create_product_variants',
                      audit_target('product', fields=['offers'], count=len(operations)), result)
    finally:
        client.http.close()


@mcp.tool()
def addshop_undo_entity_update(run_id: str, expected_current_revision: str,
                               apply: bool = False) -> dict:
    """Preview/undo only the fields changed by one generic update journal."""
    client = Addshop.from_env()
    try:
        state_dir = Path(os.getenv('ADDSHOP_STATE_DIR', 'state'))
        admin = AddshopAdmin(client, state_dir / 'admin')
        preview = admin.undo(run_id, expected_current_revision, False)
        if apply:
            require_write(kind=preview['kind'])
            _gate('undo_entity_update', {'kind': preview['kind'],
                                         'entity_id': preview['entity_id'], 'run_id': run_id})
        result = admin.undo(run_id, expected_current_revision, apply)
        return _audit('undo_entity_update', audit_target(preview['kind'], preview['entity_id'],
                                                         preview['restores_fields']), result)
    finally:
        client.http.close()


@mcp.tool()
def addshop_create_entity(kind: str, values: dict, apply: bool = False,
                          confirm_publish: bool = False) -> dict:
    """Preview/create a supported entity and verify exactly one new ID appeared."""
    active = values.get('active') if isinstance(values, dict) else None
    active_values = active if isinstance(active, list) else [active]
    if apply and any(str(value) == '1' for value in active_values) and not confirm_publish:
        raise AddshopError('Creating a published entity requires confirm_publish=true.')
    validation = validate_product_changes(values) if kind == 'product' else {'valid': True, 'warnings': []}
    if not validation['valid']:
        raise AddshopError(f'Invalid product values: {validation["errors"]}')
    if apply:
        require_write(kind=kind)
        _gate('create_entity', {'kind': kind, 'fields': sorted(values)})
    client = Addshop.from_env()
    try:
        result = AddshopAdmin(client).create(kind, values, apply)
        created_id = result.get('created', {}).get('id')
        result['validation_warnings'] = validation['warnings']
        return _audit('create_entity', audit_target(kind, created_id, values.keys()), result)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_read_offers(product_id: int) -> dict:
    """Read product variants/offers initialized by the Addshop JavaScript editor."""
    client = Addshop.from_env()
    try:
        return AddshopOffers(client).read(product_id)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_product_variant_matrix(product_id: int,
                                   desired_combinations: list[dict] | None = None) -> dict:
    """Inspect size/color variants, data issues and missing desired combinations without creating offers."""
    client = Addshop.from_env()
    try:
        return VariantMatrix(AddshopAdmin(client)).read(product_id, desired_combinations)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_read_product_images(product_id: int) -> dict:
    """Read product image metadata and return a concurrency revision."""
    client = Addshop.from_env()
    try:
        return AddshopMedia(client).images(product_id)
    finally:
        client.http.close()


@mcp.tool()
def addshop_set_product_image_color(product_id: int, image_id: int, color_id: int,
                                    expected_revision: str, apply: bool = False) -> dict:
    """Preview/assign a product image to a color and verify the updated image metadata."""
    if apply:
        require_write(special='media')
        _gate('set_product_image_color', {'product_id': product_id, 'image_id': image_id})
    client = Addshop.from_env()
    try:
        result = AddshopMedia(client).set_color(product_id, image_id, color_id,
                                                      expected_revision, apply)
        return _audit('set_product_image_color', audit_target('product', product_id,
                                                               ['image.color_id'], image_id=image_id), result)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_read_product_components(product_id: int) -> dict:
    """Read properties, videos, relations and special tags from the product editor state."""
    client = Addshop.from_env()
    try:
        return AddshopComponents(client).read(product_id)
    finally:
        client.http.close()


@mcp.tool()
def addshop_change_product_component(product_id: int, action: str, params: dict,
                                     expected_revision: str, apply: bool = False) -> dict:
    """Preview/apply one whitelisted property, video, relation or tag action."""
    client = Addshop.from_env()
    try:
        if apply:
            require_write(special='component')
            _gate('change_product_component', {'product_id': product_id, 'action': action})
        result = AddshopComponents(client).change(product_id, action, params, expected_revision, apply)
        return _audit('change_product_component', audit_target('product', product_id,
                                                                params.keys(), action=action), result)
    finally:
        client.http.close()


@mcp.tool()
def addshop_upload_product_image(product_id: int, file_path: str,
                                 expected_revision: str, apply: bool = False) -> dict:
    """Preview/upload one validated local image from ADDSHOP_UPLOAD_ROOT and verify a new image ID."""
    client = Addshop.from_env()
    try:
        if apply:
            require_write(special='media')
            _gate('upload_product_image', {'product_id': product_id})
        result = AddshopMedia(client).upload(product_id, file_path, expected_revision, apply)
        return _audit('upload_product_image', audit_target('product', product_id), result)
    finally:
        client.http.close()


@mcp.tool()
def addshop_batch_upload_product_images(product_id: int, file_paths: list[str],
                                        expected_plan_hash: str = '',
                                        apply: bool = False) -> dict:
    """Preview/upload 1-20 preflighted local images with a plan hash and partial-result journal."""
    if apply:
        require_write(special='media')
        _gate('batch_upload_product_images', {'product_id': product_id,
                                               'image_count': len(file_paths)})
    client = Addshop.from_env()
    try:
        state_dir = Path(os.getenv('ADDSHOP_STATE_DIR', 'state'))
        result = MediaBatch(client, state_dir / 'media-batches').upload(
            product_id, file_paths, expected_plan_hash, apply)
        return _audit('batch_upload_product_images',
                      audit_target('product', product_id, ['images'],
                                   count=result.get('uploaded_count', len(file_paths))), result)
    finally:
        client.http.close()


@mcp.tool()
def addshop_batch_set_product_image_colors(product_id: int, assignments: list[dict],
                                           expected_revision: str = '',
                                           apply: bool = False,
                                           confirm_clear: bool = False,
                                           expected_plan_hash: str = '') -> dict:
    """Preview/apply color links for 1-100 existing product images with rollback."""
    if apply:
        require_write(special='media')
        _gate('batch_set_product_image_colors', {'product_id': product_id,
                                                  'assignment_count': len(assignments)})
    client = Addshop.from_env()
    try:
        state_dir = Path(os.getenv('ADDSHOP_STATE_DIR', 'state'))
        result = MediaBatch(client, state_dir / 'media-batches').set_colors(
            product_id, assignments, expected_revision, apply, confirm_clear,
            expected_plan_hash)
        return _audit('batch_set_product_image_colors',
                      audit_target('product', product_id, ['image.color_id'],
                                   count=result.get('changed_images')), result)
    finally:
        client.http.close()


@mcp.tool()
def addshop_update_offer(product_id: int, offer_id: int, field: str, value: str,
                         expected_revision: str, apply: bool = False) -> dict:
    """Preview/apply one offer field with revision check and post-write verification."""
    client = Addshop.from_env()
    try:
        if apply:
            require_write(special='offer')
            _gate('update_offer', {'product_id': product_id, 'offer_id': offer_id, 'field': field})
        state_dir = os.getenv('ADDSHOP_STATE_DIR', 'state')
        result = AddshopOffers(client, Path(state_dir) / 'offers').update(
            product_id, offer_id, field, value, expected_revision, apply)
        return _audit('update_offer', audit_target('product', product_id, [field], offer_id=offer_id), result)
    finally:
        client.http.close()


@mcp.tool()
def addshop_add_offer(product_id: int, expected_revision: str, apply: bool = False) -> dict:
    """Preview/add exactly one blank product variant and return its generated offer ID."""
    if apply:
        require_write(special='offer')
        _gate('add_offer', {'product_id': product_id})
    client = Addshop.from_env()
    try:
        result = AddshopOffers(client).add(product_id, expected_revision, apply)
        return _audit('add_offer', audit_target('product', product_id, ['offers']), result)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_integration_status(name: str) -> dict:
    """Read one integration configuration with all secret values redacted."""
    client = Addshop.from_env()
    try:
        return AddshopIntegrations(client).read(name).public()
    finally:
        client.http.close()


@mcp.tool()
def addshop_integration_configure(name: str, changes: dict, expected_revision: str,
                                  apply: bool = False,
                                  confirm_external_code: bool = False) -> dict:
    """Preview/apply safe integration configuration; secrets are env:ADDSHOP_SECRET_* references."""
    if name == 'html_injection':
        scan = scan_custom_code(changes.get('head_html', ''), changes.get('body_html', ''))
        if apply and scan['high_risk'] and not confirm_external_code:
            raise AddshopError('High-risk custom code requires confirm_external_code=true after reviewing the scan.')
    client = Addshop.from_env()
    try:
        if apply:
            require_write(special='integration')
            _gate('configure_integration', {'name': name, 'fields': sorted(changes)})
        result = AddshopIntegrations(client).configure(name, changes, expected_revision, apply)
        if name == 'html_injection':
            result['security_scan'] = scan
        return _audit('configure_integration', audit_target('integration', fields=changes.keys(), name=name), result)
    finally:
        client.http.close()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_integration_catalog() -> dict:
    """Describe every discovered integration, its fields and whether it has external side effects."""
    return integration_catalog()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_scan_custom_code(head_html: str = '', body_html: str = '') -> dict:
    """Statically scan custom storefront HTML/JS/CSS before saving it."""
    return scan_custom_code(head_html, body_html)


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_capabilities() -> dict:
    """Return entity coverage and safety behavior implemented by this prototype."""
    return {
        'listable_kinds': sorted(ENTITY_REGISTRY),
        'readable_kinds': sorted([*ENTITY_REGISTRY, 'settings']),
        'updatable_kinds': sorted([*ENTITY_REGISTRY, 'settings']),
        'offers': AddshopOffers.capabilities(),
        'product_components': AddshopComponents.capabilities(),
        'integrations': integration_catalog(),
        'write_flow': ['read', 'revision_check', 'preview', 'lock', 'read_again', 'post', 'read', 'verify'],
        'dedicated_tools': ['addshop_read_product', 'addshop_test_roundtrip', 'addshop_recover_test'],
        'excluded': ['inline secret values', 'file upload in generic update', 'delete', 'reset shop',
                     'offer create/delete', 'automatic bulk import execution'],
        'active_policy': public_policy(),
    }


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_access_policy() -> dict:
    """Return the active deployment role and its allowed writes."""
    return public_policy()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_audit_log(limit: int = 100) -> dict:
    """Read recent metadata-only audit events and verify the hash chain."""
    return AuditLog(os.getenv('ADDSHOP_STATE_DIR', 'state')).read(limit)


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_write_safety_status() -> dict:
    """Return kill-switch, rate-limit and audit-chain health before a write."""
    return WriteGate(os.getenv('ADDSHOP_STATE_DIR', 'state')).status()


@mcp.tool(annotations={'readOnlyHint': True})
def addshop_local_doctor() -> dict:
    """Check local deployment, encrypted session, role and write gate without network access."""
    return local_doctor()


if __name__ == '__main__':
    main()
