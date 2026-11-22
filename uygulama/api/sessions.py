import logging
import secrets

from django.conf import settings

from .redis_client import get_redis_client


logger = logging.getLogger('api.sessions')
SESSION_COOKIE = 'oturum'
SESSION_TTL_SECONDS = 30 * 60


def session_key(token):
    return f'oturum:{token}'


def start_redis_session(request, user_id):
    token = secrets.token_urlsafe(32)
    get_redis_client().setex(session_key(token), SESSION_TTL_SECONDS, str(user_id))
    request.user_id = user_id
    request.redis_session_token = token
    request.new_redis_session = True
    request.clear_redis_session_cookie = False
    logger.info('Oturum Redis\'e yazıldı; sona erme 30 dakika sonra.')


def end_redis_session(request):
    token = getattr(request, 'redis_session_token', None)
    if token:
        get_redis_client().delete(session_key(token))
        logger.info('Oturum Redis\'ten silindi.')
    request.user_id = None
    request.redis_session_token = None
    request.clear_redis_session_cookie = True


class RedisSessionMiddleware:
    """Opaque oturum anahtarını Redis'te 30 dakikalık kayan TTL ile saklar."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = request.COOKIES.get(SESSION_COOKIE)
        request.user_id = None
        request.redis_session_token = None
        request.new_redis_session = False
        request.clear_redis_session_cookie = False

        if token:
            key = session_key(token)
            user_id = get_redis_client().get(key)
            if user_id is None:
                request.clear_redis_session_cookie = True
            else:
                request.user_id = int(user_id)
                request.redis_session_token = token
                get_redis_client().expire(key, SESSION_TTL_SECONDS)
                logger.info('Oturum Redis üzerinden bulundu ve 30 dakika uzatıldı.')

        response = self.get_response(request)

        active_token = getattr(request, 'redis_session_token', None)
        if active_token:
            response.set_cookie(
                SESSION_COOKIE,
                active_token,
                max_age=SESSION_TTL_SECONDS,
                httponly=True,
                samesite='Lax',
            )
        elif getattr(request, 'new_redis_session', False):
            response.set_cookie(
                SESSION_COOKIE,
                request.redis_session_token,
                max_age=SESSION_TTL_SECONDS,
                httponly=True,
                samesite='Lax',
            )
        elif getattr(request, 'clear_redis_session_cookie', False):
            response.delete_cookie(SESSION_COOKIE, samesite='Lax')

        return response
