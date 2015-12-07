import pickle
import types

from flask import redirect, request, session, url_for
from flask_restful import Resource, Api, abort
from oauth2client import client

from . import app, redis
from .helpers import api_route, login_required, RedisStorage
from .tasks import fetch_ticket


api = Api(app)
api.route = types.MethodType(api_route, api)


@app.errorhandler(client.HttpAccessTokenRefreshError)
def catch_invalid_refresh_token(e):
    abort(401)


@app.route('/google_login/<int:profile_id>')
def google_login(profile_id):
    flow = client.OAuth2WebServerFlow(client_id=app.config['GOOGLE_CLIENT_ID'],
                                      client_secret=app.config['GOOGLE_CLIENT_SECRET'],
                                      scope=app.config['GOOGLE_REQUEST_SCOPE'],
                                      access_type='offline',
                                      approval_prompt='force',
                                      redirect_uri=url_for('google_login_callback',
                                                           _external=True))
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

    return redirect(app.config['ZENDESK_URL'])


@api.route('/ticket/<int:ticket_id>/')
class ZendeskTicket(Resource):
    @login_required
    def post(self, ticket_id):
        fetch_ticket.delay(ticket_id)

    @login_required
    def put(self, ticket_id):
        fetch_ticket.delay(ticket_id, overwrite=True)
