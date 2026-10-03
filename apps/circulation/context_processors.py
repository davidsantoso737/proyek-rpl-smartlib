from .services import CART_SESSION_KEY


def cart_item_count(request):
    """Jumlah item keranjang peminjaman untuk badge navigasi."""
    try:
        items = request.session.get(CART_SESSION_KEY, [])
        return {"cart_item_count": len(items)}
    except Exception:
        return {"cart_item_count": 0}
