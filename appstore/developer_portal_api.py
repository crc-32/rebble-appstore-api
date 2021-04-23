import json
import traceback
import datetime
# import tempfile

from algoliasearch import algoliasearch
from flask import Blueprint, jsonify, abort, request
from flask_cors import CORS
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound

from .utils import authed_request, get_uid, id_generator, validate_new_app_fields, is_valid_category, is_valid_appinfo
from .models import Category, db, App, Developer, Release, CompanionApp, Binary, AssetCollection, LockerEntry, UserLike
from .pbw_in_memory import PBW, release_from_pbw
from .s3 import upload_pbw_from_memory, upload_asset_from_memory
from .settings import config


parent_app = None
devportal_api = Blueprint('devportal_api', __name__)
CORS(devportal_api)

category_map = {
                'Notifications': '5261a8fb3b773043d5000001',
                'Health & Fitness': '5261a8fb3b773043d5000004',
                'Remotes': '5261a8fb3b773043d5000008',
                'Daily': '5261a8fb3b773043d500000c',
                'Tools & Utilities': '5261a8fb3b773043d500000f',
                'Games': '5261a8fb3b773043d5000012',
                'Index': '527509e36526cda2d4000019',
                'Faces': '528d3ef2dc7b5f580700000a',
                'GetSomeApps': '52ccee3151a80d28e100003e',
            }

# Do we need this?
if config['ALGOLIA_ADMIN_API_KEY']:
    algolia_client = algoliasearch.Client(config['ALGOLIA_APP_ID'], config['ALGOLIA_ADMIN_API_KEY'])
    algolia_index = algolia_client.init_index(config['ALGOLIA_INDEX'])
else:
    algolia_index = None

@devportal_api.route('/onboard', methods=['POST'])
def create_developer():
        try:
            req = request.json
        except Exception as e:
            print(e)
            return jsonify(error = "Invalid POST body. Expected JSON", e = "body.invalid"), 400

        if req is None:
            return jsonify(error = "Invalid POST body. Expected JSON", e = "body.invalid"), 400

        # Get our developer ID as it exists in the user table
        # This also checks we are authed
        result = authed_request('GET', f"{config['REBBLE_AUTH_URL']}/api/v1/me/pebble/appstore")
        if result.status_code != 200:
            abort(401)
        me = result.json()

        if not "name" in req:
            return jsonify(error = "Missing required field: name", e = "missing.field.name"), 400
        
        try:
            dev = Developer.query.filter_by(id=me['id']).one()
            return jsonify(success = True, message = "User is already onboard")
        except NoResultFound:
            dev = Developer(id=me['id'], name=req['name'])
            db.session.add(dev)
            db.session.commit()

        return jsonify(success = True, id = me['id'], message = "Onboarded user")

