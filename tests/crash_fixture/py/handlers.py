"""CRASH-CHECK fixture (tests/crash_cases.py): three planted crashes, one fail-closed handler, one private helper.
Do not fix: the tests depend on these bugs. No framework needed."""
import json


def create_order(request):  # planted 1: a missing field -> KeyError
    body = json.loads(request.body)
    qty = body['item']['quantity']
    return {'status': 201, 'qty': qty}


def handle_profile(payload):  # planted 2: a null deref -> TypeError ('NoneType' object is not subscriptable)
    if 'user' not in payload:
        return {'status': 400}
    return {'status': 200, 'n': len(payload['user']['name'])}


def lambda_handler(event, context):  # planted 3: json.loads on a value that is not text -> TypeError
    body = json.loads(event['body'])
    return {'statusCode': 200, 'body': body.get('type') if isinstance(body, dict) else None}


def update_limit(request):  # negative control: checks everything first, answers 400, never raises
    try:
        b = json.loads(request.body or b'null')
    except (ValueError, UnicodeDecodeError):
        return {'status': 400}
    if not isinstance(b, dict) or type(b.get('limit')) is not int or not isinstance(b.get('name'), str):
        return {'status': 400}
    return {'status': 200, 'limit': b['limit'], 'name': _normalize(b['name'])}


def _normalize(s):  # negative control: a private helper that raises on None; only update_limit calls it
    return s.strip().lower()
