def require_admin(request):
    if not request.headers.get("X-Admin"):
        raise PermissionError("admin required")