@devportal_api.route('/submit', methods=['POST'])
def submit_new_app():
    try:

        # Validate all fields
        requestOK = validate_new_app_fields(request)

        if requestOK[0] == True:

            # Required fields are there, let's upload

            params = dict(request.form)

            screenshots = {
                "aplite": [],
                "basalt": [],
                "chalk": [],
                "diorite": []
            }

            # Create temp dir to put files in
            # working_dir_path = tempfile.mkdtemp()

            pbw_file = request.files['pbw'].read()
            pbw = PBW(pbw_file, 'aplite')

            try:
                with pbw.zip.open('appinfo.json') as f:
                    appinfo = json.load(f)
            except Exception as e:
                return jsonify(error = f"Your pbw file is invalid or corrupted", e = "invalid.pbw"), 400


            appinfo_valid = is_valid_appinfo(appinfo)
            if not appinfo_valid[0]:
                return jsonify(error = f"The appinfo.json in your pbw file has the following error: {appinfo_valid[1]}", e = "invalid.appinfocontent"), 400
            
            # Check app doesn't already exist
            try:
                if App.query.filter(App.app_uuid == appinfo['uuid']).count() > 0:
                    return jsonify(error = "An app already exists with that UUID", e = "app.exists"), 400
            except Exception as e:
                print(e)
                return jsonify(error = "The UUID provided in appinfo.json is invalid", e = "invalid.uuid"), 400

            #--- Leaving this here because if we don't dynamically created a developer ID when we
            #    submit an app, we will need to do it on the inital /me/developer call, and handle not
            #     having one here.

            # if 'developer_id' in params:
            #     developer = Developer.query.filter(Developer.id == params['developer_id']).one()
            # else:
            #     developer = Developer(id = id_generator.generate(), name = appinfo['companyName'])
            #     db.session.add(developer)
            #     print(f"Created developer {developer.id}")

            # Get developer ID from auth (This is also where we check the user is authenticated)
            result = authed_request('GET', f"{config['REBBLE_AUTH_URL']}/api/v1/me/pebble/appstore")
            if result.status_code != 200:
                abort(401)
            me = result.json()
            developer_id = me['id']
            print("Our developer ID is " + developer_id)

            # Find developer
            developer = Developer.query.filter(Developer.id == developer_id).one()

            # Upload banner if present
            if "banner" in request.files:
                header_asset = upload_asset_from_memory(request.files["banner"], request.files["banner"].content_type)
                # header_asset = "6064be425848b2c6d818149a"
            else:
                header_asset = None

            print(jsonify(screenshots))

            # Copy screenshots to platform map
            if "screenshot-generic-1" in request.files:
                for x in range(6):
                    if f"screenshot-generic-{x}" in request.files:
                        for platform in screenshots:
                            screenshots[platform].append(request.files[f"screenshot-generic-{x}"])
            else:
                for platform in screenshots:
                    for x in range(6):
                        if f"screenshot-{platform}-{x}" in request.files:
                            screenshots[platform].append(request.files[f"screenshot-{platform}-{x}"])

            # Remove any platforms with no screenshots
            clearedScreenshots = dict(screenshots)
            for platform in screenshots:
                if len(clearedScreenshots[platform]) < 1:
                    del clearedScreenshots[platform]
            screenshots = clearedScreenshots

            # Add blanks to optional values
            for x in ["source","website"]:
                if not x in params:
                    params[x] = ""

            # for x in screenshots:
            #     print(f"platform: {x}")
            #     desc = data["description"]
            #     print(f"description: {desc}")
            #     print(f"screenshots:")
            #     for s in screenshots[x]:
            #         print(f"Screenshot: {s}")

            app_obj = App(
                id = id_generator.generate(),
                app_uuid = appinfo['uuid'],
                asset_collections = { x: AssetCollection(
                    platform=x,
                    description=params['description'],
                    screenshots=[upload_asset_from_memory(s, s.content_type) for s in screenshots[x]],
                    headers = [header_asset] if header_asset else [],
                    banner = None
                ) for x in screenshots},
                category_id = category_map[params['category']],
                companions = {}, # companions not supported yet
                created_at = datetime.datetime.utcnow(),
                developer = developer,
                hearts = 0,
                releases = [],
                icon_large = upload_asset_from_memory(request.files['large_icon'], request.files["large_icon"].content_type),
                icon_small = upload_asset_from_memory(request.files['small_icon'], request.files["small_icon"].content_type) if 'small_icon' in params else '',
                #icon_large = "",
                #icon_small = "",
                source = params['source'],
                title = params['title'],
                type = params['type'],
                timeline_enabled = False,
                website = params['website']
            )
            db.session.add(app_obj)
            print(f"Created app {app_obj.id}")

            release = release_from_pbw(app_obj, pbw_file,
                                       release_notes = params['release_notes'],
                                       published_date = datetime.datetime.utcnow(),
                                       version = appinfo['versionLabel'],
                                       compatibility = appinfo.get('targetPlatforms', [ 'aplite', 'basalt', 'diorite', 'emery' ]))
            print(f"Created release {release.id}")
            upload_pbw_from_memory(release, request.files['pbw'])
            db.session.commit()

            return jsonify(success = True, id = app_obj.id)

        else:
            return jsonify(error = requestOK[1], e = requestOK[2]), 400

        # pbw = request.files['pbw']
        # print(pbw.readlines())
        # return "OK"
    except Exception as e:
        # print(e)
        traceback.print_exc()
        print("Oh no")
        abort(500)
        return

