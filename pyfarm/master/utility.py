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
    from httplib import (
        responses, BAD_REQUEST, INTERNAL_SERVER_ERROR, UNSUPPORTED_MEDIA_TYPE)
except ImportError:
    from http.client import (
        responses, BAD_REQUEST, INTERNAL_SERVER_ERROR, UNSUPPORTED_MEDIA_TYPE)

try:
    from UserDict import UserDict
except ImportError:
    from collections import UserDict

from flask import (
    jsonify as _jsonify, current_app, request, g, abort, render_template)

from pyfarm.core.enums import STRING_TYPES, NOTSET

NONE_TYPE = type(None)


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


def inside_request():
    """Returns True if we're inside a request, False if not."""
    try:
        g.__test_attribute__

    # We're not inside a request context yet and
    # there's nothing logical to return.
    except RuntimeError:
        return False

    # We're inside a request context, this exception is
    # expected because __test_context__ does not in fact
    # exist
    except AttributeError:
        return True

    # For the off chance that someone creates __test_attribute__,
    # let's not allow some really odd behavior to happen as a result.
    else:
        assert False, "g.__test_attribute__ exists now"


def get_g(attribute, instance_types, unset=NOTSET):
    """
    Returns data from :attr:`flask.g` after checking to make sure
    the attribute was set and that it has the correct type.

    This function does not check to see if you're already inside a request.


    :param str attribute:
        The name of the attribute on the :attr:`flask.g` object

    :param tuple instance_types:
        A tuple of classes which the data we're looking
        for should be a part of
    """
    # Either retrieve the value or fail if we can't find it
    try:
        value = getattr(g, attribute)
    except AttributeError:
        g.error = "`g` is lacking the `%s` attribute" % attribute
        abort(INTERNAL_SERVER_ERROR)

    # Our before_request handler *should* set a default value, check
    # that here so
    if value is unset:
        g.error = "`g.%s` has not been set" % attribute
        abort(INTERNAL_SERVER_ERROR)

    # The resulting value should have the correct type
    if not isinstance(value, instance_types):
        g.error = "expected an instance of %s but got %s instead" % (
            g.json.__class__.__name__)
        abort(BAD_REQUEST)

    return value


def validate_mimetypes(flask_request, mimetypes):
    """
    Check to make sure that the request's mimetype is in ``mimetypes``.  If
    this is not true then call :func:`flask.abort` with
    ``UNSUPPORTED_MEDIA_TYPE``

    This function does not check to see if you're already inside a request.
    """
    if flask_request.mimetype not in mimetypes:
        g.error = "Unsupported mimetype, only %s mimetype(s) are " \
                  "supported." % mimetypes
        abort(UNSUPPORTED_MEDIA_TYPE)


def simple_validator(validator,
                     mimetypes=("application/json", ), root_types=(dict, )):
    """
    A decorator, similar to :func:`.validate_with_model`, but greatly
    simplified and more flexible.  Unlike :func:`.validate_with_model` this
    decorator is meant to handle data which may not be structured for a model.

    :param tuple mimetype:
        A tuple of mimetypes that are allowed to be handled by the
        decorated function.

    :param tuple root_types:
        The root type or types which the object on :attr:`flask.g` should
        be an instance of.
    """
    def wrapper(func):

        @wraps(func)
        def wrapped(*args, **kwargs):
            if not inside_request():
                return func(*args, **kwargs)

            validate_mimetypes(request, mimetypes)
            data = get_g("json", dict)
            # TODO: validator is dict - assume values are allowed types
            # and keys are required
            # TODO: validator is voluptuous instance - run it
            # TODO: validator is otherwise callable - run it

            return func(*args, **kwargs)

    return wrapper


