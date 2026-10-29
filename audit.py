"""Read-only content/SEO audits composed from the generic entity layer."""
from addshop import AddshopError
from admin import AddshopAdmin

SEO_KINDS = {'product', 'category', 'page', 'article', 'news', 'manufacturer'}


def seo_audit(admin: AddshopAdmin, kind: str, limit: int = 100):
    if kind not in SEO_KINDS:
        raise AddshopError(f'SEO audit supports {sorted(SEO_KINDS)}.')
    if not 1 <= limit <= 500:
        raise AddshopError('Limit must be between 1 and 500.')
    listing = admin.list(kind)['items'][:limit]
    rows, errors = [], []
    seen_dir, seen_title = {}, {}
    for item in listing:
        try:
            state = admin.read(kind, item['id'])
            value = lambda name: state.values.get(name, [''])[0].strip()
            issues = []
            for field in ('title', 'seo_title', 'seo_description'):
                if field in state.values and not value(field):
                    issues.append(f'missing_{field}')
            if 'description' in state.values and len(value('description')) < 80:
                issues.append('thin_description')
            if 'dir' in state.values and not value('dir'):
                issues.append('missing_slug')
            row = {'id': item['id'], 'title': value('title'), 'slug': value('dir'),
                   'issues': issues, 'revision': state.revision}
            rows.append(row)
            if row['slug']:
                seen_dir.setdefault(row['slug'], []).append(item['id'])
            if row['title']:
                seen_title.setdefault(row['title'].casefold(), []).append(item['id'])
        except Exception as exc:
            errors.append({'id': item['id'], 'error': type(exc).__name__})
    duplicate_slugs = {k: v for k, v in seen_dir.items() if len(v) > 1}
    duplicate_titles = {k: v for k, v in seen_title.items() if len(v) > 1}
    return {'kind': kind, 'scanned': len(rows), 'with_issues': sum(bool(r['issues']) for r in rows),
            'items': rows, 'duplicate_slugs': duplicate_slugs,
            'duplicate_titles': duplicate_titles, 'errors': errors}
