"""Deterministic Russian/Latin product slug suggestions with live collision checks."""
import re
from admin import AddshopAdmin

MAP = str.maketrans({
    'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'e','ж':'zh','з':'z','и':'i','й':'y',
    'к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f',
    'х':'h','ц':'ts','ч':'ch','ш':'sh','щ':'sch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya'
})


def slugify(value):
    text = str(value).strip().casefold().translate(MAP)
    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', text)).strip('-')[:220] or 'product'


def suggest_product_slug(admin: AddshopAdmin, title, exclude_product_id=None):
    base = slugify(title)
    used = {}
    for item in admin.list('product')['items']:
        if exclude_product_id is not None and item['id'] == exclude_product_id:
            continue
        state = admin.read('product', item['id'])
        current = state.values.get('dir', [''])[0]
        if current:
            used[current] = item['id']
    candidate, counter = base, 2
    while candidate in used:
        suffix = f'-{counter}'
        candidate = base[:220-len(suffix)] + suffix
        counter += 1
    return {'title': title, 'base_slug': base, 'suggested_slug': candidate,
            'collision_count': counter - 2, 'existing_id_for_base': used.get(base)}
