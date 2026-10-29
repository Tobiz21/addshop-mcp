ADDSHOP MCP — production candidate 0.9.5, 2026-10-29

STATUS
Implemented a real stdio MCP server using the official Python SDK.
Live authenticated HTTP read/write/restore was verified on 2026-10-28 against
unpublished product 318018, "MCP TEST — HTTP prototype". The short description
was replaced with a unique marker, read back, restored, read again, and all
serialized non-secret form fields matched the pre-write snapshot.

The shop /login/ form is CUSTOMER login, not owner login. login_cli.py uses
https://addshop.ru/login/ (owner email/password), then the observed
/go_to_editor/ route. Addshop currently returns an HTTP redirect to the exact
shop origin; login_cli upgrades only that exact redirect to HTTPS.
Passwords and cookies must not be pasted into chat or committed to files.

INSTALL
Python 3.12 recommended.
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

MCP TOOLS
addshop_read_product(product_id): current fields + revision.
addshop_test_roundtrip(product_id, expected_revision, apply=false): preview;
apply=true writes a unique short_description marker, reads it back, restores
the original, reads again and compares all serialized non-secret form fields.
addshop_recover_test(run_id): reconcile an interrupted write using its journal.
addshop_list_entities(kind): list products, categories, pages, articles, news,
orders, customers, delivery/payment types, order statuses/sources, sizes, colors,
manufacturers and sliders.
addshop_get_entity(kind, entity_id): read an entity or main settings.
addshop_update_entity(..., apply=false): preview or apply exact field changes
with a revision check and read-after-write verification.
addshop_read_offers(product_id): read JavaScript-backed product variants.
addshop_update_offer(..., apply=false): preview/apply vendor code, price, size,
color, quantity or infinite-stock flag.
addshop_audit_store(): read-only coverage/health report for all supported areas.
addshop_capabilities(): machine-readable coverage and exclusions.
addshop_create_entity(kind, values, apply=false): preview/create a supported
entity and verify that exactly one new ID appeared.
addshop_seo_audit(kind, limit): audit missing SEO fields, thin descriptions,
duplicate titles and slugs.
addshop_read_product_images / addshop_upload_product_image: guarded media read
and upload from ADDSHOP_UPLOAD_ROOT with size/type/path checks.
addshop_batch_upload_product_images(): plan-hash-bound 1-20 image upload with SHA-256 preflight and recovery journal.
addshop_batch_set_product_image_colors(): validate and apply up to 100 image/color links with rollback.
addshop_integration_catalog(): integration contracts and side-effect classes.
addshop_integration_status(name): configuration with secrets redacted.
addshop_integration_configure(...): preview/apply safe configuration; secrets
are passed only as env:ADDSHOP_SECRET_* references, never inline.
addshop_contract_health(): GET-only verification of every discovered list/detail contract.
addshop_create_snapshot(): local redacted JSON backup before mass operations.
addshop_batch_update(): preview/apply up to 50 revision-checked changes with
best-effort reverse-order rollback if a later operation fails.
addshop_read_product_components / addshop_change_product_component: properties,
videos, related products and special tags from the JavaScript editor state.
addshop_describe_entity_form(): field types, required flags and current select
options, allowing the model to build valid changes without guessing IDs.
addshop_search(): cross-section search by title or exact numeric ID.
addshop_order_summary(): order fields with resolved status/payment/delivery labels;
customer name, contacts and address are redacted unless explicitly requested.
addshop_access_policy(): active role and exact permitted write areas.
addshop_audit_log(): metadata-only append log with a verifiable SHA-256 hash chain.
addshop_scan_custom_code(): inspect storefront HTML/JS/CSS for iframes, forms,
dynamic code execution, browser storage access and insecure external resources.
addshop_compare_snapshot(): verify SHA-256 and report new, missing and changed objects.
addshop_write_safety_status(): kill-switch, rate-limit and audit-chain status.
addshop_local_doctor(): local configuration, DPAPI session, role and state-dir checks.
addshop_select_products(): filter products by IDs/title/category/manufacturer/publication.
addshop_bulk_edit_products(): price/content/category/SEO/publication transforms and literal text replacement with rollback.
addshop_bulk_edit_offer_prices(): percentage edit of all selected variant prices.
addshop_bulk_reprice_offers(): set/add/percent variant prices with rounding, bounds and rollback.
addshop_bulk_update_offer_skus(): template-generated variant SKUs with catalog-wide collision checks.
addshop_batch_update_offers(): heterogeneous existing-variant edits with sensitive-change guard and rollback.
addshop_product_variant_matrix(): size/color matrix, anomaly audit and desired-combination planning.
addshop_product_quality_audit(): images, SEO, descriptions, stock, properties and duplicate SKUs.
addshop_build_product(): unpublished-first creation of card, offer, properties and images.
addshop_product_blueprint / addshop_compare_products: reusable safe specification and structured diff.
addshop_clone_product(): revision/hash-bound unpublished-first clone with explicit identity overrides.
addshop_export_products_csv / addshop_import_products_csv: strict revision-bound local bulk workflow.
addshop_export_offers_csv / addshop_import_offers_csv: strict existing-variant CSV workflow with rollback.
addshop_validate_product_changes(): conservative validation of slugs, prices, dimensions, IDs and SEO lengths.
addshop_add_offer(): add exactly one blank variant and return its generated ID.
addshop_set_product_image_color(): assign an image to a color with ownership/revision checks.
addshop_catalog_taxonomy(): category paths, reference dictionaries, orphan/cycle/duplicate checks.
addshop_bulk_edit_offer_stock(): revision-bound set/add stock changes with zero-stock confirmation.
addshop_inventory_replenishment_report(): finite-stock replenishment plan with thresholds and target levels.
addshop_product_property_matrix(): characteristic definitions, per-product values and distinct-value index.
addshop_bulk_update_existing_property(): revision-bound mass replacement of an already present characteristic.
addshop_duplicate_product_audit(): duplicate titles, slugs, barcodes and variant SKUs.
addshop_catalog_dashboard(): publication, price, stock, image and distribution metrics.
addshop_suggest_product_slug(): deterministic transliteration and live collision check.
addshop_change_journals(): value-free update history with run IDs and revisions.
addshop_undo_entity_update(): preview/restore only fields changed by one journaled update.

