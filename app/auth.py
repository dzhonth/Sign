from app.models import get_user, create_user, increment_aiid_count
from app.config import Config

def get_or_create_user(device_id):
    user = get_user(device_id)
    if not user:
        user = create_user(device_id)
    return user

def can_create_free(user):
    if user['type'] == 'paid':
        return True, "paid"
    if user['aiid_count'] < Config.LIMITS['free']:
        return True, "free"
    return False, "limit_exceeded"

def consume_free_slot(user):
    if user['type'] == 'free':
        increment_aiid_count(user['id'])