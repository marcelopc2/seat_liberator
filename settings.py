from decouple import config

DEBUG = config("DEBUG", default=False, cast=bool)
DATABASE_URL = config("DATABASE_URL", default=None)
SECRET_KEY = config("SECRET_KEY", default=None)
BASE_URL = config("BASE_URL")
API_TOKEN = config("API_TOKEN")