# No shebang line, this module is meant to be imported
#
# Copyright 2013 Oliver Palmer
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Utility
=======

General utility which are not view or tool specific
"""

import json
from functools import wraps

try:
    from httplib import BAD_REQUEST, INTERNAL_SERVER_ERROR
except ImportError:
    from http.client import BAD_REQUEST, INTERNAL_SERVER_ERROR

try:
    from UserDict import UserDict
except ImportError:
    from collections import UserDict

from flask import jsonify as _jsonify, current_app, request, g, abort
from werkzeug.datastructures import ImmutableDict

from pyfarm.core.enums import APIError, STRING_TYPES, PY3, NOTSET

COLUMN_CACHE = {}


def get_column_sets(model, primary_key=False):
    """
    returns a tuple of two sets containing all columns and required columns
    for the model provided
    """
    all_columns = set()
    required_columns = set()

    for name, column in model.__table__.c.items():
        # skip autoincremented primary keys
        if not primary_key and column.primary_key and column.autoincrement:
            continue

        all_columns.add(name)

        # column is non-nullable without a default
        if not column.nullable and column.default is None:
            required_columns.add(name)

    return all_columns, required_columns


class ReducibleDictionary(dict):
    """
    Adds a :meth:`.reduce` method to :class:`dict` class
    which will remove empty values from a dictionary.

    >>> data = ReducibleDictionary({"foo": True, "bar": None})
    >>> data.reduce()
    >>> print data
    {'foo': True}
    """
    def reduce(self):
        if not PY3:
            items = self.copy().iteritems
        else:
            items = self.copy().items

        for key, value in items():
            if value is None:
                self.pop(key)


class TemplateDictionary(ImmutableDict):
    """
    Simple dictionary which subclasses werkzeug's :class:`.ImmutableDict`
    but allows for new instanced to be produced using :meth:`.__call__`.

    >>> template = TemplateDictionary({"foo": None})
    >>> data = template()
    >>> data["foo"] = True
    >>> data["bar"] = False
    >>> print data, template
    {'foo': True, 'bar': False} TemplateDictionary({'foo': None})

    This class is mainly meant for simple REST responses, other considerations
    should be taken for more complex structures.
    """
    def __call__(self, reducible=True):
        class_ = ReducibleDictionary if reducible else dict
        return class_(self.copy())


def json_from_request(request, all_keys=None, required_keys=None,
                      disallowed_keys=None):
    """
    Returns the json data from the request or a :class:`.JSONResponse` object
    on failure.

    :keyword set all_keys:
        a set of all possible keys which may be present in the json request

    :keyword set required_keys:
        a set of keys which must be present in the json request

    :keyword set disallowed_keys:
        a set of keys which cannot be part of the request
    """
    try:
        data = request.get_json()

        # though unlikely it's possible to_json didn't fully resolve
        # the data
        if isinstance(data, STRING_TYPES):
            try:
                data = json.loads(data)
            except ValueError:  # it's also possible this was not json data
                pass

    except ValueError as e:
        errorno, msg = APIError.JSON_DECODE_FAILED
        msg += ": %s" % e
        return jsonify(errorno=errorno, message=msg), BAD_REQUEST

    if isinstance(data, dict) and "id" not in data and \
            (all_keys or required_keys or disallowed_keys):
        request_keys = set(data)

        # make sure that we don't have more request keys
        # than there are total keys
        if all_keys is not None and not request_keys.issubset(all_keys):
            errorno, msg = APIError.EXTRA_FIELDS_ERROR
            msg += ".  Extra fields were: %s" % list(request_keys - all_keys)
            return jsonify(errorno=errorno, message=msg), BAD_REQUEST

        # if required keys were provided, make sure the
        # request has at least those fields
        if required_keys is not None and not \
            request_keys.issuperset(required_keys):
            missing_keys = list(required_keys-request_keys)
            errorno, msg = APIError.MISSING_FIELDS
            msg += ".  Missing fields are: %s" % missing_keys
            return jsonify(errorno=errorno, message=msg), BAD_REQUEST

        if disallowed_keys is not None and \
            request_keys.issuperset(disallowed_keys):
            errorno, msg = APIError.EXTRA_FIELDS_ERROR
            disallowed_extras = list(request_keys.intersection(disallowed_keys))
            msg += ".  Extra fields were: %s" % disallowed_extras
            return jsonify(errorno=errorno, message=msg), BAD_REQUEST

    return data


def jsonify(*args, **kwargs):
    """
    Drop in replacement for :func:`flask.jsonify` that also handles list
    objects.  Flask does not support this by default because it's considered
    a security risk in most cases but we do need it in certain cases.
    """
    # Single argument that's not a dictionary?  Handle it ourselves just
    # like Flask would have.
    if len(args) == 1 and not isinstance(args[0], (dict, UserDict)):
        indent = None
        if current_app.config["JSONIFY_PRETTYPRINT_REGULAR"] \
                and not request.is_xhr:
            indent = 2

        return current_app.response_class(
            json.dumps(args[0], indent=indent),
            mimetype="application/json")

    else:
        return _jsonify(*args, **kwargs)


def validate_with_model(model, type_checks=None):
    """
    Decorator which will check the contents of the of the json
    request against a model for:

        * missing fields which are required
        * values which don't match their type(s) in the database
        * inclusion of fields which do not exist

    :param model:
        The model object that the decorated endpoint should use for testing
        the points above.

    :param dict type_checks:
        A dictionary containing a mapping of column names to
        special functions used for checking.  If there's a key in the
        incoming request that needs a more detailed check than
        "isinstance(g.json[column_name], <Python type(s) from sql>)" then
        this is the place to add it.
    """
    assert type_checks is None or isinstance(type_checks, dict)
    type_checks = type_checks or {}

    def wrapper(func):

        @wraps(func)
        def wrapped(*args, **kwargs):
            try:
                # special case where the decorator is being
                # called before any requests have been made
                if not hasattr(g, "json"):
                    pass

                # g.json should be set by our before_request handler
                # if not then that's an error
                elif g.json is NOTSET:
                    g.error = "expected g.json to be set"
                    abort(INTERNAL_SERVER_ERROR)

                # in all other cases g.json should be a dictionary
                elif not isinstance(g.json, dict):
                    g.error = "dictionary expected but got %s instead" % (
                        g.json.__class__.__name__)
                    abort(BAD_REQUEST)

            except RuntimeError:  # outside of a request context
                pass

            types = model.types()
            request_columns = set(g.json)
            all_valid_keys = types.columns | types.relationships

            # check to see if there are any fields that do not exist
            # in the request
            unknown_keys = request_columns - all_valid_keys
            if unknown_keys:
                g.error = "request contains field(s) that do not exist " \
                          "in %s: %r" % (model.__table__, unknown_keys)
                abort(BAD_REQUEST)

            # now check to see if we're missing any required fields
            missing_keys = (
               types.required - request_columns) - types.primary_keys
            if missing_keys:
                g.error = "request to %s is missing field(s): %r" % (
                    model.__table__, missing_keys)
                abort(BAD_REQUEST)

            # finally make sure that the types included in the request make
            # make sense
            for name, python_types in types.mappings.items():
                # if there's a custom function to do the type
                # checking then call it here
                if name in type_checks:
                    passed = type_checks[name]
                    if passed not in (True, False):
                        g.error = "expected custom type check function for " \
                                  "%r to return True or False" % name
                        abort(INTERNAL_SERVER_ERROR)

                    if not passed:
                        # set the error if the custom function has not
                        if not g.error:
                            g.error = "type check failed for %r" % name

                        abort(BAD_REQUEST)

                elif name in g.json:
                    value = g.json[name]
                    if not isinstance(value, python_types):
                        g.error = "field %r has type %s but we expected " \
                                  "type(s) %s" % (name, type(value),
                                                  python_types)
                        abort(BAD_REQUEST)

            # everything checks out, proceed back to the original function
            return func(*args, **kwargs)
        return wrapped
    return wrapper
