"""Validate local multicart declarations before touching the VM."""
from pathlib import Path
import re

MAX_CARTS=64
NATIVE_NAME=re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]*\.p8\Z',re.I)


def native_name(source,name=None):
    if name is None:
        name=Path(source).name
        if name.lower().endswith('.p8.png'):name=name[:-4]
        elif name.lower().endswith('.lua'):name=name[:-4]+'.p8'
    if not isinstance(name,str) or len(name)>64 or not NATIVE_NAME.fullmatch(name):
        raise ValueError('Native cart names must be flat ASCII filenames ending in .p8, at most 64 characters; use an explicit name for other source filenames.')
    return name.lower()


def bundle_specs(entry,carts=None,entry_name=None):
    if not isinstance(entry,str) or not entry or '\0' in entry:
        raise ValueError('entry must be a cartridge path.')
    carts=[] if carts is None else carts
    if not isinstance(carts,list) or len(carts)>=MAX_CARTS:
        raise ValueError('carts must be a list of at most 63 dependencies (64 carts including entry).')
    result=[];names=set()
    for item,is_entry in [(item,False) for item in carts]+[({'cart':entry,'name':entry_name},True)]:
        if isinstance(item,str):item={'cart':item}
        if not isinstance(item,dict) or set(item)-{'cart','name'} or 'cart' not in item:
            raise ValueError('Each dependency must be a path or an object with cart and optional name.')
        source=item['cart']
        if not isinstance(source,str) or not source or '\0' in source:
            raise ValueError('Each cart path must be a nonempty string.')
        if not source.lower().endswith(('.p8','.lua','.p8.png')):
            raise ValueError('Bundle inputs must be .p8, .lua or .p8.png carts.')
        if 'name' in item and item['name'] is None and not is_entry:
            raise ValueError('An explicit cart name must be a string.')
        name=native_name(source,item.get('name'))
        if name in names:raise ValueError('Duplicate native cartridge name: '+name)
        names.add(name)
        result.append(dict(cart=source,name=name,entry=is_entry))
    return result
