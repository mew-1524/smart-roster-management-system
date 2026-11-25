from django.conf import settings
from django.shortcuts import redirect


class LoginRequiredMiddleware:
    """All application pages require authentication (login page, admin and static files excepted)."""

    EXEMPT = ("/accounts/login/", "/admin/", "/static/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated and not request.path.startswith(self.EXEMPT):
            return redirect(f"{settings.LOGIN_URL}?next={request.path}")
        return self.get_response(request)