ENVIRONMENT FOR server.py
ADDSHOP_BASE_URL=https://new15676011817139.myaddshop.ru
ADDSHOP_COOKIE=<owner-authorized session cookie supplied securely at runtime>
ADDSHOP_SESSION_FILE=<preferred Windows DPAPI-encrypted session file>
ADDSHOP_SESSION_MAX_AGE_HOURS=168
ADDSHOP_TEST_PRODUCT_ID=<explicit test ID; omitted means writes disabled>
ADDSHOP_STATE_DIR=<absolute local directory for recovery journals>
ADDSHOP_UPLOAD_ROOT=<only local directory from which image upload is allowed>
ADDSHOP_SECRET_*=<integration credentials supplied outside the model context>
ADDSHOP_ROLE=reader|content|commerce|admin (default: admin)
ADDSHOP_ACTOR=<operator/service identity written to the audit log>
ADDSHOP_WRITES_ENABLED=0|1 (emergency write kill switch; default: 1)
ADDSHOP_MAX_WRITES_PER_MINUTE=<local circuit breaker; default: 20>
Run: python server.py
Transport: stdio. No network listener, no automatic installation into Codex.
Cookies are scoped to the selected shop; redirects are not followed by the adapter.

INTERACTIVE LIVE READ (password prompt, session only in memory)
python login_cli.py --product 296869

LIVE ROUNDTRIP (only after a dedicated unpublished test product exists)
python login_cli.py --product TEST_ID --roundtrip --state-dir PATH_TO_STATE
The product title must start with MCP TEST and publication must be disabled.
Product creation is available through the unpublished-first builder; product deletion is deliberately disabled.
The test restores the description; it does not delete a test product.

