"""Build the four carts from miku.lua / miku_a.lua / miku_b.lua / miku_c.lua."""
from build_lua import build
if __name__ == '__main__':
    result=build()
    if result['cached']:print('Carts are up to date.')
    print(result['output'])
