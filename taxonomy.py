"""Category tree and product reference dictionaries."""
from admin import AddshopAdmin


def catalog_taxonomy(admin: AddshopAdmin):
    categories, errors = {}, []
    for item in admin.list('category')['items']:
        try:
            state = admin.read('category', item['id'])
            one = lambda name: state.values.get(name, [''])[0]
            categories[item['id']] = {'id': item['id'], 'title': one('title'),
                                      'parent_id': int(one('parent_id') or 0),
                                      'slug': one('dir')}
        except Exception as exc:
            errors.append({'kind': 'category', 'id': item['id'], 'error': type(exc).__name__})
    orphan_ids = sorted(item_id for item_id, item in categories.items()
                        if item['parent_id'] and item['parent_id'] not in categories)
    cycles = []
    for item_id in categories:
        trail, current = [], item_id
        while current and current in categories:
            if current in trail:
                cycle = trail[trail.index(current):]
                normalized = min(tuple(cycle[i:] + cycle[:i]) for i in range(len(cycle)))
                if list(normalized) not in cycles:
                    cycles.append(list(normalized))
                break
            trail.append(current)
            current = categories[current]['parent_id']
    def path_for(item_id):
        path, current, guard = [], item_id, set()
        while current in categories and current not in guard:
            guard.add(current)
            path.append(categories[current]['title'])
            current = categories[current]['parent_id']
        return ' / '.join(reversed(path))
    category_rows = [{**item, 'path': path_for(item_id)} for item_id, item in sorted(categories.items())]
    dictionaries = {}
    for kind in ('manufacturer', 'size', 'color'):
        try:
            dictionaries[kind] = admin.list(kind)['items']
        except Exception as exc:
            dictionaries[kind] = []
            errors.append({'kind': kind, 'error': type(exc).__name__})
    slugs = {}
    for item in category_rows:
        if item['slug']:
            slugs.setdefault(item['slug'], []).append(item['id'])
    return {'categories': category_rows, 'dictionaries': dictionaries,
            'orphan_category_ids': orphan_ids, 'category_cycles': cycles,
            'duplicate_category_slugs': {key: value for key, value in slugs.items() if len(value) > 1},
            'errors': errors}
