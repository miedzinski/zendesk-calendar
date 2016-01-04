from celery import Celery
from flask import Flask
from redis import StrictRedis
from zdesk import Zendesk


app = Flask(__name__)
app.config.from_pyfile('settings.py')

celery = Celery(app.import_name, broker=app.config['CELERY_BROKER_URL'])
celery.conf.update(app.config)
TaskBase = celery.Task


class ContextTask(TaskBase):
    abstract = True

    def __call__(self, *args, **kwargs):
        with app.app_context():
            return TaskBase.__call__(self, *args, **kwargs)


celery.Task = ContextTask

redis = StrictRedis(host=app.config['REDIS_HOST'],
                    port=app.config['REDIS_PORT'],
                    db=app.config['REDIS_DB'])

zendesk = Zendesk(zdesk_url=app.config['ZENDESK_URL'],
                  zdesk_email=app.config['ZENDESK_EMAIL'],
                  zdesk_password=app.config['ZENDESK_TOKEN'],
                  zdesk_token=True)

from . import api  # NOQA
