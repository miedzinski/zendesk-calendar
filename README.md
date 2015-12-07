# Zendesk Calendar #

Synchronize tickets in Zendesk with Google Calendar events.

## Details ##

This application allows you to automatically insert events into Google Calendar whenever an agent gets ticket.
Currently editing event from calendar doesn't modify ticket.

Events' dates are taken from 4 custom ticket fields - start date, start end, end date, end time.
You need to add them manually. Because Zendesk lacks timepicker field, I've created small plugin that adds nice datetimepicker as iframe.
Please see [this GitHub repo](https://github.com/miedzinski/zendesk-calendar-app).

Additionally, you will need to add 2 triggers in Zendesk admin panel - for POST and PUT endpoints (ticket creation and ticket update).

If you need a better solution, check [Cronofy](https://zendesk.cronofy.com).

## Requirements ##

Python 3 (tested on 3.4.3) and Redis.

## Installation ##

    $ git clone https://github.com/miedzinski/zendesk-calendar.git
    $ cd zendesk-calendar
    $ mkvirtualenv zendesk-calendar
    $ pip install -r requirements.txt
    
Edit `settings.py` and make sure you have Redis server up.

    $ python run.py # in production use gunicorn or alternative
    $ celery -A zendesk.tasks:celery worker

## License ##

This code is available under [The MIT License](https://opensource.org/licenses/MIT), see the `LICENSE` file for more information.