RECOVERY AND LIMITS
Before the first POST the original content is persisted in a journal.
No POST is automatically retried after timeout: GET reconciles the result.
Unexpected third-party edits to the description stop automatic restoration.
Fresh CSRF/hidden values, duplicate checkbox fields, selected options and other
form fields are retained. Journals omit cookies and hidden-only token fields.
Keep journals private: they contain product content.
Concurrent changes on the site cannot be made atomic without server support.
Do not edit the selected test product from another tab during the experiment.
State-file locks include PID and creation time. Audit/rate locks recover
automatically only when the owner PID is no longer alive and the lock is older
than five minutes. Admin write locks remain conservative: inspect the journal
before removing one manually. Never delete a journal until restoration is verified.
IMPORTANT ADDSHOP CONTRACT: GET requests to `/add/` and `/delete/` are
side-effecting. The create tool sends exactly one GET only after `apply=true`,
validates the new redirect ID, then configures that exact ID. Delete routes are
blocked everywhere and are never crawled, previewed or called by this MCP.
Publishing through create, update or batch requires a separate
`confirm_publish=true` argument after preview.
Images, external integrations and all server-side side effects are outside the
form equality check. Raw HTML forms may differ from
JavaScript-initialized browser forms: live testing is mandatory before production.
See COVERAGE.md for the complete support matrix and production roadmap.

LOCAL VALIDATION
Install pytest, then: python -m pytest . -q --basetemp ..\..\work\pytest-run
python smoke_mcp.py
python quality_gate.py
Tests use synthetic HTML/HTTP responses, not a live Addshop session.
The smoke test performs real MCP initialize and tools/list over stdio.
The quality gate runs tests, compilation, protocol validation, security-module
checks and documentation checks without contacting a live shop. See QUALITY.md.

FILES
addshop.py: HTTP adapter, parser, test guard and recovery journal.
admin.py: generic discovered form engine for 15 entity kinds and settings.
offers.py: JavaScript-backed offer reader/updater.
offer_batch.py: revision-bound heterogeneous bulk updates for existing offers.
variant_matrix.py: read-only size/color matrix and missing-combination planner.
inventory.py: read-only low-stock and replenishment planning.
integrations.py: explicit Ozon, Wildberries, Yandex Market, YML and HTML contracts.
media.py: guarded product image upload.
media_batch.py: preflighted multi-image uploads with partial-result journals.
audit.py: SEO/content audit.
operations.py: snapshots, contract health and guarded batch updates.
components.py: product properties, videos, relations and tags.
privacy.py / commerce.py: personal-data minimization and order summaries.
policy.py: deployment roles and write authorization.
auditlog.py: append-only hash-chained action journal without field values.
security.py: custom storefront code scanner.
safety.py: write kill switch, rate circuit breaker and audit integrity gate.
session_store.py / session_login_cli.py: Windows DPAPI session without password storage.
doctor.py: non-network deployment diagnostics.
catalog.py: product selection, bulk card/variant editing and catalog quality audit.
product_builder.py: plan-hash-bound multi-step product creation and recovery journal.
product_inspection.py: product blueprint and structured comparison.
product_clone.py: guarded blueprint-to-builder product cloning workflow.
catalog_csv.py: SHA-256 CSV export and plan-hash-bound update import.
offer_csv.py: revision-bound CSV export/import for existing variants.
product_validation.py: reusable validation for builder, bulk and CSV workflows.
taxonomy.py: category hierarchy and product reference dictionaries.
slug.py: deterministic unique product slug suggestions.
server.py: MCP tool registry.
login_cli.py: interactive owner login and read/test runner.
discover_cli.py: authenticated read-only contract discovery.
verify_admin_cli.py / verify_offers_cli.py: guarded live roundtrip checks.
discover_integrations_cli.py: authenticated GET-only integration discovery.
verify_production_cli.py: live contract health and local snapshot verification.
ACCEPTANCE.md: client acceptance scenarios and expected safety behavior.
test_addshop.py, test_admin.py, test_offers.py: synthetic contract tests.
smoke_mcp.py: MCP transport verification.
