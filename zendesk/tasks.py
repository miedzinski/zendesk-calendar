import datetime
import time
import urllib.parse
import uuid

from flask_restful import url_for
from googleapiclient.errors import HttpError
from dateutil.parser import parse as parse_date

from . import app, celery, redis, zendesk
from .helpers import (build_service_from_id, decode_dict,
                      fields_to_dict, friendly_to_tz)


celery.conf.update(
    CELERYBEAT_SCHEDULE={
        'renew-channels': {
            'task': 'zendesk.tasks.renew_channels',
            'schedule': datetime.timedelta(minutes=1),
        }
    },
)


def insert_event(profile_id, event, ticket_id=None):
    """
    Inserts an event to Google Calendar associated with given profile.
    If given ticket_id, tries to update an existing event.
    """
    service = build_service_from_id(profile_id)
    old_profile_id = None
    event_id = None

    if ticket_id:
        key = 'ticket:%s' % ticket_id
        ticket_data = decode_dict(redis.hgetall(key))
        old_profile_id = ticket_data.get('profile_id', 0)
        event_id = ticket_data.get('event_id')

    if old_profile_id is not None and old_profile_id != profile_id:
        try:
            build_service_from_id(old_profile_id).events().delete(
                calendarId='primary',
                eventId=event_id,
                sendNotifications=False,
            ).execute()
        except HttpError:
            pass
        redis.delete('event:%s' % event_id)
        event_id = None

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

    start_date = parse_date(custom_fields[field_ids['start_date']]).date()
    start_time = parse_date(custom_fields[field_ids['start_time']]).time()
    end_date = parse_date(custom_fields[field_ids['end_date']]).date()
    end_time = parse_date(custom_fields[field_ids['end_time']]).time()

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

    redis.hmset('ticket:%s' % ticket_id, {
        'event_id': event_id.encode(),
        'profile_id': str(assignee_id).encode(),
    })
    redis.set('event:%s' % event_id, str(ticket_id).encode())

    return event


def remove_channel(profile_id):
    service = build_service_from_id(profile_id)
    key = 'notifications:%s' % profile_id
    channel = decode_dict(redis.hgetall(key))

    if channel:
        try:
            service.channels().stop(body=channel).execute()
        except HttpError as e:
            if e.resp.status != 404:
                raise


@celery.task
def setup_channel(profile_id):
    service = build_service_from_id(profile_id)

    from .api import CalendarEvent

    body = {
        'id': uuid.uuid4().hex,
        'token': app.config['API_TOKEN'],
        'type': 'web_hook',
        'address': url_for(CalendarEvent.endpoint,
                           profile_id=profile_id,
                           _external=True,
                           _scheme='https'),
        'params': {
            'ttl': '2592000'  # 30 days, maximum allowed
        }
    }

    res = service.events().watch(calendarId='primary', body=body).execute()

    expiration = int(res['expiration']) // 1000
    redis.zadd('schedule', expiration, profile_id)

    return res


@celery.task
def save_channel(profile_id, channel):
    remove_channel(profile_id)
    redis.hmset('notifications:%s' % profile_id, channel)

    return channel


@celery.task
def renew_channels():
    now = int(time.time())
    profile_ids = [int(x) for x in redis.zrangebyscore('schedule', 0, now)]

    for profile_id in profile_ids:
        setup_channel.delay(profile_id)

    return profile_ids


@celery.task
def sync_page(events):
    """
    Updates up to 100 tickets from given events.
    """
    field_ids = app.config['ZENDESK_FIELD_IDS']
    tickets = {}

    for event in events:
        try:
            ticket_id = redis.get('event:%s' % event['id']).decode()
        except AttributeError:
            # no value for given key, code fails on .decode()
            continue

        start_datetime = parse_date(event['start']['dateTime'])
        start_date = start_datetime.strftime('%Y-%m-%d')
        start_time = start_datetime.strftime('%H:%M')

        end_datetime = parse_date(event['end']['dateTime'])
        end_date = end_datetime.strftime('%Y-%m-%d')
        end_time = end_datetime.strftime('%H:%M')

        ticket = {
            'id': ticket_id,
            'custom_fields': [
                {'id': field_ids['start_date'], 'value': start_date},
                {'id': field_ids['start_time'], 'value': start_time},
                {'id': field_ids['end_date'], 'value': end_date},
                {'id': field_ids['end_time'], 'value': end_time}
            ]
        }

        tickets[ticket_id] = ticket

    if tickets:
        zendesk.tickets_update_many({'tickets': list(tickets.values())})

    return tickets


@celery.task
def make_sync(profile_id):
    service = build_service_from_id(profile_id)

    try:
        sync_token = redis.get('sync:%s' % profile_id).decode()
    except AttributeError:
        # no value for given key, code fails on .decode()
        sync_token = None

    page_token = None
    while True:
        try:
            events = service.events().list(calendarId='primary',
                                           pageToken=page_token,
                                           syncToken=sync_token).execute()
        except HttpError as e:
            if e.resp.status != 410:
                raise
            # sync token invalidated, full sync required
            sync_token = None
            redis.delete('sync:%s' % profile_id)
            continue

        batch_size = 100
        batch_len, rem = divmod(len(events['items']), batch_size)
        if rem > 0:
            batch_len += 1

        for i in range(batch_len):
            offset = i * batch_size
            sync_page.delay(events['items'][offset:offset + batch_size])

        page_token = events.get('nextPageToken')
        if not page_token:
            sync_token = events.get('nextSyncToken')
            break

    redis.set('sync:%s' % profile_id, sync_token.encode())
