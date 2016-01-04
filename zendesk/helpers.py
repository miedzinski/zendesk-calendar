import functools
import pickle

from apiclient import discovery
from flask import request
from httplib2 import Http
from oauth2client.client import Storage

from . import app, redis
from .timezone import TZ_MAPPING


def login_required(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        token = (
            request.form.get('token') or                 # Zendesk Triggers
            request.headers.get('X-Goog-Channel-Token')  # Google Notifications
        )
        if token != app.config['API_TOKEN']:
            return {'error': 'Invalid API token.'}, 401
        return f(*args, **kwargs)
    return wrapper


def api_route(self, *args, **kwargs):
    def wrapper(cls):
        self.add_resource(cls, *args, **kwargs)
        return cls
    return wrapper


class CredentialsNotFoundError(Exception):
    """ Error trying to retrieve credentials from storage. """


class RedisStorage(Storage):
    def __init__(self, instance, key, prefix='oauth2:'):
        self.instance = instance
        self.key = key
        self.prefix = prefix

    def locked_get(self):
        key = '%s%s' % (self.prefix, self.key)
        credentials = self.instance.get(key)
        try:
            return pickle.loads(credentials)
        except TypeError:
            raise CredentialsNotFoundError

    def locked_put(self, credentials):
        key = '%s%s' % (self.prefix, self.key)
        self.instance.set(key, pickle.dumps(credentials))

    def locked_delete(self):
        key = '%s%s' % (self.prefix, self.key)
        self.instance.delete(key)


def build_service_from_id(profile_id):
    store = RedisStorage(redis, profile_id)
    credentials = store.get()
    http = credentials.authorize(Http())
    service = discovery.build('calendar', 'v3', http=http)

    return service


def fields_to_dict(data):
    """
    Takes list of structure [{'id': someid, 'value': somevalue}, ...].
    Returns {someid: someval, anotherid: anotherval}.
    """
    return {el['id']: el['value'] for el in data}


def friendly_to_tz(friendly):
    """
    See mapping:
    http://api.rubyonrails.org/classes/ActiveSupport/TimeZone.html
    """
    return TZ_MAPPING.get(friendly, 'Etc/UTC')


def decode_dict(dict):
    return {k.decode(): v.decode() for k, v in dict.items()}