def validate_with_model(model, type_checks=None, ignore=None, disallow=None):
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

    :param list ignore:
        A list of fields to completely ignore in the incoming
        request. Typically this is used but ``PUT`` requests or other
        similar requests where part of the data is in the url.

    :param list disallow:
        A list of columns which are never in the request to the decorated
        function
    """
    assert type_checks is None or isinstance(type_checks, dict)
    assert isinstance(ignore, (list, tuple, set, NONE_TYPE))
    assert isinstance(disallow, (list, tuple, set, NONE_TYPE))
    type_checks = type_checks or {}
    ignore = set(ignore or [])
    disallow = set(disallow or [])

    def wrapper(func):

        @wraps(func)
        def wrapped(*args, **kwargs):
            if not inside_request():
                return func(*args, **kwargs)

            validate_mimetypes(request, ["application/json"])

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

            # assert that there's not any disallowed
            # columns in the request
            disallowed_in_request = disallow & request_columns
            if disallowed_in_request:
                g.error = "column(s) not allowed for this " \
                          "request: %s" % disallowed_in_request
                abort(BAD_REQUEST)

            all_valid_keys = types.columns | types.relationships
            unknown_keys = request_columns - all_valid_keys

            # check to see if there are any fields that do not exist
            # in the request
            if unknown_keys:
                g.error = "request contains field(s) that do not exist: " \
                          "%r" % unknown_keys
                abort(BAD_REQUEST)

            # now check to see if we're missing any required fields
            missing_keys = ((types.required - ignore) -
                            request_columns) - types.primary_keys
            if missing_keys:
                g.error = "request is missing field(s): %r" % missing_keys
                abort(BAD_REQUEST)

            # finally make sure that the types included in the request make
            # make sense
            for name, python_types in types.mappings.items():
                if name not in g.json:
                    continue

                value = g.json[name]

                # if there's a custom function to do the type
                # checking then call it here
                if name in type_checks:
                    passed = type_checks[name](value)
                    if passed not in (True, False):
                        g.error = "expected custom type check function for " \
                                  "%r to return True or False" % name
                        abort(INTERNAL_SERVER_ERROR)

                    if not passed:
                        # set the error if the custom function has not
                        if not g.error:
                            g.error = "type check failed for %r" % name

                        abort(BAD_REQUEST)

                elif not isinstance(value, python_types):
                    g.error = "field %r has type %s but we expected " \
                              "type(s) %s" % (name, type(value), python_types)
                    abort(BAD_REQUEST)

            # everything checks out, proceed back to the original function
            return func(*args, **kwargs)
        return wrapped
    return wrapper


def error_handler(e, code=None, default=None, title=None, template=None):
    """
    Constructor for http errors that respects the current mimetype.  By
    default this function returns html however when ``request.mimetype`` is
    ``application/json`` it will return a json response. This function is
    typically used within a :func:`functools.partial` call:

        >>> from functools import partial
        >>> try:
        ...     from httplib import BAD_REQUEST
        ... except ImportError:
        ...     from http.client import BAD_REQUEST
        ...
        >>> from flask import request
        >>> error_400 = partial(
        ...     error_handler, BAD_REQUEST,
        ...     lambda: "bad request to %s" % request.url, "Bad Request")

    :param flask.Response e:
        The response object which will be passed into :func:`.error_handler`,
        this value is ignored by default.

    :param int code:
        The integer to use in the response.  For the most consistent
        results you can use the :mod:`httplib` or :mod:`http.client` modules
        depending on your Python version.

    :type default: str or callable
    :param callable default:
        This will be the default error message if g.error does not
        contain anything.  ``default`` may either be a callable function
        which will produce the string or it may be a string by itself.

    :param str title:
        The HTML title of the request being made.  This is not used when
        dealing with json requests and if not provided at all will default
        to using the official status code's string representation.

    :param str template:
        A alternative template path for HTML responses
    """
    assert isinstance(code, int)
    assert code in responses, "unknown http code %s" % code

    if callable(default):
        default = default()

    if title is None:
        title = responses[code]

    error = g.error or default

    assert isinstance(error, STRING_TYPES)
    assert isinstance(title, STRING_TYPES)

    if request.mimetype == "application/json":
        response = jsonify(error=error)
    else:
        response = render_template(
            template or "pyfarm/error.html", title=title, error=error)

    return response, code
