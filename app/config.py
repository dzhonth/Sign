import os
from dotenv import load_dotenv
load_dotenv()

class Config:
    DATABASE = os.path.join(os.path.dirname(__file__), '..', 'sign.db')
    DOMAINS = {
        'free': os.getenv('FREE_DOMAIN', 'free.sign.space'),
        'paid': os.getenv('PAID_DOMAIN', 'sign.space')
    }
    LIMITS = {'free': 3}
    AIID_TYPES = {'free': 'DEMO', 'paid': 'PAID'}
    DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY')
    NOWPAYMENTS_API_KEY = os.getenv('NOWPAYMENTS_API_KEY')
    PRICE_USD = 50