@devportal_api.route('/app/<appID>', methods=['POST'])
def update_app_fields(appID):
    # try:

        try:
            req = request.json
        except Exception as e:
            print(e)
            return jsonify(error = "Invalid POST body. Expected JSON", e = "body.invalid"), 400

        if req is None:
            return jsonify(error = "Invalid POST body. Expected JSON", e = "body.invalid"), 400

        allowed_fields_type_map = {
            "title": str,
            "description": str,
            "category": str,
            "website": str,
            "source": str,
            "visible": str
        }

        # Check all passed fields are allowed
        for x in req:
            if not x in allowed_fields_type_map:
                return jsonify(error = f"Illegal field: {x}", e = "illegal.field"), 400

            if not type(x) == allowed_fields_type_map[x]:
                return jsonify(error = f"Invalid value for field '{x}'", e = f"invalid.field.{x}"), 400


        # Check app exists
        app = App.query.filter(App.id == appID)
        if app.count() < 1:
            return jsonify(error = "Unknown app", e = "app.notfound"), 400      
        app = app.one()

        # Check we own the app
        result = authed_request('GET', f"{config['REBBLE_AUTH_URL']}/api/v1/me/pebble/appstore")
        if result.status_code != 200:
            abort(401)
        me = result.json()
        if not me['id'] == app.developer_id:
            return jsonify(error = "You do not have permission to modify that app", e = "permission.denied"), 403

        # Check any enum fields
        if "category" in req and not is_valid_category(req["category"]):
            return jsonify(error = "Invalid value for field: category", e = "invalid.field.category"), 400
        if "visible" in req and not (req["visible"].lower() == "true" or req["visible"].lower() == "false"):
            return jsonify(error = "Invalid value for field: visible", e = "invalid.field.visible"), 400

        # Disallow change face category
        if "category" in req and app.category == "Faces":
            return jsonify(error = "Cannot change category for watchface", e = "disallowed.field.category"), 400

        # Check title length
        if "title" in req and len(req["title"]) > 45:
            return jsonify(error = "Title must be less than 45 characters", e = "invalid.field.title"), 400
            
        # Update the app
        # TODO: Find a way to do this in a loop app[x] doesn't work
        if "title" in req:
            app.title = req["title"]
        if "category" in req:
            app.category = category_map[req["category"]]
        if "website" in req:
            app.website = req["website"]
        if "source" in req:
            app.source = req["source"]
        if "visible" in req:
            # We've already check it's 'true' or 'false'
            if req["visible"].lower() == "true":
                app.visible = True
            else:
                app.visible = False

        # Updating description requires iterating through asset collection
        if "description" in req:
            for x in app.asset_collections:
                app.asset_collections[x].description = req["description"]

        

        db.session.commit()

        return jsonify(success = True, id = app.id)

    # except Exception as e:
    #     print(e)
    #     traceback.print_exc()
    #     print("Oh no")
    #     abort(500)
    #     return

@devportal_api.route('/app/<appID>', methods=['GET'])
def redirect_to_app_api(appID):
    # Get requests on new API should be sent back to existing API
    response = jsonify()
    response.status_code = 302
    response.headers['location'] = '/api/v1/apps/id/' + appID
    response.autocorrect_location_header = False
    return response

def init_app(app, url_prefix='/api/v2'):
    global parent_app
    parent_app = app
    app.register_blueprint(devportal_api, url_prefix=url_prefix)