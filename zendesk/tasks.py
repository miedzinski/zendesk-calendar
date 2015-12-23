import datetime
import urllib.parse

from celery import Celery
from zdesk import Zendesk
from dateutil.parser import parse

from . import app, redis
from .helpers import build_service_from_id, fields_to_dict, friendly_to_tz


celery = Celery(app.import_name, broker=app.config['CELERY_BROKER_URL'])
celery.conf.update(app.config)
TaskBase = celery.Task


class ContextTask(TaskBase):
    abstract = True

    def __call__(self, *args, **kwargs):
        with app.app_context():
            return TaskBase.__call__(self, *args, **kwargs)

celery.Task = ContextTask

zendesk = Zendesk(zdesk_url=app.config['ZENDESK_URL'],
                  zdesk_email=app.config['ZENDESK_EMAIL'],
                  zdesk_password=app.config['ZENDESK_TOKEN'],
                  zdesk_token=True)


def insert_event(profile_id, event, ticket_id=None):
    """
    Inserts an event to Google Calendar associated with given profile.
    If given ticket_id, tries to update an existing event.
    """
    service = build_service_from_id(profile_id)
    event_id = None

    if ticket_id:
        try:
            event_id = redis.get('ticket_%s' % ticket_id).decode()
        except AttributeError:
            # no value for given key, code fails on .decode()
            pass

    if event_id:
        res = service.events().patch(calendarId='primary',
                                     eventId=event_id,
                                     body=event).execute()
    else:
        res = service.events().insert(calendarId='primary',
                                      body=event).execute()

    return res.get('id')


@celery.task
def fetch_ticket(ticket_id, overwrite=False):
    ticket = zendesk.ticket_show(id=ticket_id)['ticket']

    assignee_id = ticket['assignee_id']

    summary = ticket['subject']
    description = ticket['description']
    url = urllib.parse.urljoin(app.config['ZENDESK_URL'],
                               'tickets/%d/' % ticket_id)

    field_ids = app.config['ZENDESK_FIELD_IDS']
    custom_fields = fields_to_dict(ticket['custom_fields'])

    start_date = parse(custom_fields[field_ids['start_date']]).date()
    start_time = parse(custom_fields[field_ids['start_time']]).time()
    end_date = parse(custom_fields[field_ids['end_date']]).date()
    end_time = parse(custom_fields[field_ids['end_time']]).time()

    start = datetime.datetime.combine(start_date, start_time)
    end = datetime.datetime.combine(end_date, end_time)

    assignee = zendesk.user_show(id=assignee_id)['user']
    timezone = friendly_to_tz(assignee.get('time_zone'))

    event = {
        'summary': summary,
        'description': description,
        'start': {
            'dateTime': start.isoformat(),
            'timeZone': timezone
        },
        'end': {
            'dateTime': end.isoformat(),
            'timeZone': timezone
        },
        'source': {
            'title': ticket_id,
            'url': url
        }
    }

    if not overwrite:
        event_id = insert_event(assignee_id, event)
    else:
        event_id = insert_event(assignee_id, event, ticket_id)

    if event_id:
        redis.set('ticket_%s' % ticket_id, event_id.encode())
