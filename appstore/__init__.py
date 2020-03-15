try:
    import google.auth.exceptions
    try:
        import googleclouddebugger
        googleclouddebugger.enable(module='appstore-api')
    except (ImportError, google.auth.exceptions.DefaultCredentialsError):
        print("Couldn't start cloud debugger")
except ImportError:
    print("Couldn't import google exceptions")

from flask import Flask, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .settings import config

from .honeycomb import init_app as init_honeycomb
from .models import init_app as init_models
from .api import init_app as init_api
from .dev_portal_api import init_app as init_dev_portal_api
from .commands import init_app as init_commands
from .utils import init_app as init_utils
from .locker import locker

app = Flask(__name__)
app.config.update(**config)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
init_honeycomb(app)
init_models(app)
init_utils(app)
init_api(app)
init_dev_portal_api(app)
init_commands(app)

@app.route('/heartbeat')
def heartbeat():
    return 'ok'

@app.route('/dummy', methods=['GET', 'POST'])
def dummy():
    return jsonify({})
