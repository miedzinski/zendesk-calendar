import pickle
import types

from flask import redirect, request, session, url_for
from flask_restful import Resource, Api, abort
from oauth2client import client

from . import app, redis
from .helpers import api_route, login_required, RedisStorage
from .tasks import fetch_ticket, make_sync, save_channel, setup_channel


api = Api(app)
api.route = types.MethodType(api_route, api)


@app.route('/google_login/<int:profile_id>')
def google_login(profile_id):
    flow = client.OAuth2WebServerFlow(client_id=app.config['GOOGLE_CLIENT_ID'],
                                      client_secret=app.config['GOOGLE_CLIENT_SECRET'],
                                      scope=app.config['GOOGLE_REQUEST_SCOPE'],
                                      access_type='offline',
                                      approval_prompt='force',
                                      redirect_uri=url_for('google_login_callback',
                                                           _external=True,
                                                           _scheme='https'))
    authorize_url = flow.step1_get_authorize_url()

    session['flow'] = pickle.dumps(flow)
    session['profile_id'] = profile_id

    return redirect(authorize_url)


@app.route('/google_login_callback')
def google_login_callback():
    code = request.args.get('code')
    flow = session.get('flow')
    profile_id = session.get('profile_id')

    if not all((code, flow, profile_id)):
        abort(400)

    flow = pickle.loads(flow)

    credentials = flow.step2_exchange(code)

    store = RedisStorage(redis, profile_id)
    store.put(credentials)

    setup_channel.delay(profile_id)

    return redirect(app.config['ZENDESK_URL'])


@api.route('/ticket/<int:ticket_id>/')
class ZendeskTicket(Resource):
    @login_required
    def post(self, ticket_id):
        fetch_ticket.delay(ticket_id)

        return '', 202

    @login_required
    def put(self, ticket_id):
        fetch_ticket.delay(ticket_id, overwrite=True)

        return '', 202


@api.route('/notifications/<int:profile_id>/')
class CalendarEvent(Resource):
    @login_required
    def post(self, profile_id):
        state = request.headers.get('X-Goog-Resource-State')
        if state == 'sync':
            channel = {
                'id': request.headers.get('X-Goog-Channel-ID'),
                'resourceId': request.headers.get('X-Goog-Resource-ID')
            }
            save_channel.delay(profile_id, channel)
        elif state == 'exists':
            make_sync.delay(profile_id)
        else:
            abort(501)

        return '', 202
