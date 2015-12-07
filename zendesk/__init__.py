from flask import Flask
from redis import StrictRedis


app = Flask(__name__)
app.config.from_pyfile('settings.py')

redis = StrictRedis(host=app.config['REDIS_HOST'],
                    port=app.config['REDIS_PORT'],
                    db=app.config['REDIS_DB'])

from . import api
