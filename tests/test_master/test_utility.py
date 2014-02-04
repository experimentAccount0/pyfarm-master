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

from json import dumps
from functools import partial

try:
    from httplib import OK, BAD_REQUEST
except ImportError:
    from http.client import OK, BAD_REQUEST

from flask import g
from werkzeug.exceptions import InternalServerError, BadRequest
from werkzeug.datastructures import ImmutableDict

# test class must be loaded first
from pyfarm.master.testutil import BaseTestCase
BaseTestCase.build_environment()

from pyfarm.core.enums import NOTSET
from pyfarm.models.core.mixins import UtilityMixins
from pyfarm.master.utility import (
    ReducibleDictionary, TemplateDictionary, validate_with_model)
from pyfarm.master.entrypoints.main import load_error_handlers
from pyfarm.master.application import db
from pyfarm.models.core.cfg import TABLE_PREFIX


class ColumnSetTest(db.Model):
    __tablename__ = "%s_column_set_test" % TABLE_PREFIX
    a = db.Column(db.Integer, primary_key=True)
    b = db.Column(db.Integer, nullable=True)
    c = db.Column(db.Integer, nullable=False)
    d = db.Column(db.Integer, nullable=False, default=0)


class ValidationTestModel(db.Model, UtilityMixins):
    __tablename__ = "%s_validate_with_model_test" % TABLE_PREFIX
    id = db.Column(db.Integer, autoincrement=True, primary_key=True)
    a = db.Column(db.Integer, nullable=False)
    b = db.Column(db.Integer, nullable=False, default=1)


class FakeRequest(object):
    def __init__(self, data, raise_error=False):
        self.data = data
        self.raise_error = raise_error

    def get_json(self):
        if self.raise_error:
            raise ValueError("FAILED")
        return self.data


class TestUtility(BaseTestCase):
    def setUp(self):
        super(TestUtility, self).setUp()
        g.error = NOTSET
        g.json = NOTSET

    def test_reducible_dictionary(self):
        source = {"a": None}
        data = ReducibleDictionary(source)
        self.assertTrue(issubclass(ReducibleDictionary, dict))
        self.assertDictEqual(data, source)
        data.reduce()
        self.assertDictEqual(data, {})
        self.assertDictEqual(source, {"a": None})

    def test_template_dictionary(self):
        template = TemplateDictionary()
        self.assertIsInstance(template, ImmutableDict)
        self.assertIsInstance(template(), ReducibleDictionary)
        self.assertIsInstance(template(reducible=False), dict)


class TestValidateWithModel(BaseTestCase):
    def setup_app(self):
        super(TestValidateWithModel, self).setup_app()
        load_error_handlers(self.app)

    def setUp(self):
        super(TestValidateWithModel, self).setUp()
        self.post = partial(
            self.client.post, headers={"Content-Type": "application/json"})

    def add_route(self, function, methods=None):
        assert callable(function)

        @self.app.route("/", methods=methods or ("POST", ))
        def view():
            return function()

    def test_no_data_to_decode(self):
        @validate_with_model(ValidationTestModel)
        def test():
            return ""

        self.add_route(test)
        response = self.post("/")
        self.assertIn("error", response.json)
        self.assertEqual(response.json["error"], "no data to decode")

    def test_invalid_type(self):
        @validate_with_model(ValidationTestModel)
        def test():
            return ""

        self.add_route(test)
        response = self.post("/", data=dumps(["foo", "bar"]))
        self.assertIn("error", response.json)
        self.assertEqual(
            response.json["error"], "dictionary expected but got list instead")

    def test_fields_dont_exist(self):
        @validate_with_model(ValidationTestModel)
        def test():
            return ""

        self.add_route(test)
        response = self.post("/", data=dumps({"foobar": True}))
        self.assertIn("error", response.json)
        error_message = "request contains field(s) that do not exist " \
                        "in %s: {'foobar'}" % ValidationTestModel.__tablename__
        self.assertEqual(response.json["error"], error_message)

    def test_missing_required_fields(self):
        @validate_with_model(ValidationTestModel)
        def test():
            return ""

        self.add_route(test)
        response = self.post("/", data=dumps({"b": 1}))
        self.assertIn("error", response.json)
        error_message = "request to %s is missing " \
                        "field(s): {'a'}" % ValidationTestModel.__tablename__
        self.assertEqual(response.json["error"], error_message)

    def test_type_check(self):
        @validate_with_model(ValidationTestModel)
        def test():
            return ""

        self.add_route(test)
        response = self.post("/", data=dumps({"a": "foobar"}))
        self.assertIn("error", response.json)
        error_message = "field 'a' has type <class 'str'> but we " \
                        "expected type(s) <class 'int'>"
        self.assertEqual(response.json["error"], error_message)

    def test_custom_type_check_false(self):
        with self.assertRaises(AssertionError):
            @validate_with_model(ValidationTestModel, type_checks={"a"})
            def test():
                return ""

        def a_type_check(value):
            return False

        @validate_with_model(
            ValidationTestModel, type_checks={"a": a_type_check})
        def test():
            return ""

        self.add_route(test)
        response = self.post("/", data=dumps({"a": 1}))
        self.assert_bad_request(response)

    def test_custom_type_check_true(self):
        with self.assertRaises(AssertionError):
            @validate_with_model(ValidationTestModel, type_checks={"a"})
            def test():
                return ""

        def a_type_check(value):
            return True

        @validate_with_model(
            ValidationTestModel, type_checks={"a": a_type_check})
        def test():
            return ""

        self.add_route(test)
        response = self.post("/", data=dumps({"a": 1}))
        self.assert_ok(response)

    def test_custom_type_check_with_custom_error(self):
        with self.assertRaises(AssertionError):
            @validate_with_model(ValidationTestModel, type_checks={"a"})
            def test():
                return ""

        def a_type_check(value):
            g.error = "bad"
            return False

        @validate_with_model(
            ValidationTestModel, type_checks={"a": a_type_check})
        def test():
            return ""

        self.add_route(test)
        response = self.post("/", data=dumps({"a": 1}))
        self.assert_bad_request(response)
        self.assertEqual(response.json, {"error": "bad"})

    def test_custom_type_check_invalid_output(self):
        with self.assertRaises(AssertionError):
            @validate_with_model(ValidationTestModel, type_checks=["a"])
            def test():
                return ""

        def a_type_check(value):
            return None

        @validate_with_model(
            ValidationTestModel, type_checks={"a": a_type_check})
        def test():
            return ""

        self.add_route(test)
        response = self.post("/", data=dumps({"a": 1}))
        self.assert_internal_server_error(response)
        self.assertEqual(
            response.json,
            {"error": "expected custom type check function for "
                      "'a' to return True or False"})
